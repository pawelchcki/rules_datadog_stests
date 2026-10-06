"""Capture genuine pinned Python security behavior with independent HTTP controls."""
import argparse
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from harness.datadog_backend.wire import msgpack

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
ENV = {
    "DD_SERVICE": "security-extra", "DD_ENV": "test", "DD_VERSION": "1",
    "DD_TRACE_ENABLED": "true", "DD_APPSEC_ENABLED": "true", "DD_IAST_ENABLED": "true",
    "DD_IAST_REQUEST_SAMPLING": "100", "DD_IAST_DEDUPLICATION_ENABLED": "false",
    "DD_IAST_VULNERABILITIES_PER_REQUEST": "10", "DD_IAST_MAX_CONCURRENT_REQUESTS": "10",
    "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]', "DD_TRACE_RATE_LIMIT": "-1",
    "DD_TRACE_API_VERSION": "v0.4", "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.05",
    "DD_TRACE_HEADER_TAGS": "x-security-marker:security.marker",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true", "DD_TELEMETRY_HEARTBEAT_INTERVAL": "0.2",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false", "DD_RUNTIME_METRICS_ENABLED": "false",
    "DD_PROFILING_ENABLED": "false", "DD_IAST_PATCH_MODULES": "security_extra_views,security_controls",
    "DD_IAST_REDACTION_ENABLED": "false", "DD_TRACE_PROPAGATION_STYLE_EXTRACT": "datadog,tracecontext",
    "DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog,tracecontext", "DD_TRACE_STARTUP_LOGS": "false",
}
PROFILES = ["default", "tagging", "large", "events", "identified", "anonymized", "disabled", "rate", "standalone", "controls", "rasp", "sampling", "renaming", "ip-custom", "ip-disabled", "ip-precedence", "onboarding"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Intake(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.records = []
        self.lock = threading.Lock()
        self.rc_responses = []
        self.rc_stage = 0

    def snapshot(self):
        with self.lock:
            return list(self.records)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, body, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/info":
            self.respond(json.dumps({"version": "7.83.1", "endpoints": ["/v0.4/traces", "/telemetry/proxy/api/v2/apmtelemetry", "/v0.7/config"]}).encode())
        else:
            self.record_and_respond()

    def record_and_respond(self):
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 <= length <= 32 * 1024 * 1024
        body = self.rfile.read(length)
        assert len(body) == length
        with self.server.lock:
            record = {"path": urlsplit(self.path).path, "method": self.command,
                "headers": dict(self.headers), "body": base64.b64encode(body).decode(), "sha256": sha(body)}
            self.server.records.append(record)
        if self.path == "/v0.7/config":
            response = self.server.rc_responses[self.server.rc_stage] if self.server.rc_responses else {}
            raw = json.dumps(response, separators=(",", ":")).encode()
            with self.server.lock:
                record.update(response=base64.b64encode(raw).decode(), responseSha256=sha(raw))
            self.respond(raw)
        elif self.path.startswith("/mirror/"):
            status = int(urlsplit(self.path).path.rsplit("/", 1)[1])
            payload = json.loads(body) if body else None
            query = parse_qs(urlsplit(self.path).query)
            self.respond(json.dumps({"status": "OK", "payload": payload}).encode(), status,
                         {"echo-headers": query["echo-headers"][0]} if "echo-headers" in query else {})
        else:
            self.respond(b'{"rate_by_service":{}}')

    do_POST = record_and_respond
    do_PUT = record_and_respond
    do_TRACE = record_and_respond


def decoded(record):
    raw = base64.b64decode(record["body"], validate=True)
    assert sha(raw) == record["sha256"]
    headers = {key.lower(): value for key, value in record["headers"].items()}
    if headers.get("content-encoding") == "gzip":
        raw = gzip.decompress(raw)
    return msgpack(raw) if record["path"] == "/v0.4/traces" else json.loads(raw)


def native_spans(records):
    for record in records:
        if record["path"] == "/v0.4/traces":
            headers = {k.lower(): v for k, v in record["headers"].items()}
            assert headers["datadog-meta-tracer-version"] == "4.15.5"
            assert headers["datadog-meta-lang"] == "python"
    return [span for record in records if record["path"] == "/v0.4/traces" for chunk in decoded(record) for span in chunk]


def structured(root, name):
    tag = root.get("meta", {}).get("_dd." + name + ".json")
    if tag:
        return json.loads(tag)
    blob = root.get("meta_struct", {})[name]
    assert isinstance(blob, dict) and "base64" in blob, root
    return msgpack(base64.b64decode(blob["base64"], validate=True))


def event(root):
    return bool(root.get("meta", {}).get("_dd.appsec.json") or root.get("meta_struct", {}).get("appsec"))


def vulnerabilities(root):
    if not (root.get("meta", {}).get("_dd.iast.json") or root.get("meta_struct", {}).get("iast")):
        return []
    return structured(root, "iast")["vulnerabilities"]


def telemetry_series(records):
    events = []
    for record in records:
        if "apmtelemetry" in record["path"]:
            message = decoded(record)
            events.extend(message["payload"] if message.get("request_type") == "message-batch" else [message])
    return [series for e in events if e.get("request_type") == "generate-metrics" for series in e["payload"].get("series", [])]


def check_waf(rows, records, log):
    safe = rows["waf-safe"]["root"]
    assert not event(safe), safe
    for marker, rule in [("scanner", "ua0-600-12x"), ("lfi", "crs-930-120"), ("sqli", "crs-942-160")]:
        root = rows[marker]["root"]
        triggers = structured(root, "appsec")["triggers"]
        assert any(t["rule"]["id"] == rule for t in triggers), (rule, triggers)
        assert root["metrics"]["_dd.appsec.enabled"] == 1 and root["metrics"]["_dd.appsec.waf.duration"] >= 0
    return ["waf_rules"]


def check_obfuscation(rows, records, log):
    root = rows["obfuscation"]["root"]
    triggers = structured(root, "appsec")["triggers"]
    params = [p for t in triggers for m in t["rule_matches"] for p in m["parameters"]]
    assert any(p["address"] == "server.request.query" and p["key_path"] == ["pwd"] and p["value"] == "<Redacted>" for p in params), params
    assert "controlled-secret-value" not in json.dumps(triggers)
    plain = structured(rows["obfuscation-control"]["root"], "appsec")
    assert "controlled-secret-value" in json.dumps(plain), plain
    return ["sensitive_data_obfuscation"]


def check_cipher(rows, records, log):
    assert rows["cipher"]["body"]["outcome"] and rows["cipher-safe"]["body"]["outcome"]
    assert not any(v["type"] == "WEAK_CIPHER" for v in vulnerabilities(rows["cipher-safe"]["root"]))
    found = [v for v in vulnerabilities(rows["cipher"]["root"]) if v["type"] == "WEAK_CIPHER"]
    assert len(found) == 1 and found[0]["evidence"]["value"] == "RC4", found
    assert found[0]["location"]["path"].endswith("security_extra_views.py") and found[0]["location"]["line"] > 0
    return ["weak_cipher_detection"]


def check_identify(rows, records, log):
    for marker in ("identify", "identify-attack", "propagate"):
        tags = rows[marker]["root"]["meta"]
        for field in ("id", "email", "name", "session_id", "role", "scope"):
            assert tags["usr." + field] == "usr." + field, tags
    assert rows["propagate"]["root"]["meta"]["_dd.p.usr.id"] == "dXNyLmlk"
    outgoing = [r for r in records if r["path"] == "/witness"]
    assert len(outgoing) == 1, outgoing
    headers = {k.lower(): v for k, v in outgoing[0]["headers"].items()}
    assert "_dd.p.usr.id=dXNyLmlk" in headers["x-datadog-tags"] and "t.usr.id:dXNyLmlk" in headers["tracestate"]
    tags = rows["incoming"]["root"]["meta"]
    assert tags["_dd.p.usr.id"] == "dXNyLmlk" and "usr.id" not in tags
    return ["propagation_of_user_id_rfc"]


def check_circular(rows, records, log):
    assert rows["circular"]["body"]["outcome"] == "circular-b:circular-a"
    assert "most likely due to a circular import" not in log and "ImportError" not in log
    return ["language_specifics"]


def check_shell(rows, records, log):
    all_spans = native_spans(records)
    for marker, field in [("shell-exec", "cmd.exec"), ("shell-shell", "cmd.shell")]:
        root = rows[marker]["root"]
        children = [s for s in all_spans if s["trace_id"] == root["trace_id"] and s["name"] == "command_execution" and s.get("meta", {}).get("component") == "subprocess"]
        assert children, children
        child = children[0]
        assert child["type"] == "system" and child["meta"]["component"] == "subprocess"
        assert child["meta"]["cmd.exit_code"] == "0"
        index = {s["span_id"]: s for s in all_spans if s["trace_id"] == root["trace_id"]}
        ancestor = child
        while ancestor["span_id"] != root["span_id"]:
            ancestor = index[ancestor["parent_id"]]
        assert field in child["meta"], child
        assert "witness" in rows[marker]["body"]["outcome"]
    command = next(s for s in all_spans if s["trace_id"] == rows["shell-exec"]["root"]["trace_id"] and s["name"] == "command_execution" and "cmd.exec" in s.get("meta", {}))
    assert "controlled-command-secret" not in command["meta"]["cmd.exec"] and "'?'" in command["meta"]["cmd.exec"], command
    return ["appsec_shell_execution_tracing"]


def check_truncation(rows, records, log):
    metrics = rows["truncation"]["root"]["metrics"]
    assert metrics["_dd.appsec.truncated.string_length"] == 5000
    assert metrics["_dd.appsec.truncated.container_size"] == 300
    assert 20 <= metrics["_dd.appsec.truncated.container_depth"] <= 28
    assert not any(k.startswith("_dd.appsec.truncated.") for k in rows["truncation-safe"]["root"]["metrics"])
    series = telemetry_series(records)
    assert any(s["metric"] == "waf.requests" and "input_truncated:true" in s.get("tags", []) for s in series), series
    assert any(s["metric"] == "waf.input_truncated" and s["type"] == "count" and "truncation_reason:7" in s.get("tags", []) for s in series), series
    return ["appsec_truncation_action"]


def check_tagging(rows, records, log):
    for version, integer, keep, report in [(1, 662607015, False, False), (2, 602214076, True, False), (3, 299792458, True, True), (4, 1729, False, True)]:
        root = rows["tagging-" + str(version)]["root"]
        assert root["meta"]["_dd.appsec.trace.agent"] == "TraceTagging/v" + str(version)
        assert root["metrics"]["_dd.appsec.trace.integer"] == integer
        assert (root["metrics"]["_sampling_priority_v1"] == 2) == keep, root
        assert event(root) == report, root
        if report:
            assert any(t["rule"]["id"] == "ttr-000-00" + str(version) for t in structured(root, "appsec")["triggers"])
    assert "_dd.appsec.trace.agent" not in rows["tagging-safe"]["root"]["meta"]
    return ["appsec_trace_tagging_rules"]


def check_large(rows, records, log):
    for marker, pattern in [("large-first", "first_pattern_of_a_very_long_list"), ("large-last", "last_pattern_of_a_very_long_list")]:
        report = structured(rows[marker]["root"], "appsec")
        assert pattern in json.dumps(report), report
    assert not event(rows["large-safe"]["root"])
    return ["serialize_waf_rules_without_limiting_their_sizes"]


def check_events(rows, records, log):
    for marker, outcome in [("event-success", "success"), ("event-failure", "failure")]:
        tags = rows[marker]["root"]["meta"]
        prefix = "appsec.events.users.login." + outcome
        for field, value in [("usr.login", "login_safe"), ("usr.id", "user_id_safe"), ("track", "true"), ("metadata0", "value0"), ("metadata_number", "123"), ("metadata_boolean", "true")]:
            assert tags[prefix + "." + field] == value, tags
        assert tags["_dd." + prefix + ".sdk"] == "true" and tags["_dd.appsec.user.collection_mode"] == "sdk"
        if outcome == "success":
            assert tags["usr.id"] == "user_id_safe"
        else:
            assert tags[prefix + ".usr.exists"] == "true" and "usr.id" not in tags
        ids = {t["rule"]["id"] for t in structured(rows[marker]["root"], "appsec")["triggers"]}
        assert ("003_trigger_on_login_success" if outcome == "success" else "004_trigger_on_login_failure") in ids, ids
    assert not event(rows["event-control"]["root"])
    unsafe = structured(rows["event-unsafe"]["root"], "appsec")
    assert "002_trigger_on_usr_id" in {t["rule"]["id"] for t in unsafe["triggers"]}
    series = telemetry_series(records)
    assert any(s["metric"] == "sdk.event" and "sdk_version:v2" in s.get("tags", []) for s in series), series
    return ["event_tracking_sdk_v2"]


def check_mode(rows, records, log, mode):
    good = rows["mode-success"]["root"]["meta"]
    bad = rows["mode-failure"]["root"]["meta"]
    if mode == "disabled":
        assert not any(k.startswith("appsec.events.users.login.") for k in good)
        assert not any(k.startswith("appsec.events.users.login.") for k in bad)
    else:
        for tags, outcome in [(good, "success"), (bad, "failure")]:
            prefix = "appsec.events.users.login." + outcome
            assert tags[prefix + ".track"] == "true" and tags["_dd.appsec.user.collection_mode"] == {"identified": "identification", "anonymized": "anonymization"}[mode], tags
            expected = "test" if mode == "identified" else "anon_" + sha(b"test")[:32]
            assert tags[prefix + ".usr.login"] == expected, tags
        assert "usr.id" in good and "usr.id" not in bad, (good, bad)
        assert good["usr.id"] == ("1" if mode == "identified" else "anon_" + sha(b"1")[:32]), good
    return ["user_id_collection_modes"]


def check_rate(rows, records, log):
    attacked = [rows["rate-" + str(i)]["root"] for i in range(20)]
    count = sum(root["metrics"]["_sampling_priority_v1"] == 2 for root in attacked)
    assert all(event(root) for root in attacked)
    assert 1 <= count <= 6, count
    assert not event(rows["rate-safe"]["root"])
    assert all(root["metrics"]["_dd.appsec.enabled"] == 1 for root in attacked)
    return ["appsec_rate_limiter"]


def check_standalone(rows, records, log):
    root = rows["standalone"]["root"]
    assert event(root) and root["metrics"]["_sampling_priority_v1"] == 2
    assert root["meta"]["_dd.p.dm"] == "-5" and root["metrics"]["_dd.apm.enabled"] == 0, root
    assert not event(rows["standalone-safe"]["root"])
    return ["appsec_apm_standalone"]


def check_controls(rows, records, log):
    for mode in ("raw", "different"):
        vulns = vulnerabilities(rows["controls-" + mode]["root"])
        assert len([v for v in vulns if v["type"] == "COMMAND_INJECTION"]) == 1, vulns
    for mode in ("sanitize", "validate"):
        root = rows["controls-" + mode]["root"]
        assert root["metrics"]["_dd.iast.enabled"] == 1
        assert not any(v["type"] == "COMMAND_INJECTION" for v in vulnerabilities(root)), root
        assert root["metrics"]["_dd.iast.telemetry.suppressed.vulnerabilities.command_injection"] > 0, root
    assert not vulnerabilities(rows["controls-safe"]["root"])
    return ["iast_security_controls"]


def check_api10(rows, records, log):
    names = ["req_headers", "req_method", "req_body", "res_status", "res_headers", "res_body"]
    for name in names:
        root = rows["api10-" + name]["root"]
        assert root["meta"]["_dd.appsec.trace." + name] == "TAG_API10_" + name.upper(), root
        assert all("_dd.appsec.trace." + other not in root["meta"] for other in names if other != name), root
        assert rows["api10-" + name]["body"]["outcome"]["status"] == (201 if name == "res_status" else 200)
    assert not any(k.startswith("_dd.appsec.trace.") for k in rows["api10-safe"]["root"]["meta"])
    assert len([r for r in records if r["path"].startswith("/mirror/")]) == 7
    return ["api10"]


def rc_requests(records):
    return [decoded(r) for r in records if r["path"] == "/v0.7/config"]


def check_onboarding(rows, records, log):
    for marker in ("rc-baseline", "rc-disabled", "rc-removed"):
        root = rows[marker]["root"]
        assert not event(root) and root.get("metrics", {}).get("_dd.appsec.enabled") != 1, root
    root = rows["rc-enabled"]["root"]
    assert event(root) and root["metrics"]["_dd.appsec.enabled"] == 1
    assert any(t["rule"]["id"] == "ua0-600-12x" for t in structured(root, "appsec")["triggers"])
    requests = rc_requests(records)
    assert requests and all(r["client"]["client_tracer"]["tracer_version"] == "4.15.5" for r in requests)
    assert any("ASM_FEATURES" in r["client"]["products"] for r in requests)
    path = "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config"
    for version, config_version, size, digest in [(2, 1, 47, "9221dfd9f6084151313e3e4920121ae843614c328e4630ea371ba66e2f15a0a6"),
                                                 (3, 2, 48, "a38ebf9fa256071f9823a5f512a84e8a786c8da2b0719452deeb5dc287ec990f")]:
        observed = [r for r in requests if r["client"]["state"]["targets_version"] == version and any(c["product"] == "ASM_FEATURES" and c["version"] == config_version and c["apply_state"] == 2 for c in r["client"]["state"].get("config_states", []))]
        assert observed, (version, requests)
        assert any(f["path"] == path and f["length"] == size and {"algorithm": "sha256", "hash": digest} in f["hashes"] for r in observed for f in r.get("cached_target_files", [])), observed
    removed = [r for r in requests if r["client"]["state"]["targets_version"] == 4]
    assert removed and all(not r["client"]["state"].get("config_states") and not r.get("cached_target_files") for r in removed), removed
    for record in records:
        if record["path"] == "/v0.7/config":
            raw = base64.b64decode(record["response"], validate=True)
            assert sha(raw) == record["responseSha256"]
            response = json.loads(raw)
            targets = json.loads(base64.b64decode(response["targets"]))
            assert targets["signatures"] and targets["signed"]["_type"] == "targets"
            for f in response.get("target_files", []):
                document = base64.b64decode(f["raw"], validate=True)
                metadata = targets["signed"]["targets"][f["path"]]
                assert metadata["hashes"]["sha256"] == sha(document) and metadata["length"] == len(document)
    enabled = [r for r in requests if r["client"]["state"]["targets_version"] in (2, 5, 6)]
    assert enabled and all(int.from_bytes(bytes(r["client"]["capabilities"]), "big") & (1 << 43) for r in enabled), enabled
    sixth = [r for r in requests if r["client"]["state"]["targets_version"] == 6]
    assert any({"ASM_FEATURES", "ASM_DD"} <= {c["product"] for c in r["client"]["state"].get("config_states", []) if c["apply_state"] == 2} for r in sixth), sixth
    check_tagging(rows, records, log)
    return ["appsec_onboarding", "appsec_trace_tagging_rules"]


IP_HEADERS = [("x-forwarded-for", "5.6.7.0"), ("x-real-ip", "8.7.6.5"), ("true-client-ip", "5.6.7.2"),
              ("x-client-ip", "5.6.7.3"), ("forwarded-for", "5.6.7.5"), ("x-cluster-client-ip", "5.6.7.6"),
              ("fastly-client-ip", "5.6.7.7"), ("cf-connecting-ip", "5.6.7.8"), ("cf-connecting-ipv6", "0:2:3:4:5:6:7:8")]


def check_ip(rows, records, log, profile):
    if profile == "ip-custom":
        assert rows["ip"]["root"]["meta"]["http.client_ip"] == "5.6.7.9"
    elif profile == "ip-disabled":
        assert "http.client_ip" not in rows["ip"]["root"]["meta"]
    else:
        for i, (_, value) in enumerate(IP_HEADERS):
            assert rows["ip-" + str(i)]["root"]["meta"]["http.client_ip"] == value
    return ["trace_client_ip_header"]


def check_sampling(rows, records, log):
    groups = {"get": set(), "post": set(), "second": set()}
    for group in groups:
        for i in range(10):
            root = rows["sampling-" + group + "-" + str(i)]["root"]
            found = [v for v in vulnerabilities(root) if v["type"] == "COMMAND_INJECTION"]
            assert len(found) < 15
            assert len(found) <= 2
            groups[group].update(v["hash"] for v in found)
            assert root["metrics"]["_dd.iast.enabled"] == 1
    assert all(len(hashes) == 15 for hashes in groups.values()), groups
    assert len(set.union(*groups.values())) == 45, groups
    assert not vulnerabilities(rows["sampling-safe"]["root"])
    return ["iast_vuln_sampling_route_method_count_algorithm"]


def check_renaming(rows, records, log):
    expected = {"static": "/resource_renaming/some/url", "int": "/resource_renaming/int/{param:int}",
                "int_id": "/resource_renaming/int_id/{param:int_id}", "hex": "/resource_renaming/hex/{param:hex}",
                "hex_id": "/resource_renaming/hex_id/{param:hex_id}", "str": "/resource_renaming/files/{param:str}"}
    for name, endpoint in expected.items():
        root = rows["renaming-" + name]["root"]
        assert root["meta"]["http.endpoint"] == endpoint, root
    return ["resource_renaming"]


def fetch(url, data=None, headers=None, method=None):
    try:
        with urlopen(Request(url, data=data, headers=headers or {}, method=method), timeout=10) as response:
            return response.status, response.read()
    except HTTPError as response:
        return response.code, response.read()


def resolve(path):
    for candidate in [Path(path)] + [Path(root) / path for root in (os.environ.get("RUNFILES_DIR"), os.environ.get("TEST_SRCDIR")) if root]:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError(path)


def run(args):
    output = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for profile in ([args.profile] if args.profile else PROFILES):
        out = output / profile
        out.mkdir(parents=True, exist_ok=True)
        for source in Path(args.app).parent.iterdir():
            if source.is_file() and source.suffix in (".py", ".json"):
                shutil.copyfile(source, out / source.name)
        server = Intake()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = dict(ENV, DD_TRACE_AGENT_URL="http://127.0.0.1:" + str(server.server_port),
                   SECURITY_EXTRA_DOWNSTREAM="http://127.0.0.1:" + str(server.server_port),
                   SECURITY_EXTRA_VENDOR=args.sdk_overlay, SECURITY_EXTRA_STATE=str(out))
        if profile not in ("default", "controls", "sampling"):
            env["DD_IAST_ENABLED"] = "false"
        if profile == "onboarding":
            env.pop("DD_APPSEC_ENABLED")
            env.update(DD_REMOTE_CONFIGURATION_ENABLED="true", DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.2")
            server.rc_responses = json.loads((out / "onboarding-tagging-responses.json").read_text())
            env.pop("DD_TRACE_SAMPLING_RULES")
        if profile in ("tagging", "rate"):
            env.pop("DD_TRACE_SAMPLING_RULES")
        if profile in ("tagging", "large", "events", "rasp"):
            env["DD_APPSEC_RULES"] = str(out / {"tagging": "tagging-rules.json", "large": "large-rules.json", "events": "ato-sdk-rules.json", "rasp": "rasp-ruleset.json"}[profile])
        if profile == "rasp":
            env["DD_APPSEC_RASP_ENABLED"] = "true"
        if profile in ("identified", "anonymized", "disabled"):
            env["DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE"] = {"identified": "identification", "anonymized": "anonymization", "disabled": "disabled"}[profile]
        if profile.startswith("ip-"):
            env.update(DD_APPSEC_ENABLED="false", DD_TRACE_CLIENT_IP_ENABLED="true")
            if profile in ("ip-custom", "ip-disabled"):
                env["DD_TRACE_CLIENT_IP_HEADER"] = "custom-ip-header"
            if profile == "ip-disabled":
                env["DD_TRACE_CLIENT_IP_ENABLED"] = "false"
        if profile == "sampling":
            env["DD_IAST_VULNERABILITIES_PER_REQUEST"] = "2"
        if profile == "renaming":
            env["DD_TRACE_RESOURCE_RENAMING_ENABLED"] = "true"
            env["DD_TRACE_RESOURCE_RENAMING_ALWAYS_SIMPLIFIED_ENDPOINT"] = "true"
        if profile == "rate":
            env["DD_APPSEC_TRACE_RATE_LIMIT"] = "1"
        if profile == "standalone":
            env["DD_APM_TRACING_ENABLED"] = "false"
        if profile == "controls":
            env["DD_IAST_SECURITY_CONTROLS_CONFIGURATION"] = "SANITIZER:COMMAND_INJECTION:security_controls:sanitize;INPUT_VALIDATOR:COMMAND_INJECTION:security_controls:validate:0;SANITIZER:SQL_INJECTION:security_controls:different"
        ready = out / "app.port"
        ready.unlink(missing_ok=True)
        launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags + ["--instance=security-extra"]
        launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        launch += ["--", str(out / "app.py"), "--ready-file", str(ready)]
        process_env = {k: v for k, v in os.environ.items() if not k.startswith(("DD_", "OTEL_")) and k != "PYTHONOPTIMIZE"}
        rows = {}
        proc = None
        try:
            with (out / "app.log").open("wb") as log:
                proc = subprocess.Popen(launch, stdout=log, stderr=log, cwd=out, env=process_env, start_new_session=True)
            deadline = time.monotonic() + 60
            while not ready.exists():
                assert proc.poll() is None, (out / "app.log").read_text(errors="replace")
                assert time.monotonic() < deadline, "App readiness timeout"
                time.sleep(0.05)
            base = "http://127.0.0.1:" + ready.read_text().strip()

            def request(marker, path="/x/waf", query=None, headers=None, payload=None, form=None, method=None, status=200):
                hdr = {"X-Security-Marker": marker, **(headers or {})}
                data = None
                if payload is not None:
                    data = json.dumps(payload).encode()
                    hdr["Content-Type"] = "application/json"
                if form is not None:
                    data = urlencode(form).encode()
                    hdr["Content-Type"] = "application/x-www-form-urlencoded"
                code, body = fetch(base + path + ("?" + urlencode(query) if query else ""), data, hdr, method)
                (out / (marker + ".response.json")).write_bytes(body)
                assert code == status, (marker, code, body)
                identity = json.loads(body)
                for _ in range(200):
                    roots = [s for s in native_spans(server.snapshot()) if s.get("meta", {}).get("security.marker") == marker and s["span_id"] == int(identity["span_id"])]
                    if roots:
                        assert len(roots) == 1 and roots[0]["trace_id"] == int(identity["trace_id"]) & ((1 << 64) - 1)
                        rows[marker] = {"root": roots[0], "body": identity}
                        return
                    time.sleep(0.03)
                raise AssertionError("Missing native request root: " + marker)

            if profile == "default":
                request("waf-safe")
                request("scanner", headers={"User-Agent": "Arachni/v1"})
                request("lfi", query={"attack": "/.htaccess"})
                request("sqli", query={"attack": "select pg_sleep(10)"})
                request("obfuscation", query={"pwd": "controlled-secret-value select pg_sleep(10)"})
                request("obfuscation-control", query={"public": "controlled-secret-value select pg_sleep(10)"})
                request("cipher-safe", "/x/weak-cipher-safe")
                request("cipher", "/x/weak-cipher")
                request("identify", "/x/identify")
                request("identify-attack", "/x/identify", headers={"User-Agent": "Arachni/v1"})
                request("propagate", "/x/identify-propagate")
                request("incoming", headers={"x-datadog-trace-id": "1", "x-datadog-parent-id": "1", "x-datadog-tags": "_dd.p.usr.id=dXNyLmlk"})
                request("circular", "/x/circular")
                request("shell-exec", "/x/shell", query={"input": "controlled-command-secret"})
                request("shell-shell", "/x/shell", query={"mode": "shell"})
                request("truncation-safe", payload={"key": "value"})
                deep = {"value": "a"}
                for _ in range(25):
                    deep = {"a": deep}
                request("truncation", payload={"deepObject": deep, "longValue": "testattack" * 500, "largeObject": {"key" + str(i): "value" + str(i) for i in range(300)}})
            elif profile == "tagging":
                request("tagging-safe")
                for version in range(1, 5):
                    request("tagging-" + str(version), headers={"User-Agent": "TraceTagging/v" + str(version)})
            elif profile == "large":
                request("large-safe", headers={"attack": "benign"})
                request("large-first", headers={"attack": "first_pattern_of_a_very_long_list"})
                request("large-last", headers={"attack": "last_pattern_of_a_very_long_list"})
            elif profile == "events":
                request("event-control")
                payload = {"login": "login_safe", "user_id": "user_id_safe", "metadata": {"metadata0": "value0", "metadata_number": 123, "metadata_boolean": True}}
                request("event-success", "/x/track-success-v2", payload=payload)
                request("event-failure", "/x/track-failure-v2", payload=payload)
                request("event-unsafe", "/x/track-success-v2", payload={"login": "login_unsafe", "user_id": "user_id_unsafe"})
            elif profile in ("identified", "anonymized", "disabled"):
                request("mode-success", "/x/login", form={"username": "test", "password": "1234"})
                request("mode-failure", "/x/login", form={"username": "test", "password": "wrong"}, status=401)
            elif profile == "rate":
                request("rate-safe")
                started = time.monotonic()
                for i in range(20):
                    request("rate-" + str(i), headers={"User-Agent": "Arachni/v1"})
                assert time.monotonic() - started < 5, "Rate workload too slow for 1/sec bound"
            elif profile == "standalone":
                request("standalone-safe")
                request("standalone", headers={"User-Agent": "Arachni/v1"})
            elif profile == "controls":
                request("controls-safe", "/x/controls")
                for mode in ("raw", "different", "sanitize", "validate"):
                    request("controls-" + mode, "/x/controls", query={"param": "controlled-tainted-input", "mode": mode})
            elif profile == "rasp":
                request("api10-safe", "/x/api10")
                request("api10-req_headers", "/x/api10", query={"Witness": "pwq3ojtropiw3hjtowir"})
                request("api10-req_method", "/x/api10", method="TRACE")
                request("api10-req_body", "/x/api10", payload={"payload_in": "qw2jedrkjerbgol23ewpfirj2qw3or"})
                request("api10-res_status", "/x/api10", query={"status": "201"})
                request("api10-res_headers", "/x/api10", query={"url_extra": "?echo-headers=qwoierj12l3"})
                request("api10-res_body", "/x/api10", payload={"payload_out": "kqehf09123r4lnksef"})
            elif profile == "sampling":
                request("sampling-safe", "/x/controls")
                for i in range(10):
                    request("sampling-get-" + str(i), "/x/sampling/" + str(i), query={"param": "value" + str(i)})
                    request("sampling-post-" + str(i), "/x/sampling/" + str(i), form={"param": "value" + str(i)})
                    request("sampling-second-" + str(i), "/x/sampling-second/" + str(i), query={"param": "value" + str(i)})
            elif profile == "renaming":
                for name, path in [("static", "some/url"), ("int", "int/123"), ("int_id", "int_id/123-456.678"), ("hex", "hex/abc123"), ("hex_id", "hex_id/abc123-abc123"), ("str", "files/very-long-filename-with-special-chars")]:
                    request("renaming-" + name, "/resource_renaming/" + path)
            elif profile.startswith("ip-"):
                if profile == "ip-precedence":
                    for i in range(len(IP_HEADERS)):
                        request("ip-" + str(i), headers=dict(IP_HEADERS[i:]))
                else:
                    request("ip", headers={"custom-ip-header": "5.6.7.9", "X-Forwarded-For": "1.2.3.4"})
            elif profile == "onboarding":
                def wait_rc(version):
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        requests = rc_requests(server.snapshot())
                        if any(r["client"]["state"].get("targets_version") == version and
                               (version in (1, 4) or {"ASM_FEATURES", *(["ASM_DD"] if version == 6 else [])} <= {c.get("product") for c in r["client"]["state"].get("config_states", []) if c.get("apply_state") == 2}) for r in requests):
                            return
                        assert proc.poll() is None, (out / "app.log").read_text(errors="replace")
                        time.sleep(0.1)
                    raise AssertionError("Missing ASM_FEATURES acknowledgement: " + str(version))
                for stage, marker in enumerate(("rc-baseline", "rc-enabled", "rc-disabled", "rc-removed")):
                    server.rc_stage = stage
                    wait_rc(stage + 1)
                    request(marker, headers={"User-Agent": "Arachni/v1"})
                server.rc_stage = 4
                wait_rc(5)
                request("rc-reenabled")
                server.rc_stage = 5
                wait_rc(6)
                request("tagging-safe")
                for version in range(1, 5):
                    request("tagging-" + str(version), headers={"User-Agent": "TraceTagging/v" + str(version)})
            time.sleep(0.5)
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            records = server.snapshot()
            capture = json.dumps(records, indent=2).encode() + b"\n"
            (out / "capture.json").write_bytes(capture)
            (out / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")
            log_text = (out / "app.log").read_text(errors="replace")
            checks = {"default": [check_waf, check_obfuscation, check_cipher, check_identify, check_circular, check_shell, check_truncation],
                "tagging": [check_tagging], "large": [check_large], "events": [check_events],
                "rate": [check_rate], "standalone": [check_standalone], "controls": [check_controls], "rasp": [check_api10], "sampling": [check_sampling], "renaming": [check_renaming], "onboarding": [check_onboarding]}
            if profile in ("identified", "anonymized", "disabled"):
                checks[profile] = [lambda r, c, l: check_mode(r, c, l, profile)]
            if profile.startswith("ip-"):
                checks[profile] = [lambda r, c, l: check_ip(r, c, l, profile)]
            artifacts = [{"file": str(p.relative_to(output)), "sha256": sha(p.read_bytes())} for p in sorted(out.iterdir()) if p.is_file() and p.suffix in (".py", ".json", ".log", ".port") and p.name != "capture.json"]
            for check in checks[profile]:
                name = profile + ":" + ("check_mode" if profile in ("identified", "anonymized", "disabled") else "check_ip" if profile.startswith("ip-") else check.__name__)
                result = {"name": name, "status": "failed", "configuration": env, "capabilityInventoryRevision": REVISION,
                          "capabilityNames": [], "captureFile": profile + "/capture.json", "captureSha256": sha(capture), "artifacts": artifacts}
                try:
                    result["capabilityNames"] = check(rows, records, log_text)
                    result["status"] = "passed"
                except Exception as error:
                    result["detail"] = repr(error)
                results.append(result)
                print(name, result["status"], flush=True)
            (output / "datadog-security-extra-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")
    assert results and all(r["status"] == "passed" for r in results), "Security extras assertions failed; inspect retained evidence"


def main():
    if not __debug__:
        raise RuntimeError("Security extras requires Python assertions enabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES)
    for name in ("launcher", "rootfs", "app", "sdk-overlay"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--injection-flag", dest="injection_flags", action="append", default=[])
    args = parser.parse_args()
    for key in ("launcher", "rootfs", "app", "sdk_overlay"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    run(args)


if __name__ == "__main__":
    main()
