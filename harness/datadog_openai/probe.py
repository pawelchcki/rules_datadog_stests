"""Observe official OpenAI client instrumentation across both real Agent paths."""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import time

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer, decompress
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_llmobs.connect import ConnectRelay
from harness.datadog_telemetry.probe import TelemetryBackendHandler, sha
from harness.datadog_telemetry.proxy import CaptureProxy
from harness.datadog_openai import assertions, fake_api

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
SDK_PATH = "/evp_proxy/v2/api/v2/llmobs"


def run_workload(args, agent_url, out, mode, api_url):
    out.mkdir(parents=True, exist_ok=True)
    proxy = CaptureProxy(agent_url, out / "tracer")
    with server_thread(proxy):
        env = dict(BASE_ENV)
        env.update({"DD_TRACE_AGENT_URL": "http://127.0.0.1:" + str(proxy.server_port), "DD_SERVICE": "openai-lab",
                    "DD_ENV": "openai-env", "DD_VERSION": "openai-version", "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]',
                    "DD_LLMOBS_AGENTLESS_ENABLED": "false", "DD_APM_TRACING_ENABLED": str(mode == "apm").lower(),
                    "_DD_LLMOBS_WRITER_INTERVAL": "3600", "DD_TRACE_API_VERSION": "v0.4"})
        launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
        launch += ["--instance=datadog-openai-" + mode]
        launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        launch += ["--", args.app, "--identity-file", str(out / "identity.json"), "--sdk-overlay", args.sdk_overlay,
                   "--api-url", api_url, "--mode", mode]
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_")) and key != "PYTHONOPTIMIZE"}
        with process(launch, out / "app.log", clean_env) as proc:
            assert proc.wait(timeout=40) == 0, (out / "app.log").read_text(errors="replace")
        return json.loads((out / "identity.json").read_text()), proxy.snapshot(), env


