"""Deliver signed remote configuration through real core and trace Agents."""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import time

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, core_agent, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_llmobs.connect import ConnectRelay
from harness.datadog_telemetry.proxy import CaptureProxy
from harness.datadog_telemetry.probe import sha
from harness.datadog_debugger.backend import FIXTURES, Handler, decode_document
from harness.datadog_debugger import assertions
from harness.datadog_backend.backend import decompress
import uuid


SOURCE_HASHES = {'test_debugger_expression_language.py': '50ae83b9cf4cec5bf4b82523500cf9c56f7aba57ee38f634f29278f45b4cc47d', 'test_debugger_pii.py': '6424fbd8062227a33fc50c6a7ac22bf4bbe638de627b65fef817d4c7e7115107', 'test_debugger_probe_budgets.py': '9b9ef7c01a96f4b4ccfab0b17abbce5d7ac249e96052cfd3e230609679ed603f', 'test_debugger_probe_snapshot.py': '0394c8f8698095b72178a9383ffa3258a2d6c483e765e14289bf8b2dcda2162c'}

def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, message, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result: return result
        time.sleep(0.1)
    raise AssertionError(message)


def rc_requests(proxy, directory):
    return [json.loads((directory / r["raw_file"]).read_bytes()) for r in proxy.snapshot() if r["path"] == "/v0.7/config"]


