"""Assert SDK telemetry across the tracer and real Agent boundaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import time

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer, Handler as BackendHandler
from harness.datadog_backend.wire import trace_chunks
from harness.datadog_telemetry import assertions
from harness.datadog_telemetry.proxy import CaptureProxy

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
UPSTREAM = "https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/"
TELEMETRY_PATH = "/api/v2/apmtelemetry"
SOURCE_HASHES = {
    "test_telemetry.py": "edfa2307a83513e0cd706e78518d4abe459124df371a715bc54a8c79b230a97e",
    "parametric/test_telemetry.py": "cc765ee35cd0d86b9a9cfc610e6c70c626fc9f7611ff2e72db5d69fa3c527507",
}
CHECKS = [
    ("telemetry_app_started_event", "test_telemetry.py", "Test_Telemetry.test_app_started_sent_exactly_once", assertions.check_startup),
    ("telemetry_configurations_collected", "parametric/test_telemetry.py", "Test_Consistent_Configs.test_library_settings", assertions.check_configuration),
    ("telemetry_heart_beat_collected", "test_telemetry.py", "Test_Telemetry.test_app_heartbeats_delays", assertions.check_heartbeat),
    ("telemetry_metrics_collected", "test_telemetry.py", "Test_Metric_Generation_Enabled.test_metric_tracers_spans_created", assertions.check_metrics),
    ("telemetry_api_v2_implemented", "test_telemetry.py", "Test_TelemetryV2.test_app_started_product_info", assertions.check_v2),
    ("app_extended_heartbeat_event", "test_telemetry.py", "Test_ExtendedHeartbeat.test_extended_heartbeat_config_matches", assertions.check_extended),
    ("telemetry_message_batch", "test_telemetry.py", "Test_MessageBatch.test_message_batch_enabled", assertions.check_batch),
    ("dd_telemetry_dependency_collection_enabled_supported", "test_telemetry.py", "Test_DependencyEnable.test_app_dependency_loaded_not_sent_dependency_collection_disabled", assertions.check_dependencies),
    ("telemetry_instrumentation", "test_telemetry.py", "Test_Telemetry.test_proxy_forwarding", assertions.check_forwarding),
]


class TelemetryBackendHandler(BackendHandler):
    def setup(self):
        super().setup()
        # Agent forwards on several pooled sockets. An idle socket remains open
        # longer than this bounded test instead of racing its next POST.
        self.connection.settimeout(60)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run_workload(args, agent_url, out, dependencies):
    out.mkdir(parents=True, exist_ok=True)
    identity_path, stop_path = out / "identity.json", out / "stop"
    stop_path.unlink(missing_ok=True)
    identity_path.unlink(missing_ok=True)
    proxy = CaptureProxy(agent_url, out / "tracer")
    with server_thread(proxy):
        env = dict(BASE_ENV)
        env.update({"DD_TRACE_AGENT_URL": "http://127.0.0.1:" + str(proxy.server_port),
                    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true", "DD_TELEMETRY_HEARTBEAT_INTERVAL": "1",
                    "_DD_TELEMETRY_EXTENDED_HEARTBEAT_INTERVAL": "2",
                    "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED": str(dependencies).lower(),
                    "DD_SERVICE": "telemetry-lab", "DD_ENV": "telemetry-env", "DD_VERSION": "telemetry-version",
                    "DD_TRACE_RATE_LIMIT": "7", "DD_TRACE_API_VERSION": "v0.5"})
        (out / "requested-environment.json").write_text(json.dumps(env, indent=2) + "\n")
        launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
        launch += ["--instance=datadog-telemetry-" + str(dependencies).lower()]
        launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        launch += ["--", args.app, "--identity-file", str(identity_path), "--stop-file", str(stop_path)]
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
        log_path = out / "app.log"
        with process(launch, log_path, clean_env) as proc:
            deadline = time.monotonic() + 30
            while not identity_path.exists():
                assert proc.poll() is None and time.monotonic() < deadline, log_path.read_text(errors="replace")
                time.sleep(0.1)
            # Native telemetry metric flushes occur every ten seconds. Waiting
            # longer also supplies multiple actual heartbeat intervals.
            deadline = time.monotonic() + 13
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text(errors="replace")
                time.sleep(0.1)
            stop_path.write_text("stop\n")
            assert proc.wait(timeout=15) == 0, log_path.read_text(errors="replace")
        return json.loads(identity_path.read_text()), proxy.snapshot(), env


def execute(args, out):
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = TelemetryBackendHandler
    certificate = Path(__file__).with_name("localhost-test.crt")
    # The checked-in key is a public test fixture, never a production identity.
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, Path(__file__).with_name("localhost-test.key"))
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    results = []
    with server_thread(backend):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        backend_url = "https://127.0.0.1:" + str(backend.server_port)
        config = {
            "api_key": API_KEY, "hostname": "telemetry-agent", "env": "telemetry-agent-env", "dd_url": backend_url,
            "log_level": "info", "log_to_console": True, "log_file": "", "cmd_port": 0,
            "remote_configuration": {"enabled": False}, "agent_telemetry": {"enabled": False},
            "apm_config": {"enabled": True, "receiver_port": port, "receiver_socket": "", "log_file": "",
                "debug": {"port": 0}, "apm_non_local_traffic": False, "apm_dd_url": backend_url,
                "telemetry": {"enabled": True, "dd_url": backend_url},
                "max_memory": 0, "max_cpu_percent": 0, "trace_writer": {"flush_period_seconds": 0.2}},
        }
        # JSON is a YAML subset and preserves the numeric-looking API key string.
        config_path = out / "datadog.yaml"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_"))}
        clean_env["SSL_CERT_FILE"] = str(certificate)
        command = agent_command(args.agent_rootfs) + ["run", "--config=" + str(config_path)]
        log_path = out / "agent.log"
        with process(command, log_path, clean_env) as proc:
            agent_url = "http://127.0.0.1:" + str(port)
            info = wait_ready(proc, agent_url + "/info", log_path)
            assert info["version"] == AGENT_VERSION, info
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            try:
                for dependencies in (True, False):
                    name = "dependencies-enabled" if dependencies else "dependencies-disabled"
                    identity, records, env = run_workload(args, agent_url, out / name, dependencies)
                    assert all(record["status"] == 200 for record in records), records
                    tracer_records = assertions.observations(records, identity)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        forwarded = [record for record in backend.snapshot() if record["path"] == TELEMETRY_PATH
                                     and (record.get("payload") or {}).get("runtime_id") == identity["runtime_id"]]
                        if len(forwarded) >= len(tracer_records):
                            break
                        time.sleep(0.1)
                    backend_records = backend.snapshot()
                    wanted = {int(span["span_id"]) for span in identity["spans"]}
                    delivered = [span for record in backend_records if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                                 for chunk in trace_chunks(record["payload"]) for span in chunk["spans"] if span["span_id"] in wanted]
                    assert len(delivered) == len(wanted) == 3, delivered
                    assert all(span["name"] == "telemetry.controlled" and span["service"] == "telemetry-lab" for span in delivered), delivered
                    assert all(span["meta"]["runtime-id"] == identity["runtime_id"] for span in delivered), delivered
                    for feature, source, method, check in CHECKS:
                        receipt = {"name": name + ":" + feature, "feature": feature, "status": "failed",
                                   "capabilityNames": [feature], "capabilityInventoryRevision": REVISION,
                                   "source": UPSTREAM + source + "#" + method, "sourceMethod": method,
                                   "sourceSha256": SOURCE_HASHES[source], "configuration": env,
                                   "agentConfigurationSha256": sha(config_path.read_bytes()),
                                   "workloadSha256": sha(Path(args.app).read_bytes()),
                                   "identitySha256": sha((out / name / "identity.json").read_bytes()),
                                   "tracerCaptureSha256": sha((out / name / "tracer/requests.json").read_bytes())}
                        receipt.update(captureFile=name + "/tracer/requests.json", captureSha256=receipt["tracerCaptureSha256"])
                        receipt["artifacts"] = [
                            {"file": name + "/identity.json", "sha256": receipt["identitySha256"]},
                            {"file": "datadog.yaml", "sha256": receipt["agentConfigurationSha256"]},
                            {"file": "agent-info.json", "sha256": sha((out / "agent-info.json").read_bytes())},
                        ] + [{"file": name + "/tracer/" + record["raw_file"], "sha256": record["raw_sha256"]} for record in records]
                        results.append(receipt)
                        try:
                            if check == assertions.check_forwarding:
                                detail = check(records, backend_records, identity)
                            elif check in (assertions.check_configuration, assertions.check_extended, assertions.check_dependencies):
                                detail = check(records, identity, dependencies)
                            else:
                                detail = check(records, identity)
                            receipt.update(status="passed", detail=detail)
                            print(name, feature, "passed", flush=True)
                        except Exception as error:
                            receipt["detail"] = repr(error)
                            raise
            finally:
                backend_bytes = json.dumps(backend.snapshot(), indent=2).encode() + b"\n"
                (out / "backend-capture.json").write_bytes(backend_bytes)
                for result in results:
                    result["backendCaptureSha256"] = sha(backend_bytes)
                    result["artifacts"].append({"file": "backend-capture.json", "sha256": sha(backend_bytes)})
                    result["artifacts"].extend({"file": "backend/" + record["raw_file"], "sha256": record["raw_sha256"]} for record in backend.snapshot())
                missing = [{"name": name, "status": "missing", "reason": "Pinned Python manifest declares missing_feature; no support inferred from other counters"}
                           for name in ("metric-generation-disable-control", "spans_enqueued_for_serialization", "trace_chunks_enqueued_for_serialization")]
                report = {"schemaVersion": 1, "agentVersion": AGENT_VERSION, "results": results, "missingAssertions": missing}
                (out / "datadog-telemetry-results.json").write_text(json.dumps(report, indent=2) + "\n")
    assert len(results) == len(CHECKS) * 2, results


def main():
    if not __debug__:
        raise RuntimeError("Telemetry assertions require Python optimization disabled")
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