def execute(args, out):
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = TelemetryBackendHandler
    certificate = Path(__file__).parent.parent / "datadog_llmobs/localhost-test.crt"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, certificate.with_suffix(".key"))
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    relay = ConnectRelay(backend.server_port)
    api = fake_api.server(out / "api")
    results = []
    with server_thread(backend), server_thread(relay), server_thread(api):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        backend_url = "https://127.0.0.1:" + str(backend.server_port)
        config = {"api_key": API_KEY, "hostname": "openai-agent", "env": "openai-agent-env", "dd_url": backend_url,
            "log_level": "info", "log_to_console": True, "log_file": "", "cmd_port": 0,
            "proxy": {"https": "http://127.0.0.1:" + str(relay.server_port)},
            "evp_proxy_config": {"enabled": True, "dd_url": "backend.test"},
            "remote_configuration": {"enabled": False}, "agent_telemetry": {"enabled": False},
            "apm_config": {"enabled": True, "receiver_port": port, "receiver_socket": "", "log_file": "", "debug": {"port": 0},
                "apm_non_local_traffic": False, "apm_dd_url": backend_url, "telemetry": {"enabled": False},
                "trace_writer": {"flush_period_seconds": 0.2}, "max_memory": 0, "max_cpu_percent": 0}}
        (out / "datadog.yaml").write_text(json.dumps(config, indent=2) + "\n")
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_"))}
        clean_env["SSL_CERT_FILE"] = str(certificate)
        with process(agent_command(args.agent_rootfs) + ["run", "--config=" + str(out / "datadog.yaml")], out / "agent.log", clean_env) as proc:
            agent_url = "http://127.0.0.1:" + str(port)
            info = wait_ready(proc, agent_url + "/info", out / "agent.log")
            assert info["version"] == AGENT_VERSION
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            try:
                for mode in ("apm", "llmobs"):
                    case_dir = out / mode
                    first_api = len(api.snapshot())
                    identity, tracer_records, env = run_workload(args, agent_url, case_dir, mode, "http://127.0.0.1:" + str(api.server_port))
                    api_records = api.snapshot()[first_api:]
                    assertions.check_client(identity, api_records)
                    assert all(record["status"] == 200 for record in tracer_records), tracer_records
                    spans = []
                    native_spans = []
                    if mode == "apm":
                        for request in tracer_records:
                            if request["path"] == "/v0.4/traces":
                                raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
                                assert sha(raw) == request["raw_sha256"]
                                native_spans.extend(span for trace in msgpack(raw) for span in trace)
                        assert len([s for s in native_spans if s["name"] == "openai.request"]) == 8
                        (case_dir / "native-spans.json").write_text(json.dumps(native_spans, indent=2) + "\n")
                        deadline = time.monotonic() + 12
                        while time.monotonic() < deadline:
                            spans = [span for record in backend.snapshot() if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                                     for chunk in trace_chunks(record["payload"]) for span in chunk["spans"]]
                            if len([s for s in spans if s["name"] == "openai.request"]) == 8:
                                break
                            time.sleep(0.1)
                    else:
                        for request in [r for r in tracer_records if r["path"] == SDK_PATH]:
                            matches = [r for r in backend.snapshot() if r["path"] == "/api/v2/llmobs" and r["raw_sha256"] == request["raw_sha256"]]
                            assert len(matches) == 1 and matches[0]["status"] == 200, matches
                            forwarded = matches[0]
                            assert forwarded["headers"]["via"] == "trace-agent " + AGENT_VERSION
                            assert forwarded["headers"]["x-datadog-hostname"] == "openai-agent"
                            assert forwarded["headers"]["x-datadog-agentdefaultenv"] == "openai-agent-env"
                            raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
                            assert raw == (out / "backend" / forwarded["raw_file"]).read_bytes()
                            document = json.loads(decompress(raw, request["headers"].get("content-encoding", "")))
                            assert document == forwarded["payload"]
                            spans.extend(span for batch in document for span in batch["spans"])
                    (case_dir / "events.json").write_text(json.dumps(spans, indent=2) + "\n")
                    for feature, check in assertions.CHECKS[mode]:
                        receipt = {"name": mode + ":" + feature, "capabilityNames": [feature], "capabilityInventoryRevision": REVISION,
                            "status": "failed", "configuration": env, "captureFile": mode + "/tracer/requests.json",
                            "captureSha256": sha((case_dir / "tracer/requests.json").read_bytes()), "agentVersion": AGENT_VERSION,
                            "clientVersion": identity["client_version"],
                            "sourceSha256": "4d55cfcd058c01a19555fc3f5eb2ad298e17e50ffe2bcacbe255ca825868bae1" if mode == "apm" else "3a93b9d275f73cbf316237fb0eef6c0708e3fb243c2906315f49076688415a02",
                            "missingAssertions": (["streaming", "tool calls", "text error outputs differ from upstream: pinned Python emits an empty message placeholder"] if feature == "llm_observability_openai_llm_interactions" else []), "workloadSha256": sha(Path(args.app).read_bytes()),
                            "source": "https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/integration_frameworks/llm/openai/test_openai_" + ("apm.py" if mode == "apm" else "llmobs.py"),
                            "artifacts": []}
                        for file in [mode + "/identity.json", mode + "/events.json", "datadog.yaml", "agent-info.json"]:
                            receipt["artifacts"].append({"file": file, "sha256": sha((out / file).read_bytes())})
                        receipt["artifacts"].extend({"file": mode + "/tracer/" + r["raw_file"], "sha256": r["raw_sha256"]} for r in tracer_records)
                        results.append(receipt)
                        try:
                            check(spans, identity)
                            if mode == "apm":
                                check(native_spans, identity)
                                receipt["artifacts"].append({"file": mode + "/native-spans.json", "sha256": sha((case_dir / "native-spans.json").read_bytes())})
                            receipt["status"] = "passed"
                            print(receipt["name"], "passed", flush=True)
                        except Exception as error:
                            receipt["detail"] = repr(error)
                            raise
            finally:
                for label, records in (("api", api.snapshot()), ("backend", backend.snapshot())):
                    path = out / (label + "-capture.json")
                    path.write_text(json.dumps(records, indent=2) + "\n")
                    for receipt in results:
                        receipt["artifacts"].append({"file": path.name, "sha256": sha(path.read_bytes())})
                        receipt["artifacts"].extend({"file": label + "/" + r["raw_file"], "sha256": r["raw_sha256"]} for r in records)
                (out / "datadog-openai-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")
    assert len(results) == 6


def main():
    if not __debug__:
        raise RuntimeError("OpenAI assertions require Python optimization disabled")
    parser = argparse.ArgumentParser()
    for option in ("agent-rootfs", "launcher", "rootfs", "app", "sdk-overlay"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("agent_rootfs", "launcher", "rootfs", "app", "sdk_overlay"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out)


if __name__ == "__main__":
    main()
