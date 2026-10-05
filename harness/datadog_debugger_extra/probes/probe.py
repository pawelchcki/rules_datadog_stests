"""Signed RC enable/retain/disable controls through the actual pinned Agents."""
import gzip
import io
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlsplit
import zipfile
from harness.datadog_debugger_extra.common import *
from harness.datadog_debugger.backend import Handler as DebuggerHandler
from harness.datadog_backend.backend import decompress

FIXTURES = json.loads(Path(__file__).with_name("signed-fixtures.json").read_text())
FEATURES = ["debugger", "debugger_code_origins", "debugger_exception_replay", "debugger_inproduct_enablement", "debugger_symdb", "tracer_flare", "app_client_configuration_change_event"]

SOURCE_METADATA = {'debugger': {'path': 'tests/debugger/test_debugger_telemetry.py', 'sha256': '843a023e544b48040305a149f91c1f1db427296ba7aaf820fe3a12739c52f96a'}, 'debugger_code_origins': {'path': 'tests/debugger/test_debugger_code_origins.py', 'sha256': '2a8a4ca21f3af517fce4fd314a0e269b71ecd45e5e5107af689878de6b7d8f13'}, 'debugger_exception_replay': {'path': 'tests/debugger/test_debugger_exception_replay.py', 'sha256': '6f609de0aaac1a869b8b093c1636d45cedea33409d14bf6745ca4eef17802c44'}, 'debugger_inproduct_enablement': {'path': 'tests/debugger/test_debugger_inproduct_enablement.py', 'sha256': 'd50fd54a26c53726c7e695c9abf626fe16e4b747ffca14cbbe440230ce4b2a92'}, 'debugger_symdb': {'path': 'tests/debugger/test_debugger_symdb.py', 'sha256': '917dbe03476e4a370a64345ce9a3dbba4bc37ce7684c8dcaf81ce203f6d5d08b'}, 'tracer_flare': {'path': 'tests/parametric/test_tracer_flare.py', 'sha256': 'a26d8e77ef5630751ea019d4fa29104f9436b9e154d2f121930a7ad32803fafa'}, 'app_client_configuration_change_event': {'path': 'utils/_features.py', 'sha256': '79883e646ed3ddd87f5d17039b07840f85251a7a67f64be9fa433dab7a985d8b'}}

class Handler(DebuggerHandler):
    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/symdb/v1/input", "/api/v2/symdb", "/tracer_flare/v1", "/api/v2/apmflare", "/api/ui/support/serverless/flare"):
            return super().do_POST()
        assert self.headers.get("DD-API-KEY") == API_KEY
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.capture(path, {k.lower(): v for k, v in self.headers.items()}, body, 200, None)
        self.respond(200, {})

def parts(raw, headers):
    content = decompress(raw, headers.get("content-encoding", ""))
    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + content)
    assert message.is_multipart() and not message.defects
    return {p.get_param("name", header="Content-Disposition"): p.get_payload(decode=True) for p in message.iter_parts()}

def snapshots(proxy, out):
    events = []
    from harness.datadog_debugger.backend import decode_document
    for r in proxy.snapshot():
        if r["path"].startswith("/debugger/"):
            raw = (out / "tracer" / r["raw_file"]).read_bytes()
            events.extend(decode_document(decompress(raw, r["headers"].get("content-encoding", "")), r["headers"]["content-type"]))
    return events

def check_debugger(spans, events, identities):
    dynamic = [s for s in spans if s["name"] == "dd.dynamic.span" and s["meta"].get("debugger.probeid") == "extra-span"]
    assert len(dynamic) == 2, dynamic
    roots = [s for s in spans if s["meta"].get("extra.stage")]
    for stage in (1, 2, 3, 4, 5):
        normals = [s for s in roots if s["meta"]["extra.stage"] == str(stage) and s["resource"] == "GET normal"]
        assert len(normals) == 1, (stage, roots)
        assert (normals[0]["meta"].get("extra.decoration") == "signed-rc") == (stage in (2, 3)), normals
    logs = [e for e in events if e.get("debugger", {}).get("snapshot", {}).get("probe", {}).get("id") == "extra-log"]
    assert len(logs) == 2, logs
    for event in logs:
        assert event["debugger"]["snapshot"]["captures"]["return"]["arguments"]["value"]["value"] == "7", event
        assert event["debugger"]["snapshot"]["captures"]["return"]["locals"]["@return"]["value"] == "14", event

