"""Prove LLMObs SDK event semantics through the pinned Agent EVP proxy."""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import time

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer, decompress
from harness.datadog_telemetry.probe import TelemetryBackendHandler, sha
from harness.datadog_telemetry.proxy import CaptureProxy
from harness.datadog_llmobs.connect import ConnectRelay
from harness.datadog_llmobs import assertions

LLMOBS_PATH = "/api/v2/llmobs"
SDK_PATH = "/evp_proxy/v2" + LLMOBS_PATH
REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
SOURCE = "https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/parametric/test_llm_observability/test_llm_observability.py"
SOURCE_SHA256 = "f278f1ac42070b31dc5aad400cd04f0a4c3ae49e5b9ba63ecaff20a8ab7126b6"
CHECKS = [
    ("llm_observability_sdk_enablement", "Test_Enablement.test_ml_app", 33, assertions.check_enablement),
    ("llm_observability_prompts", "Test_Prompts.test_prompt_annotation", 51, assertions.check_prompts),
    ("llm_observability_cost_tags", "Test_CostTags.test_cost_tags_annotated_to_llm_span", 289, assertions.check_cost_tags),
]


def run_workload(args, agent_url, out, ml_app):
    out.mkdir(parents=True, exist_ok=True)
    identity_path = out / "identity.json"
    identity_path.unlink(missing_ok=True)
    proxy = CaptureProxy(agent_url, out / "tracer")
    with server_thread(proxy):
        env = dict(BASE_ENV)
        env.update({"DD_TRACE_AGENT_URL": "http://127.0.0.1:" + str(proxy.server_port), "DD_SERVICE": "llmobs-lab",
                    "DD_ENV": "llmobs-env", "DD_VERSION": "llmobs-version",
                    "DD_LLMOBS_AGENTLESS_ENABLED": "false", "DD_APM_TRACING_ENABLED": "false",
                    "_DD_LLMOBS_WRITER_INTERVAL": "3600"})
        (out / "requested-environment.json").write_text(json.dumps(env, indent=2) + "\n")
        launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
        launch += ["--instance=datadog-llmobs-" + out.name]
        launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        launch += ["--", args.app, "--identity-file", str(identity_path)]
        if ml_app is not None:
            launch += ["--ml-app", ml_app]
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_")) and key != "PYTHONOPTIMIZE"}
        log_path = out / "app.log"
        with process(launch, log_path, clean_env) as proc:
            assert proc.wait(timeout=35) == 0, log_path.read_text(errors="replace")
        assert identity_path.exists(), log_path.read_text(errors="replace")
        return json.loads(identity_path.read_text()), proxy.snapshot(), env


