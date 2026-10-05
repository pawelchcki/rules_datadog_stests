"""Runtime-verify llm_observability_datasets with the pinned ddtrace 4.14 SDK and a real trace-agent.

Upstream: tests/parametric/test_llm_observability/test_llm_observability_dne.py::Test_Dataset::test_dataset_create_delete.
The pinned SDK's experiments client is hardcoded agentless in ddtrace 4.14, so the workload
routes it through the real agent EVP proxy via DD_LLMOBS_OVERRIDE_ORIGIN plus the private
agentless flag flip it records in the identity file; this probe answers the SDK's
/api/unstable/llm-obs/v1 dataset API on the loopback backend and asserts both wire boundaries.
"""
import json
import select
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from harness.datadog_agent.probe import AGENT_VERSION, server_thread
from harness.datadog_llmobs import connect
from harness.datadog_integrations import datasets_assertions
from harness.datadog_integrations.lab import (AgentLab, attach_artifacts, base_parser,
    output_dir, receipt, resolve_args, write_results)
from harness.datadog_telemetry.probe import TelemetryBackendHandler, sha
from harness.datadog_telemetry.proxy import CaptureProxy

CAPABILITY = "llm_observability_datasets"
SOURCE = ("https://github.com/DataDog/system-tests/blob/" + "098fe0967c587db8a16b74a1e711777d0a9d5867"
          + "/tests/parametric/test_llm_observability/test_llm_observability_dne.py#L36")
SOURCE_HASH = "17a3993a4eb55d1b8c86a60adcb1eee828c1af16a404e181b54695052635f869"
RESULTS = "datadog-integrations-datasets-results.json"
MAX_BODY = 32 * 1024 * 1024


class DatasetsBackendHandler(TelemetryBackendHandler):
    """Answers the pinned SDK's llm-obs DNE API on top of the shared fake intake.

    Responses carry the shapes ddtrace 4.14 parses: project create returns
    data.id, dataset create returns data.id plus data.attributes.current_version,
    delete only needs a 200. Everything else delegates to the stock intake.
    """

    PROJECTS_PATH = "/api/unstable/llm-obs/v1/projects"
    DATASET_DELETE_PATH = "/api/unstable/llm-obs/v1/datasets/delete"

    def do_POST(self):
        path = urlsplit(self.path).path
        if path in (self.PROJECTS_PATH, self.DATASET_DELETE_PATH) or datasets_assertions.DATASET_CREATE_PATH.match(path):
            self._datasets_post(path)
        else:
            super().do_POST()

    def _datasets_post(self, path):
        headers = {key.lower(): value for key, value in self.headers.items()}
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or "transfer-encoding" in headers:
                raise ValueError("one Content-Length and no Transfer-Encoding required")
            length = int(lengths[0])
            if length < 0 or length > MAX_BODY:
                raise ValueError("request exceeds size limit")
        except ValueError as exc:
            self.close_connection = True
            self.respond(400, {"error": str(exc)})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self.respond(400, {"error": "truncated request"})
            return
        status, payload, error, response = 200, None, None, {}
        if not self.authenticated():
            status, error = 403, "invalid API key"
        else:
            try:
                document = json.loads(body)
                payload = document
                response = self._datasets_response(path, document)
            except (ValueError, KeyError, TypeError) as exc:
                status, error = 400, str(exc)
        self.server.capture(path, headers, body, status, payload, error)
        self.respond(status, {"error": error} if error else response)

    def _datasets_response(self, path, document):
        attributes = document["data"]["attributes"]
        if path == self.PROJECTS_PATH:
            project_id = "dne-project-%06d" % (len(self.server.records) + 1)
            return {"data": {"id": project_id, "type": "projects",
                             "attributes": {"name": attributes["name"], "description": attributes.get("description", "")}}}
        if path == self.DATASET_DELETE_PATH:
            return {"data": {"id": None, "type": "datasets",
                             "attributes": {"type": attributes["type"], "dataset_ids": attributes["dataset_ids"]}}}
        dataset_id = "dne-dataset-%06d" % (len(self.server.records) + 1)
        return {"data": {"id": dataset_id, "type": "datasets",
                         "attributes": {"name": attributes["name"], "description": attributes.get("description", ""),
                                        "current_version": 1}}}


class DatasetsRelayHandler(connect.Handler):
    """CONNECT relay that also lets the agent reach the synthetic api subdomain."""

    def do_CONNECT(self):
        with self.server.lock:
            self.server.records.append(self.path)
        allowed = {"api.backend.test:443", "llmobs-intake.backend.test:443",
                   "127.0.0.1:" + str(self.server.backend_port)}
        if self.path not in allowed:
            self.send_error(403, "only the synthetic LLMObs intake is allowed")
            return
        with socket.create_connection(("127.0.0.1", self.server.backend_port), timeout=10) as upstream:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            peers = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(peers, [], [], 20)
                if not ready:
                    return
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is self.connection else self.connection).sendall(chunk)