def check_code_origins(spans):
    roots = [s for s in spans if s["meta"].get("extra.stage") and s["resource"] == "GET error"]
    for stage in (1, 2, 3, 4, 5):
        match = [s for s in roots if s["meta"]["extra.stage"] == str(stage)]
        assert len(match) == 1, match
        meta = match[0]["meta"]
        assert (meta.get("_dd.code_origin.type") == "entry") == (stage in (2, 3)), meta
        if stage in (2, 3):
            assert meta["_dd.code_origin.frames.0.method"] == "error", meta
            assert meta["_dd.code_origin.frames.0.file"].endswith("workload.py"), meta
            assert int(meta["_dd.code_origin.frames.0.line"]) > 0

def check_replay(spans, events):
    errors = [s for s in spans if s["name"] == "extra.exception"]
    assert len(errors) == 5 and all(s["error"] == 1 for s in errors), errors
    captured = [s for s in errors if s["meta"].get("error.debug_info_captured") == "true"]
    assert captured, errors
    wanted = {v for s in captured for k, v in s["meta"].items() if k.startswith("_dd.debug.error.") and k.endswith(".snapshot_id")}
    replay = [e["debugger"]["snapshot"] for e in events if e.get("debugger", {}).get("snapshot", {}).get("exceptionId")]
    assert wanted and wanted <= {s["id"] for s in replay}, (wanted, replay)
    assert any("debugger-extra-error:3" in json.dumps(s) for s in replay), replay
    assert all(not s["meta"].get("error.debug_info_captured") for s in errors if s["meta"].get("error.message") in ("debugger-extra-error:2", "debugger-extra-error:5", "debugger-extra-error:6")), errors

def check_symdb(proxy, out, ready):
    uploads = [r for r in proxy.snapshot() if r["path"] == "/symdb/v1/input"]
    assert uploads and all(r["status"] == 200 for r in uploads), uploads
    found = False
    def scope_matches(scope):
        return scope.get("name", "").endswith("DebuggerController") and scope.get("scope_type") == "CLASS" and bool(scope.get("scopes")) or any(scope_matches(s) for s in scope.get("scopes", []))
    for row in uploads:
        p = parts((out / "tracer" / row["raw_file"]).read_bytes(), row["headers"])
        event = json.loads(p["event"])
        attachment = json.loads(gzip.decompress(p["file"]))
        assert event["type"] == "symdb" and event["runtimeId"] == ready["runtime_id"], event
        assert event["uploadId"] == attachment["upload_id"] and event["batchNum"] == attachment["batch_num"]
        assert event["attachmentSize"] == len(p["file"])
        found |= any(scope_matches(s) for s in attachment["scopes"])
    assert found, "Application class with nested symbol scopes not uploaded"

def check_flare(proxy, out):
    rows = [r for r in proxy.snapshot() if r["path"] == "/tracer_flare/v1"]
    assert len(rows) == 1 and rows[0]["status"] == 200, rows
    p = parts((out / "tracer" / rows[0]["raw_file"]).read_bytes(), rows[0]["headers"])
    assert p["case_id"] == b"12345", p.keys()
    archive = zipfile.ZipFile(io.BytesIO(p["flare_file"]))
    assert archive.testzip() is None
    names = archive.namelist()
    assert any("tracer_config_" in n for n in names) and any("tracer_python_" in n for n in names), names
    config = json.loads(archive.read(next(n for n in names if "tracer_config_" in n)))
    assert config["configs"]["service"] == "rc-lab", config

