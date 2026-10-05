"""Runtime-verify genuine GraphQL error reporting with success controls and real-Agent spans.

GraphQL error tracking is unsupported by the pinned Python SDK and is not claimed.
"""
import json
import os
from pathlib import Path
import time
from urllib.request import Request
from urllib.request import urlopen

from harness.datadog_agent.probe import BASE_ENV, process, server_thread, wait_ready
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_integrations import graphql_assertions
from harness.datadog_integrations.lab import (AgentLab, REVISION, TRACER_SOURCE, attach_artifacts, base_parser,
                                              output_dir, receipt, resolve_args, write_results)
from harness.datadog_telemetry.probe import sha
from harness.datadog_telemetry.proxy import CaptureProxy

SOURCE = "https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/test_graphql.py"
ERROR_DOCUMENT = {"query": graphql_assertions.ERROR_QUERY, "operationName": "myQuery"}
SUCCESS_DOCUMENT = {"query": graphql_assertions.SUCCESS_QUERY, "operationName": "myQuery"}
EXTENSIONS_ENV = "int,float,str,bool,other"
CASES = [
    {"name": "reporting", "capability": "graphql_operation_error_reporting", "expect_event": True,
     "env_extra": {"DD_TRACE_GRAPHQL_ERROR_EXTENSIONS": EXTENSIONS_ENV}, "missing": []},
]


def post(url, document):
    request = Request(url, data=json.dumps(document).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=10) as response:
        assert response.status == 200, response.status
        return json.loads(response.read())


def wait_backend_spans(lab, span_ids, deadline_s=15):
    """Poll the real Agent's backend exports until every wanted span is delivered."""
    deadline = time.monotonic() + deadline_s
    spans = []
    while time.monotonic() < deadline:
        spans = [span for record in lab.backend.snapshot() if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                 for chunk in trace_chunks(record["payload"]) for span in chunk["spans"]]
        if span_ids <= {span["span_id"] for span in spans}:
            return [span for span in spans if span["span_id"] in span_ids]
        time.sleep(0.1)
    raise AssertionError("backend spans incomplete: " + repr(spans))


def run_case(lab, args, out, case):
    """Launch the workload once, replay the upstream requests, and return evidence."""
    name = case["name"]
    case_dir = out / name
    case_dir.mkdir(parents=True, exist_ok=True)
    proxy = CaptureProxy(lab.agent_url, case_dir / "tracer")
    ready = case_dir / "ready.port"
    identity_path = case_dir / "identity.json"
    ready.unlink(missing_ok=True)
    identity_path.unlink(missing_ok=True)
    with server_thread(proxy):
        env = dict(BASE_ENV)
        env.update({"DD_SERVICE": "graphql-lab", "DD_ENV": "graphql-env", "DD_VERSION": "graphql-version",
                    "DD_TRACE_API_VERSION": "v0.4"})
        env.update(case["env_extra"])
        env["DD_TRACE_AGENT_URL"] = "http://127.0.0.1:" + str(proxy.server_port)
        launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
        launch += ["--instance=datadog-graphql-" + name]
        launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        launch += ["--", args.app, "--ready-file", str(ready), "--identity-file", str(identity_path),
                   "--sdk-overlay", args.sdk_overlay, "--requests", "4"]
        clean_env = {key: value for key, value in os.environ.items()
                     if not key.startswith(("DD_", "OTEL_", "_DD_")) and key != "PYTHONOPTIMIZE"}
        with process(launch, case_dir / "app.log", clean_env) as proc:
            deadline = time.monotonic() + 60
            while not ready.exists():
                assert proc.poll() is None and time.monotonic() < deadline, (case_dir / "app.log").read_text(errors="replace")
                time.sleep(0.1)
            app_url = "http://127.0.0.1:" + ready.read_text().strip()
            wait_ready(proc, app_url + "/healthz", case_dir / "app.log")
            for document in (ERROR_DOCUMENT, SUCCESS_DOCUMENT, ERROR_DOCUMENT, SUCCESS_DOCUMENT):
                payload = post(app_url + "/graphql", document)
                if document is ERROR_DOCUMENT:
                    assert payload["data"] == {"withError": None} and payload["errors"], payload
                else:
                    assert payload["data"] == {"hello": "Hello world"} and "errors" not in payload, payload
            assert proc.wait(timeout=30) == 0, (case_dir / "app.log").read_text(errors="replace")
    records = proxy.snapshot()
    assert records and all(record["status"] == 200 for record in records), records
    native_spans = []
    for record in records:
        if record["path"] == "/v0.4/traces":
            raw = (case_dir / "tracer" / record["raw_file"]).read_bytes()
            assert sha(raw) == record["raw_sha256"]
            native_spans.extend(span for trace in msgpack(raw) for span in trace)
    identity = json.loads(identity_path.read_text())
    execute_ids = {span["span_id"] for span in native_spans if span["name"] == "graphql.execute"}
    assert len(execute_ids) == 4, native_spans
    backend_spans = wait_backend_spans(lab, execute_ids)
    (case_dir / "native-spans.json").write_text(json.dumps(native_spans, indent=2) + "\n")
    (case_dir / "events.json").write_text(json.dumps({"native": native_spans, "backend": backend_spans}, indent=2) + "\n")
    return env, identity, records, native_spans, backend_spans


def execute(args, out):
    results = []
    with AgentLab(args, out, hostname="graphql-lab") as lab:
        try:
            for case in CASES:
                name = case["name"]
                env, identity, records, native_spans, backend_spans = run_case(lab, args, out, case)
                result = receipt("graphql-" + name, [case["capability"]], env, name + "/tracer/requests.json",
                                 sha((out / name / "tracer" / "requests.json").read_bytes()), out,
                                 clientVersion=identity["tracer_version"], sourceSha256=TRACER_SOURCE,
                                 workloadSha256=sha(Path(args.app).read_bytes()), source=SOURCE,
                                 missingAssertions=case["missing"])
                attach_artifacts(result, out, [name + "/identity.json", name + "/native-spans.json", name + "/events.json",
                                               name + "/app.log", "datadog.yaml", "agent-info.json"])
                result["artifacts"].extend({"file": name + "/tracer/" + record["raw_file"], "sha256": record["raw_sha256"]}
                                           for record in records)
                results.append(result)
                try:
                    graphql_assertions.check_case(native_spans, backend_spans, graphql_assertions.REPORTING
                                                  if case["expect_event"] else graphql_assertions.TRACKING,
                                                  case["expect_event"], name)
                    result["status"] = "passed"
                    print(result["name"], "passed", flush=True)
                except Exception as error:
                    result["detail"] = repr(error)
                    raise
        finally:
            (out / "backend-capture.json").write_text(json.dumps(lab.backend.snapshot(), indent=2) + "\n")
            for result in results:
                attach_artifacts(result, out, ["backend-capture.json"])
                result["artifacts"].extend({"file": "backend/" + record["raw_file"], "sha256": record["raw_sha256"]}
                                           for record in lab.backend.snapshot())
            write_results(out, "datadog-graphql-results.json", results)
    assert len(results) == len(CASES) and all(result["status"] == "passed" for result in results), results


def main():
    if not __debug__:
        raise RuntimeError("GraphQL assertions require Python optimization disabled")
    execute(resolve_args(base_parser().parse_args()), output_dir())


if __name__ == "__main__":
    main()
