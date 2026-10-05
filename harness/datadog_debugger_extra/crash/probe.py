"""Native SIGSEGV report enabled/disabled controls with Agent forwarding."""
import json
import signal
import time
from harness.datadog_debugger_extra.common import *
from harness.datadog_debugger.backend import FIXTURES, Handler

def logs(rows, ready):
    documents = [r["payload"]["document"] for r in rows if r["path"] == "/telemetry/proxy/api/v2/apmtelemetry" and r.get("payload")]
    result = []
    for doc in documents:
        for event in doc["payload"] if doc["request_type"] == "message-batch" else [doc]:
            if event["request_type"] == "logs":
                payload = event["payload"]
                for log in payload if isinstance(payload, list) else payload["logs"]:
                    if "is_crash:true" in log.get("tags", "") and "is_crash_ping:true" not in log.get("tags", ""):
                        result.append(log)
    return result

def execute(args, out):
    results = []
    with backend_context(out, Handler, FIXTURES) as (backend, relay, url):
        apm, health, cmd = port(), port(), port()
        config = agent_config(out, url, relay.server_port, FIXTURES, apm, health, cmd, telemetry=True)
        config["apm_config"]["telemetry"]["dd_url"] = url
        with agents_running(args, config, out, health, relay.server_port) as (_, clean):
            try:
                for enabled in (True, False):
                    name = "enabled" if enabled else "disabled"
                    directory = out / name
                    directory.mkdir()
                    proxy = CaptureProxy("http://127.0.0.1:" + str(apm), directory / "tracer")
                    with server_thread(proxy):
                        env = dict(BASE_ENV)
                        env.update(DD_TRACE_AGENT_URL="http://127.0.0.1:" + str(proxy.server_port), DD_TRACE_API_VERSION="v0.4", DD_CRASHTRACKING_ENABLED=str(enabled).lower(), DD_CRASHTRACKING_ERRORS_INTAKE_ENABLED="false", DD_CRASHTRACKING_STACKTRACE_RESOLVER="none", DD_SERVICE="extra-crash", DD_CRASHTRACKING_STDOUT_FILENAME=str(directory / "receiver.stdout"), DD_CRASHTRACKING_STDERR_FILENAME=str(directory / "receiver.stderr"))
                        with process(launch_command(args, env, directory, instance="extra-crash-" + name), directory / "app.log", clean) as sdk:
                            ready = wait_for(lambda: json.loads((directory / "ready.json").read_text()) if (directory / "ready.json").exists() else None, "Crash SDK did not start")
                            assert ready["tracer_version"] == "4.14.0", ready
                            assert ready["crashtracker_available"] and ready["crashtracker_started"] == enabled, ready
                            record = receipt("crashtracking", env, ready, "Real SIGSEGV from isolated pinned SDK; enabled report and disabled absence, control span and Agent forwarding", ["Other signals and delayed intake timeout"], "tests/parametric/test_crashtracking.py", "d21b9166ed7fbaf06e490b649e0c23c87bad3d2f8770feb153800d88619129e8")
                            record["name"] = name + ":crashtracking"
                            record["captureFile"] = name + "/tracer/requests.json"
                            results.append(record)
                            # Start the fatal signal only after the ordinary
                            # native writer has proved this exact SDK identity
                            # can reach the real Agent and backend.
                            wanted = int(ready["span_id"])
                            wait_for(lambda: any(s["span_id"] == wanted for r in backend.snapshot() if r["path"] == "/api/v0.2/traces" for chunk in trace_chunks(r["payload"]) for s in chunk["spans"]), "Crash control span not forwarded before signal")
                            started = time.monotonic()
                            (directory / "crash.command").touch()
                            status = sdk.wait(timeout=15)
                            assert status == -signal.SIGSEGV and time.monotonic() - started < 15, status
                        if enabled:
                            wait_for(lambda: logs(proxy.snapshot(), ready), "Native crash report not captured", timeout=60)
                        else:
                            time.sleep(2)
                        reports = logs(proxy.snapshot(), ready)
                        assert bool(reports) == enabled, reports
                        if enabled:
                            tags = dict(t.split(":", 1) for t in reports[0]["tags"].split(","))
                            assert tags.get("si_signo", tags.get("signum")) == "11", tags
                            crash = json.loads(reports[0]["message"])
                            metadata_tags = dict(t.split(":", 1) for t in crash["metadata"]["tags"])
                            assert metadata_tags["service"] == "extra-crash" and metadata_tags["runtime_id"] == ready["runtime_id"], metadata_tags
                            assert crash["error"]["kind"] == "UnixSignal" and crash["sig_info"]["si_signo"] == 11
                            crash_rows = [r for r in proxy.snapshot() if logs([r], ready)]
                            wait_for(lambda: all(any(b["raw_sha256"] == r["raw_sha256"] and b["status"] == 200 for b in backend.snapshot()) for r in crash_rows), "Agent did not forward native crash report")
                        wanted = int(ready["span_id"])
                        wait_for(lambda: any(s["span_id"] == wanted for r in backend.snapshot() if r["path"] == "/api/v0.2/traces" for chunk in trace_chunks(r["payload"]) for s in chunk["spans"]), "Crash control span not forwarded")
                        record["status"] = "passed"
                        record["detail"] = {"exitStatus": status, "crashReports": len(reports)}
                        print(name, "crashtracking passed", flush=True)
            finally:
                (out / "backend-capture.json").write_text(json.dumps(backend.snapshot(), indent=2))
                files = ["backend-capture.json", "datadog.yaml", "agent-info.json"]
                files += [str(p.relative_to(out)) for case in ("enabled", "disabled") for p in (out / case).rglob("*") if p.is_file()]
                files += ["backend/" + r["raw_file"] for r in backend.snapshot()]
                flush_results(out, "datadog-debugger-extra-crash-results.json", results, files)

if __name__ == "__main__":
    run_main(execute)
