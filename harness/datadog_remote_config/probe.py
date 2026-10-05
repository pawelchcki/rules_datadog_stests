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
from harness.datadog_remote_config.backend import FIXTURES, Handler
from harness.datadog_remote_config import assertions


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
            "logs_enabled": False, "process_config": {"process_collection": {"enabled": False}, "container_collection": {"enabled": False},
                "process_discovery": {"enabled": False}, "run_in_core_agent": False},
            "apm_config": {"enabled": True, "receiver_port": apm_port, "receiver_socket": "", "log_file": "", "debug": {"port": 0},
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
                    "DD_REMOTE_CONFIGURATION_ENABLED": "true", "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS": "0.2",
                    "DD_TRACE_AGENT_URL": "http://127.0.0.1:" + str(proxy.server_port)})
                launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
                launch += ["--instance=datadog-remote-config"] + ["--env=" + key + "=" + value for key, value in sorted(env.items())]
                launch += ["--", args.app, "--directory", str(out), "--url", "http://127.0.0.1:" + str(proxy.server_port) + "/info"]
                with process(launch, out / "app.log", clean_env) as sdk:
                    wait_for(lambda: (out / "ready.json").exists(), "SDK did not start")
                    for stage in (1, 2, 3):
                        backend.rc_stage = stage
                        if stage == 1:
                            wait_for(lambda: rc_requests(proxy, out / "tracer"), "SDK did not poll real Agent")
                        else:
                            def acknowledged():
                                return [r for r in rc_requests(proxy, out / "tracer") if any(c.get("product") == "APM_TRACING" and c.get("version") == stage and c.get("apply_state") == 2 for c in r["client"]["state"].get("config_states", []))]
                            wait_for(acknowledged, "No real Agent RC acknowledgement for stage " + str(stage))
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
                identities = [json.loads((out / (str(stage) + ".identity.json")).read_text()) for stage in (1, 2, 3)]
                ready = json.loads((out / "ready.json").read_text())
                requests = rc_requests(proxy, out / "tracer")
                def delivered():
                    exported = [s for r in backend.snapshot() if r["path"] == "/api/v0.2/traces" and r["status"] == 200
                                for c in trace_chunks(r["payload"]) for s in c["spans"]]
                    return exported if int(identities[-1]["span_id"]) in {s["span_id"] for s in exported} else None
                exported = wait_for(delivered, "Agent did not export post-reset control trace")
                (out / "agent-spans.json").write_text(json.dumps(exported, indent=2) + "\n")
                checks = [
                    ("dynamic_configuration", lambda: assertions.check_dynamic(spans, identities)),
                    ("adaptive_sampling", lambda: assertions.check_sampling(spans, identities, exported)),
                    ("remote_config_object_supported", lambda: assertions.check_protocol(requests, ready, backend.snapshot())),
                    ("remote_config_semantic_versioning", lambda: assertions.check_version(requests, ready)),
                ]
                results = []
                try:
                    for feature, check in checks:
                        source = "tests/parametric/test_dynamic_configuration.py" if feature in ("dynamic_configuration", "adaptive_sampling") else "tests/remote_config/test_remote_configuration.py"
                        result = {"name": feature, "status": "failed", "capabilityNames": [feature],
                                  "capabilityInventoryRevision": "098fe0967c587db8a16b74a1e711777d0a9d5867",
                                  "configuration": env, "agentVersion": AGENT_VERSION, "clientVersion": ready["tracer_version"],
                                  "captureFile": "tracer/requests.json", "captureSha256": sha((out / "tracer/requests.json").read_bytes()),
                                  "source": "https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/" + source,
                                  "workloadSha256": sha(Path(args.app).read_bytes()),
                                  "sourceSha256": "9745518e579088d6a4dd46f4b87ced33fbb398a054f7bdbc31e9404b3d575982" if feature in ("dynamic_configuration", "adaptive_sampling") else "572968ae8bb54a4a9dcea977839a5d93ecd9c4b97769fd482eabcc3a17cced53",
                                  "scope": "Signed APM_TRACING product; baseline, remote update, reset; verified TLS and real core/trace Agents",
                                  "missingAssertions": ["Other RC products and TUF root rotation"], "artifacts": []}
                        results.append(result)
                        try:
                            check()
                            result["status"] = "passed"
                            print(feature, "passed", flush=True)
                        except Exception as error:
                            result["detail"] = repr(error)
                            raise
                finally:
                    (out / "backend-capture.json").write_text(json.dumps(backend.snapshot(), indent=2) + "\n")
                    (out / "signed-fixtures.json").write_text(json.dumps(FIXTURES, indent=2) + "\n")
                    (out / "connect-relay.json").write_text(json.dumps(relay.records, indent=2) + "\n")
                    files = ["backend-capture.json", "signed-fixtures.json", "connect-relay.json", "native-spans.json", "agent-spans.json", "rc-requests.json", "ready.json", "datadog.yaml", "agent-info.json"]
                    files += [str(stage) + ".identity.json" for stage in (1, 2, 3)]
                    files += ["tracer/" + r["raw_file"] for r in proxy.snapshot()]
                    files += ["backend/" + r["raw_file"] for r in backend.snapshot()]
                    files += ["backend/" + r["payload"]["response_file"] for r in backend.snapshot() if r["path"].startswith("/api/v0.1/")]
                    for result in results:
                        result["artifacts"] = [{"file": file, "sha256": sha((out / file).read_bytes())} for file in sorted(set(files))]
                    (out / "datadog-remote-config-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")


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