def execute(args, out):
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = TelemetryBackendHandler
    certificate = Path(__file__).with_name("localhost-test.crt")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, Path(__file__).with_name("localhost-test.key"))
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    relay = ConnectRelay(backend.server_port)
    results = []
    with server_thread(backend), server_thread(relay):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        backend_url = "https://127.0.0.1:" + str(backend.server_port)
        config = {
            "api_key": API_KEY, "hostname": "llmobs-agent", "env": "llmobs-agent-env", "dd_url": backend_url,
            "log_level": "info", "log_to_console": True, "log_file": "", "cmd_port": 0,
            "proxy": {"https": "http://127.0.0.1:" + str(relay.server_port), "no_proxy": ["127.0.0.1", "localhost"]},
            "evp_proxy_config": {"enabled": True, "dd_url": "backend.test"},
            "remote_configuration": {"enabled": False}, "agent_telemetry": {"enabled": False},
            "apm_config": {"enabled": True, "receiver_port": port, "receiver_socket": "", "log_file": "",
                "debug": {"port": 0}, "apm_non_local_traffic": False, "apm_dd_url": backend_url,
                "telemetry": {"enabled": False}, "max_memory": 0, "max_cpu_percent": 0},
        }
        config_path = out / "datadog.yaml"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_"))}
        clean_env["SSL_CERT_FILE"] = str(certificate)
        command = agent_command(args.agent_rootfs) + ["run", "--config=" + str(config_path)]
        log_path = out / "agent.log"
        with process(command, log_path, clean_env) as proc:
            agent_url = "http://127.0.0.1:" + str(port)
            info = wait_ready(proc, agent_url + "/info", log_path)
            assert info["version"] == AGENT_VERSION and "/evp_proxy/v2/" in info["endpoints"], info
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            try:
                for name, ml_app in [("override", "overridden-test-ml-app"), ("default", None), ("empty", "")]:
                    case_dir = out / name
                    identity, tracer_records, env = run_workload(args, agent_url, case_dir, ml_app)
                    assert all(record["status"] == 200 for record in tracer_records), tracer_records
                    requests = [record for record in tracer_records if record["path"] == SDK_PATH]
                    assert requests, "No SDK LLMObs EVP requests"
                    spans = []
                    for request in requests:
                        matches = [record for record in backend.snapshot() if record["path"] == LLMOBS_PATH
                                   and record["raw_sha256"] == request["raw_sha256"]]
                        assert len(matches) == 1 and matches[0]["status"] == 200, matches
                        forwarded = matches[0]
                        assert request["headers"]["x-datadog-evp-subdomain"] == "llmobs-intake", request
                        assert forwarded["headers"]["via"] == "trace-agent " + AGENT_VERSION, forwarded
                        assert forwarded["headers"]["x-datadog-hostname"] == "llmobs-agent", forwarded
                        assert forwarded["headers"]["x-datadog-agentdefaultenv"] == "llmobs-agent-env", forwarded
                        raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
                        assert sha(raw) == request["raw_sha256"], request
                        assert (out / "backend" / forwarded["raw_file"]).read_bytes() == raw, forwarded
                        document = json.loads(decompress(raw, request["headers"].get("content-encoding", "")))
                        assert document == forwarded["payload"], (document, forwarded)
                        for batch in document:
                            assert batch["_dd.stage"] == "raw" and batch["event_type"] == "span", batch
                            assert batch["_dd.tracer_version"] == identity["tracer_version"], batch
                            spans.extend(batch["spans"])
                    assert len(spans) == len(identity["spans"]) == 14, (len(spans), identity)
                    by_name = {span["name"]: span for span in spans}
                    assert set(by_name) == set(identity["spans"]), by_name
                    for span_name, expected in identity["spans"].items():
                        actual = by_name[span_name]
                        assert actual["span_id"] == expected["span_id"] and actual["trace_id"] == expected["trace_id"], actual
                    (case_dir / "events.json").write_text(json.dumps(spans, indent=2) + "\n")
                    for feature, method, line, check in CHECKS:
                        result = {"name": name + ":" + feature, "feature": feature, "status": "failed",
                                  "capabilityNames": [feature], "capabilityInventoryRevision": REVISION,
                                  "configuration": env, "enableArguments": {"ml_app": ml_app, "agentless_enabled": False},
                                  "source": SOURCE + "#L" + str(line), "sourceSha256": SOURCE_SHA256, "sourceMethod": method,
                                  "agentVersion": AGENT_VERSION, "agentConfigurationSha256": sha(config_path.read_bytes()),
                                  "workloadSha256": sha(Path(args.app).read_bytes()), "identitySha256": sha((case_dir / "identity.json").read_bytes()),
                                  "tracerCaptureSha256": sha((case_dir / "tracer/requests.json").read_bytes()),
                                  "eventsSha256": sha((case_dir / "events.json").read_bytes())}
                        result.update(captureFile=name + "/tracer/requests.json", captureSha256=result["tracerCaptureSha256"])
                        result["artifacts"] = [
                            {"file": name + "/identity.json", "sha256": result["identitySha256"]},
                            {"file": name + "/events.json", "sha256": result["eventsSha256"]},
                            {"file": "datadog.yaml", "sha256": result["agentConfigurationSha256"]},
                            {"file": "agent-info.json", "sha256": sha((out / "agent-info.json").read_bytes())},
                        ] + [{"file": name + "/tracer/" + record["raw_file"], "sha256": record["raw_sha256"]} for record in tracer_records]
                        results.append(result)
                        try:
                            check(by_name, ml_app)
                            result["status"] = "passed"
                            print(name, feature, "passed", flush=True)
                        except Exception as error:
                            result["detail"] = repr(error)
                            raise
            finally:
                (out / "connect-relay.json").write_text(json.dumps(relay.records, indent=2) + "\n")
                backend_bytes = json.dumps(backend.snapshot(), indent=2).encode() + b"\n"
                (out / "backend-capture.json").write_bytes(backend_bytes)
                for result in results:
                    result["backendCaptureSha256"] = sha(backend_bytes)
                    result["artifacts"].append({"file": "backend-capture.json", "sha256": sha(backend_bytes)})
                    result["artifacts"].extend({"file": "backend/" + record["raw_file"], "sha256": record["raw_sha256"]} for record in backend.snapshot())
                (out / "datadog-llmobs-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")
    assert len(results) == len(CHECKS) * 3, results
    assert "llmobs-intake.backend.test:443" in relay.records, relay.records


def main():
    if not __debug__:
        raise RuntimeError("LLMObs assertions require Python optimization disabled")
    parser = argparse.ArgumentParser()
    for option in ("agent-rootfs", "launcher", "rootfs", "app"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("agent_rootfs", "launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out)


if __name__ == "__main__":
    main()