class DatasetsRelay(connect.ConnectRelay):
    def __init__(self, backend_port):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), DatasetsRelayHandler)
        self.backend_port = backend_port
        self.records = []
        self.lock = threading.Lock()


def _with_bodies(records, directory):
    requests = []
    for record in records:
        body = json.loads((Path(directory) / record["raw_file"]).read_bytes())
        # The pinned SDK sends absolute-form request lines through the override
        # origin, so the capture path can include scheme and authority.
        path = urlsplit(record["path"]).path
        requests.append({"path": path, "status": record["status"], "headers": record["headers"],
                         "raw_sha256": record["raw_sha256"], "body": body})
    return requests


def execute(args, out):
    lab = AgentLab(args, out, hostname="integrations-lab")
    lab.backend.RequestHandlerClass = DatasetsBackendHandler
    lab.relay = DatasetsRelay(lab.backend.server_port)
    lab.config["proxy"]["https"] = "http://127.0.0.1:" + str(lab.relay.server_port)
    # The pinned loopback certificate only carries the llmobs-intake.backend.test
    # SAN, while the dataset API rides the agent EVP proxy's "api" subdomain.
    lab.config["skip_ssl_validation"] = True
    (out / "datadog.yaml").write_text(json.dumps(lab.config, indent=2) + "\n")
    results = []
    try:
        with lab:
            info = json.loads((out / "agent-info.json").read_text())
            assert info["version"] == AGENT_VERSION and "/evp_proxy/v2/" in info["endpoints"], info
            case = out / "datasets"
            sdk_dir = case / "sdk"
            sdk_dir.mkdir(parents=True, exist_ok=True)
            proxy = CaptureProxy(lab.agent_url, sdk_dir)
            with server_thread(proxy):
                env_extra = {"DD_LLMOBS_AGENTLESS_ENABLED": "false", "DD_LLMOBS_ML_APP": "integrations-lab",
                             "DD_API_KEY": "00000000000000000000000000000002",
                             "DD_APP_KEY": "00000000000000000000000000000003",
                             "DD_LLMOBS_OVERRIDE_ORIGIN": "http://127.0.0.1:" + str(proxy.server_port) + "/evp_proxy/v2"}
                app_args = [args.app, "--identity-file", str(case / "identity.json")]
                tracer_records, env = lab.run_workload(case, app_args, env_extra, "datadog-datasets-lab")
            assert all(record["status"] == 200 for record in tracer_records), tracer_records
            sdk_records = proxy.snapshot()
            identity = json.loads((case / "identity.json").read_text())
            (case / "requested-environment.json").write_text(json.dumps(env, indent=2) + "\n")

            result = receipt(CAPABILITY, [CAPABILITY], env, "datasets/tracer/requests.json",
                             sha((case / "tracer" / "requests.json").read_bytes()), out,
                             clientVersion=identity["tracer_version"], source=SOURCE,
                             sourceMethod="Test_Dataset.test_dataset_create_delete", sourceSha256=SOURCE_HASH,
                             workloadSha256=sha(Path(args.app).read_bytes()),
                             missingAssertions=[{"name": "vcr_replay_of_real_datadog_api", "status": "missing",
                                 "reason": "upstream replays recorded cassettes of the real llm-obs API; this lab "
                                           "substitutes a loopback fake backend behind the real trace-agent EVP proxy"}])
            attach_artifacts(result, out, ["datasets/identity.json", "datasets/requested-environment.json",
                                           "datasets/app.log", "datasets/sdk/requests.json",
                                           "datadog.yaml", "agent-info.json"])
            result["artifacts"].extend({"file": "datasets/sdk/" + record["raw_file"], "sha256": record["raw_sha256"]}
                                       for record in sdk_records)
            results.append(result)
            try:
                datasets_assertions.check(identity, _with_bodies(sdk_records, sdk_dir), lab.backend.snapshot(),
                                          lab.relay.records, AGENT_VERSION, lab.hostname, lab.hostname + "-env")
                result["status"] = "passed"
                print(CAPABILITY, "passed", flush=True)
            except Exception as error:
                result["detail"] = repr(error)
                raise
    finally:
        backend_records = lab.backend.snapshot()
        backend_bytes = json.dumps(backend_records, indent=2).encode() + b"\n"
        (out / "backend-capture.json").write_bytes(backend_bytes)
        (out / "connect-relay.json").write_text(json.dumps(lab.relay.records, indent=2) + "\n")
        for result in results:
            result["backendCaptureSha256"] = sha(backend_bytes)
            result["artifacts"].append({"file": "backend-capture.json", "sha256": sha(backend_bytes)})
            result["artifacts"].append({"file": "connect-relay.json", "sha256": sha((out / "connect-relay.json").read_bytes())})
            result["artifacts"].extend({"file": "backend/" + record["raw_file"], "sha256": record["raw_sha256"]}
                                       for record in backend_records)
        write_results(out, RESULTS, results)


def main():
    if not __debug__:
        raise RuntimeError("Dataset assertions require Python optimization disabled")
    args = resolve_args(base_parser().parse_args())
    execute(args, output_dir())


if __name__ == "__main__":
    main()