def check_config(proxy, ready):
    documents = [r["payload"]["document"] for r in proxy.snapshot() if r["path"] == "/telemetry/proxy/api/v2/apmtelemetry"]
    events = [e for d in documents for e in (d["payload"] if d["request_type"] == "message-batch" else [d]) if e["request_type"] == "app-client-configuration-change"]
    assert events, documents
    configs = [c for e in events for c in e["payload"]["configuration"]]
    assert any(c["name"] == "DD_TRACE_SAMPLING_RULES" and c["origin"] == "remote_config" and json.loads(c["value"]) == [{"sample_rate": 1.0}] for c in configs), configs

def check_debugger_telemetry(proxy, ready):
    documents = [r["payload"]["document"] for r in proxy.snapshot() if r["path"] == "/telemetry/proxy/api/v2/apmtelemetry"]
    started = [e for d in documents for e in (d["payload"] if d["request_type"] == "message-batch" else [d]) if e["request_type"] == "app-started"]
    assert len(started) == 1, started
    configs = {c["name"]: c for c in started[0]["payload"]["configuration"]}
    expected = {"DD_DYNAMIC_INSTRUMENTATION_ENABLED": "false", "DD_EXCEPTION_REPLAY_ENABLED": "false", "DD_SYMBOL_DATABASE_UPLOAD_ENABLED": "true", "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "true"}
    for name, value in expected.items():
        assert configs[name]["value"] == value and configs[name]["origin"] == "default", configs[name]

def startup_telemetry_received(proxy):
    return any(e["request_type"] == "app-started"
               for r in proxy.snapshot() if r["path"] == "/telemetry/proxy/api/v2/apmtelemetry"
               for d in [r["payload"]["document"]]
               for e in (d["payload"] if d["request_type"] == "message-batch" else [d]))

def execute(args, out):
    results = []
    with backend_context(out, Handler, FIXTURES, ["7-83-1-flare.datadoghq.com:443"]) as (backend, relay, url):
        apm, health, cmd = port(), port(), port()
        config = agent_config(out, url, relay.server_port, FIXTURES, apm, health, cmd, telemetry=True)
        config["apm_config"].update(symdb_dd_url=url + "/symdb/v1/input")
        config["apm_config"]["telemetry"]["dd_url"] = url
        with agents_running(args, config, out, health, relay.server_port) as (_, clean):
            proxy = CaptureProxy("http://127.0.0.1:" + str(apm), out / "tracer")
            with server_thread(proxy):
                env = dict(BASE_ENV)
                env.pop("DD_TRACE_SAMPLING_RULES")
                # Retain the normal native telemetry heartbeat. A 0.2-second
                # heartbeat invokes the SDK's fallback app_started() while its
                # product loader is still importing startup configuration.
                # ProductManager emits app-started itself once loading finishes.
                env.update(DD_SERVICE="rc-lab", DD_ENV="rc-env", DD_VERSION="extra-version", DD_TRACE_API_VERSION="v0.4", DD_REMOTE_CONFIGURATION_ENABLED="true", DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.2", DD_INSTRUMENTATION_TELEMETRY_ENABLED="true", DD_DYNAMIC_INSTRUMENTATION_UPLOAD_INTERVAL_SECONDS="0.2", DD_SYMBOL_DATABASE_INCLUDES="target", DD_TRACE_AGENT_URL="http://127.0.0.1:" + str(proxy.server_port))
                try:
                    with process(launch_command(args, env, out), out / "app.log", clean) as sdk:
                        ready = wait_for(lambda: json.loads((out / "ready.json").read_text()) if (out / "ready.json").exists() else None, "SDK startup missing")
                        wait_for(lambda: any("APM_TRACING" in r["client"]["products"] for r in rc_requests(proxy, out / "tracer")), "SDK remote products not registered")
                        assert ready["tracer_version"] == "4.14.0", ready
                        results = [receipt(f, env, ready, "Signed RC through real core/trace Agents; enable, retained empty config, disable and native outputs", ["Other language and advanced upstream cases"], SOURCE_METADATA[f]["path"], SOURCE_METADATA[f]["sha256"]) for f in FEATURES]
                        # Validate the actual startup defaults while the backend
                        # still serves the empty stage. Later RC configuration
                        # change events cannot satisfy this assertion.
                        wait_for(lambda: startup_telemetry_received(proxy), "SDK startup telemetry missing")
                        check_debugger_telemetry(proxy, ready)
                        for stage in (1, 2, 3, 4, 5):
                            rc_version = [2, 4, 5, 6, 7][stage - 1]
                            if stage == 2:
                                backend.rc_stage = 3
                                wait_for(lambda: any("LIVE_DEBUGGING" in r["client"]["products"] and r["client"]["state"]["targets_version"] == 3 for r in rc_requests(proxy, out / "tracer")), "Dynamic Instrumentation product not remotely enabled")
                            backend.rc_stage = rc_version
                            wait_for(lambda: any(r["client"]["state"]["targets_version"] == rc_version and any(c["product"] == "APM_TRACING" and c["version"] == rc_version and c["apply_state"] == 2 for c in r["client"]["state"].get("config_states", [])) for r in rc_requests(proxy, out / "tracer")), "RC stage not acknowledged " + str(stage))
                            if stage == 2:
                                wait_for(lambda: {"extra-span", "extra-decoration", "extra-log"} <= {e["debugger"]["diagnostics"]["probeId"] for e in snapshots(proxy, out) if e.get("debugger", {}).get("diagnostics", {}).get("status") == "INSTALLED"}, "Enabled probes not installed")
                            time.sleep(1)
                            (out / (str(stage) + ".command")).touch()
                            wait_for(lambda: (out / (str(stage) + ".identity.json")).exists(), "Workload stage missing " + str(stage))
                        assert sdk.wait(timeout=20) == 0, (out / "app.log").read_text()
                    spans = tracer_spans(proxy, out / "tracer", "/v0.4/traces")
                    events = snapshots(proxy, out)
                    (out / "native-spans.json").write_text(json.dumps(spans, indent=2))
                    (out / "events.json").write_text(json.dumps(events, indent=2))
                    identities = [i for stage in (1,2,3,4,5) for i in json.loads((out / (str(stage) + ".identity.json")).read_text())]
                    wanted = {int(i["span_id"]) for i in identities}
                    forwarded = wait_for(lambda: [s for r in backend.snapshot() if r["path"] == "/api/v0.2/traces" for chunk in trace_chunks(r["payload"]) for s in chunk["spans"] if s["span_id"] in wanted], "Agent spans missing")
                    assert wanted <= {s["span_id"] for s in forwarded}, (wanted, forwarded)
                    for row in proxy.snapshot():
                        if row["path"].startswith("/debugger/") or row["path"] in ("/symdb/v1/input", "/tracer_flare/v1", "/telemetry/proxy/api/v2/apmtelemetry"):
                            assert row["status"] == 200, row
                            assert any(r["raw_sha256"] == row["raw_sha256"] and r["status"] == 200 for r in backend.snapshot()), (row, backend.snapshot())
                    checks = [lambda: (check_debugger(spans, events, identities), check_debugger_telemetry(proxy, ready)), lambda: check_code_origins(spans), lambda: check_replay(spans, events), lambda: (check_debugger(spans, events, identities), check_code_origins(spans)), lambda: check_symdb(proxy, out, ready), lambda: check_flare(proxy, out), lambda: check_config(proxy, ready)]
                    for record, check in zip(results, checks):
                        try:
                            check()
                            record["status"] = "passed"
                            print(record["name"], "passed", flush=True)
                        except Exception as exc:
                            record["detail"] = repr(exc)
                            raise
                finally:
                    (out / "backend-capture.json").write_text(json.dumps(backend.snapshot(), indent=2))
                    (out / "signed-fixtures.json").write_text(json.dumps(FIXTURES, indent=2))
                    flush_results(out, "datadog-debugger-extra-probes-results.json", results, artifact_files(out, proxy, backend, ["backend-capture.json", "signed-fixtures.json", "native-spans.json", "events.json", "ready.json", "datadog.yaml", "agent-info.json"] + [str(s) + ".identity.json" for s in (1,2,3,4,5)]))

if __name__ == "__main__":
    run_main(execute)