def execute(args, out):
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = Handler
    backend.rc_stage = 1
    backend.rc_fixtures = FIXTURES
    certificate = Path(__file__).parent.parent / "datadog_llmobs/localhost-test.crt"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, certificate.with_suffix(".key"))
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    relay = ConnectRelay(backend.server_port)
    with server_thread(backend), server_thread(relay):
        apm_port, health_port, cmd_port = port(), port(), port()
        backend_url = "https://127.0.0.1:" + str(backend.server_port)
        config = {"api_key": API_KEY, "hostname": "rc-agent", "env": "rc-env", "dd_url": backend_url,
            "log_level": "debug", "log_to_console": True, "log_file": "", "cmd_port": cmd_port, "expvar_port": 0,
            "proxy": {"http": "http://127.0.0.1:" + str(relay.server_port), "https": "http://127.0.0.1:" + str(relay.server_port)},
            "remote_configuration": {"enabled": True, "no_tls": False, "no_tls_validation": False, "rc_dd_url": backend_url, "refresh_interval": "5s", "org_status_refresh_interval": "5s",
                                     "config_root": json.dumps(FIXTURES["root"]), "director_root": json.dumps(FIXTURES["root"])},
            "agent_telemetry": {"enabled": False}, "health_platform": {"enabled": False}, "inventories_diagnostics_enabled": False, "health_port": health_port, "dogstatsd_port": port(), "dogstatsd_socket": "",
            "enable_metadata_collection": False, "inventories_enabled": False, "cloud_provider_metadata": [],
            "run_path": str(out / "core-run"), "conf_path": str(out / "conf.d"), "auth_token_file_path": str(out / "auth_token"),
            "logs_enabled": True, "process_config": {"process_collection": {"enabled": False}, "container_collection": {"enabled": False},
                "process_discovery": {"enabled": False}, "run_in_core_agent": False},
            "apm_config": {"enabled": True, "receiver_port": apm_port, "receiver_socket": "", "log_file": "", "debug": {"port": 0},
                "debugger_dd_url": backend_url + "/api/v2/debugger", "debugger_diagnostics_dd_url": backend_url + "/api/v2/debugger",
                "apm_non_local_traffic": False, "apm_dd_url": backend_url, "telemetry": {"enabled": False},
                "trace_writer": {"flush_period_seconds": 0.2}, "max_memory": 0, "max_cpu_percent": 0}}
        (out / "conf.d").mkdir(exist_ok=True)
        config_path = out / "datadog.yaml"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_"))}
        clean_env["SSL_CERT_FILE"] = str(certificate)
        args.core_agent = True
        args.backend_url = "http://127.0.0.1:" + str(relay.server_port)
        with core_agent(args, config_path, out, clean_env, health_port), process(agent_command(args.agent_rootfs) + ["run", "--config=" + str(config_path)], out / "agent.log", clean_env) as apm:
            agent_url = "http://127.0.0.1:" + str(apm_port)
            info = wait_ready(apm, agent_url + "/info", out / "agent.log")
            assert info["version"] == AGENT_VERSION and "/v0.7/config" in info["endpoints"], info
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            proxy = CaptureProxy(agent_url, out / "tracer")
            with server_thread(proxy):
                env = dict(BASE_ENV)
                env.pop("DD_TRACE_SAMPLING_RULES")
                env.update({"DD_SERVICE": "rc-lab", "DD_ENV": "rc-env", "DD_VERSION": "rc-version",
                    "DD_TRACE_SAMPLE_RATE": "1", "DD_TAGS": "local_tag:local-value", "DD_TRACE_API_VERSION": "v0.4",
                    "DD_DYNAMIC_INSTRUMENTATION_ENABLED": "true", "DD_DYNAMIC_INSTRUMENTATION_UPLOAD_INTERVAL_SECONDS": "0.2",
                    "DD_REMOTE_CONFIGURATION_ENABLED": "true", "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS": "0.2",
                    "DD_TRACE_AGENT_URL": "http://127.0.0.1:" + str(proxy.server_port)})
                launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
                launch += ["--instance=datadog-debugger"] + ["--env=" + key + "=" + value for key, value in sorted(env.items())]
                launch += ["--", args.app, "--directory", str(out)]
                with process(launch, out / "app.log", clean_env) as sdk:
                    wait_for(lambda: (out / "ready.json").exists(), "SDK did not start")
                    for stage in (1, 2, 3):
                        backend.rc_stage = stage
                        if stage == 1:
                            wait_for(lambda: rc_requests(proxy, out / "tracer"), "SDK did not poll real Agent")
                        elif stage == 2:
                            def acknowledged():
                                return [r for r in rc_requests(proxy, out / "tracer") if any(c.get("product") == "LIVE_DEBUGGING" and c.get("version") == stage and c.get("apply_state") == 2 for c in r["client"]["state"].get("config_states", []))]
                            wait_for(acknowledged, "No real Agent RC acknowledgement for stage " + str(stage))
                        else:
                            wait_for(lambda: any(r["client"]["state"]["targets_version"] == stage for r in rc_requests(proxy, out / "tracer")), "RC did not remove probes")
                        (out / (str(stage) + ".command")).touch()
                        wait_for(lambda: (out / (str(stage) + ".identity.json")).exists(), "Missing workload stage " + str(stage))
                    def backend_acknowledged():
                        return any(c.get("is_tracer") and c["state"].get("targets_version") == 3
                                   for r in backend.snapshot() if r["path"] == "/api/v0.1/configurations"
                                   for c in r["payload"]["request"].get("active_clients", []))
                    wait_for(backend_acknowledged, "Core Agent did not report tracer RC acknowledgement upstream")
                    assert sdk.wait(timeout=20) == 0, (out / "app.log").read_text()
                spans = [span for record in proxy.snapshot() if record["path"] == "/v0.4/traces"
                         for trace in msgpack((out / "tracer" / record["raw_file"]).read_bytes()) for span in trace]
                (out / "native-spans.json").write_text(json.dumps(spans, indent=2) + "\n")
                (out / "rc-requests.json").write_text(json.dumps(rc_requests(proxy, out / "tracer"), indent=2) + "\n")
                identities = [json.loads((out / (str(i) + ".identity.json")).read_text()) for i in (1, 2, 3)]
                ready = json.loads((out / "ready.json").read_text())
                rows = [r for r in proxy.snapshot() if r["path"].startswith("/debugger/")]
                assert rows and all(r["status"] == 200 for r in rows), rows
                events = []
                for row in rows:
                    raw = (out / "tracer" / row["raw_file"]).read_bytes()
                    assert assertions.SECRET.encode() not in raw
                    matches = [r for r in backend.snapshot() if r["path"] == "/api/v2/debugger" and r["raw_sha256"] == row["raw_sha256"]]
                    assert len(matches) == 1 and matches[0]["status"] == 200, matches
                    forwarded = matches[0]
                    assert (out / "backend" / forwarded["raw_file"]).read_bytes() == raw
                    assert sha(raw) == row["raw_sha256"]
                    assert forwarded["headers"]["dd-evp-origin"] == "agent-debugger", forwarded
                    uuid.UUID(forwarded["headers"]["dd-request-id"])
                    tags = set(",".join(forwarded["payload"]["ddtags"]).split(","))
                    assert {"host:rc-agent", "default_env:rc-env", "agent_version:" + AGENT_VERSION} <= tags, tags
                    document = decode_document(decompress(raw, row["headers"].get("content-encoding", "")), row["headers"]["content-type"])
                    assert document == forwarded["payload"]["document"]
                    events.extend(document)
                diagnostics = [e["debugger"]["diagnostics"] for e in events if "diagnostics" in e.get("debugger", {})]
                for probe in assertions.PROBES:
                    statuses = {d["status"] for d in diagnostics if d["probeId"] == probe and d["probeVersion"] == 1 and d["runtimeId"] == ready["runtime_id"]}
                    assert {"RECEIVED", "INSTALLED", "EMITTING"} <= statuses and "ERROR" not in statuses, (probe, statuses)
                controls = [span for row in backend.snapshot() if row["path"] == "/api/v0.2/traces" and row["status"] == 200 for chunk in trace_chunks(row["payload"]) for span in chunk["spans"]]
                assert {int(i["span_id"]) for i in identities} <= {s["span_id"] for s in controls}, controls
                (out / "events.json").write_text(json.dumps(events, indent=2) + "\n")
                (out / "agent-spans.json").write_text(json.dumps(controls, indent=2) + "\n")
                results = []
                try:
                    for feature, check, source in assertions.CHECKS:
                        receipt = {"name": feature, "status": "failed", "capabilityNames": [feature],
                            "capabilityInventoryRevision": "098fe0967c587db8a16b74a1e711777d0a9d5867", "configuration": env,
                            "agentVersion": AGENT_VERSION, "clientVersion": ready["tracer_version"],
                            "source": "https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/debugger/" + source,
                            "sourceSha256": SOURCE_HASHES[source],
                            "captureFile": "tracer/requests.json", "captureSha256": sha((out / "tracer/requests.json").read_bytes()),
                            "workloadSha256": sha(Path(args.app).read_bytes()), "scope": "Four signed LIVE_DEBUGGING probes; real core/trace Agent forwarding; install, invoke, remove",
                            "missingAssertions": ["Other expression operators, probe types, redaction sources and capture limits"], "artifacts": []}
                        results.append(receipt)
                        try:
                            check(events, identities)
                            receipt["status"] = "passed"
                            print(feature, "passed", flush=True)
                        except Exception as error:
                            receipt["detail"] = repr(error)
                            raise
                finally:
                    (out / "backend-capture.json").write_text(json.dumps(backend.snapshot(), indent=2) + "\n")
                    (out / "signed-fixtures.json").write_text(json.dumps(FIXTURES, indent=2) + "\n")
                    (out / "connect-relay.json").write_text(json.dumps(relay.records, indent=2) + "\n")
                    files = ["backend-capture.json", "signed-fixtures.json", "connect-relay.json", "native-spans.json", "agent-spans.json", "rc-requests.json", "ready.json", "datadog.yaml", "agent-info.json", "events.json"]
                    files += [str(i) + ".identity.json" for i in (1, 2, 3)]
                    files += ["tracer/" + r["raw_file"] for r in proxy.snapshot()]
                    files += ["backend/" + r["raw_file"] for r in backend.snapshot()]
                    files += ["backend/" + r["payload"]["response_file"] for r in backend.snapshot() if r["path"].startswith("/api/v0.1/")]
                    for receipt in results:
                        receipt["artifacts"] = [{"file": file, "sha256": sha((out / file).read_bytes())} for file in sorted(set(files))]
                    (out / "datadog-debugger-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")


def main():
    if not __debug__:
        raise RuntimeError("RC assertions require Python optimization disabled")
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
