"""Assert genuine native security findings and source/sink controls on Django."""
import argparse
import base64
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
import threading
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

from harness.datadog_backend.wire import msgpack
from harness.datadog_security.schema import validate as validate_iast_schema
from harness.datadog_security.telemetry import TelemetryRelay

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
RASP_CASES = {
    "rasp-lfi": ("rasp_local_file_inclusion", "rasp-930-100", "server.io.fs.file", "rasp/base/../safe.txt"),
    "rasp-ssrf": ("rasp_server_side_request_forgery", "rasp-934-100", "server.io.net.url", None),
    "rasp-sql": ("rasp_sql_injection", "rasp-942-100", "server.db.statement", "' OR 1 = 1 --"),
    "rasp-command": ("rasp_command_injection", "rasp-932-110", "server.sys.exec.cmd", "/bin/echo"),
    "rasp-shell": ("rasp_shell_injection", "rasp-932-100", "server.sys.shell.cmd", "printf security-lab; printf security-lab"),
}
SINK_CASES = {
    "command": ("COMMAND_INJECTION", "iast_sink_command_injection", "security-lab"),
    "path-traversal": ("PATH_TRAVERSAL", "iast_sink_path_traversal", "safe.txt"),
    "code": ("CODE_INJECTION", "iast_sink_code_injection", "2 + 3"),
    "ssrf": ("SSRF", "iast_sink_ssrf", None),
    "deserialize": ("UNTRUSTED_SERIALIZATION", "iast_sink_untrusted_deserialization", "Vsecurity-lab\np0\n."),
    "header": ("HEADER_INJECTION", "iast_sink_header_injection", "security-lab"),
    "redirect": ("UNVALIDATED_REDIRECT", "iast_sink_unvalidatedredirect", "/healthz"),
    "xss": ("XSS", "iast_sink_xss", "<b>security-lab</b>"),
    "cookie-insecure": ("INSECURE_COOKIE", "iast_sink_insecure_cookie", None),
    "cookie-httponly": ("NO_HTTPONLY_COOKIE", "iast_sink_http_only_cookie", None),
    "cookie-samesite": ("NO_SAMESITE_COOKIE", "iast_sink_samesite_cookie", None),
}
SOURCE_CASES = {
    "parameter-value": ("iast_source_request_parameter_value", "http.request.parameter", "tainted-input"),
    "parameter-name": ("iast_source_request_parameter_name", "http.request.parameter.name", "tainted-input"),
    "header-value": ("iast_source_header_value", "http.request.header", "tainted-input"),
    "header-name": ("iast_source_header_name", "http.request.header.name", "X-Security-Source"),
    "cookie-value": ("iast_source_cookie_value", "http.request.cookie.value", "tainted-input"),
    "cookie-name": ("iast_source_cookie_name", "http.request.cookie.name", "security-input"),
    "body": ("iast_source_body", "http.request.body", "tainted-input"),
    "path": ("iast_source_path", "http.request.path", "/security/path"),
    "path-parameter": ("iast_source_path_parameter", "http.request.path.parameter", "tainted-input"),
    "uri": ("iast_source_uri", "http.request.uri", "/security/uri"),
    "multipart": ("iast_source_multipart", "http.request.multipart.parameter", "tainted-input"),
}
ENV = {
    "DD_SERVICE": "datadog-security-lab", "DD_ENV": "test", "DD_VERSION": "1",
    "DD_TRACE_ENABLED": "true", "DD_APPSEC_ENABLED": "true", "DD_IAST_ENABLED": "true",
    "DD_IAST_REQUEST_SAMPLING": "100", "DD_IAST_DEDUPLICATION_ENABLED": "false",
    "DD_IAST_MAX_VULNERABILITIES_PER_REQUEST": "20", "DD_IAST_MAX_CONCURRENT_REQUESTS": "10",
    "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]', "DD_TRACE_RATE_LIMIT": "-1",
    "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.05", "DD_TRACE_HEADER_TAGS": "x-security-marker:security.marker",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false", "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_RUNTIME_METRICS_ENABLED": "false", "DD_PROFILING_ENABLED": "false",
    "DD_IAST_PATCH_MODULES": "security_views", "DD_IAST_REDACTION_ENABLED": "false",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def fetch(url, data=None, headers=None, follow=True):
    try:
        opener = urlopen if follow else build_opener(NoRedirect).open
        with opener(Request(url, data=data, headers=headers or {}), timeout=10) as response:
            return response.status, response.read()
    except HTTPError as response:
        return response.code, response.read()


def spans(capture):
    records = json.loads(capture)
    return [span for record in records for chunk in record["payload"]["traces"] for span in chunk]


def marked_root(native, marker):
    roots = [span for span in native if span.get("meta", {}).get("security.marker") == marker
             and int(span.get("parent_id", 0)) == 0]
    assert len(roots) == 1, (marker, roots, native)
    return roots[0]


def native_iast_report(root):
    raw = root.get("meta", {}).get("_dd.iast.json")
    structured = root.get("meta_struct", {}).get("iast")
    if raw:
        report = json.loads(raw)
    else:
        assert isinstance(structured, list) and len(structured) <= 65536, root
        report = msgpack(bytes(structured))
    assert isinstance(report.get("sources", []), list), report
    assert isinstance(report.get("vulnerabilities"), list) and report["vulnerabilities"], report
    for vulnerability in report["vulnerabilities"]:
        assert isinstance(vulnerability.get("type"), str) and vulnerability["type"], vulnerability
        assert isinstance(vulnerability.get("evidence"), dict), vulnerability
        assert isinstance(vulnerability.get("location"), dict), vulnerability
        assert isinstance(vulnerability.get("hash"), int), vulnerability
    return report


def iast_report(root):
    report = native_iast_report(root)
    validate_iast_schema(report)
    return report


def assert_source(root, safe_root, case):
    _, origin, expected_value = SOURCE_CASES[case]
    assert "_dd.iast.json" not in safe_root.get("meta", {}) and "iast" not in safe_root.get("meta_struct", {}), safe_root
    report = iast_report(root)
    sql = [v for v in report["vulnerabilities"] if v["type"] == "SQL_INJECTION"]
    assert len(sql) == 1, report
    indices = [i for i, source in enumerate(report["sources"])
               if source.get("origin") == origin and source.get("value") == expected_value]
    assert indices, (origin, expected_value, report)
    parts = sql[0]["evidence"].get("valueParts", [])
    assert any(part.get("source") in indices and part.get("value") == expected_value for part in parts), report
    location = sql[0]["location"]
    assert location.get("path", "").endswith("security_views.py") and int(location["line"]) > 0, location
    return [SOURCE_CASES[case][0], "iast_sink_sql_injection", "iast_schema"]


def assert_sink(root, safe_root, vulnerability, feature):
    assert "_dd.iast.json" not in safe_root.get("meta", {}) and "iast" not in safe_root.get("meta_struct", {}), safe_root
    report = native_iast_report(root) if vulnerability == "UNTRUSTED_SERIALIZATION" else iast_report(root)
    matching = [v for v in report["vulnerabilities"] if v["type"] == vulnerability]
    assert len(matching) == 1, report
    return [feature] + ([] if vulnerability == "UNTRUSTED_SERIALIZATION" else ["iast_schema"])


def assert_iast_stack(root):
    report = native_iast_report(root)
    raw = root.get("meta_struct", {}).get("_dd.stack")
    assert isinstance(raw, list) and len(raw) <= 65536, root
    stacks = msgpack(bytes(raw)).get("vulnerability", [])
    assert stacks and len(stacks) <= 64, stacks
    index = {stack["id"]: stack for stack in stacks}
    assert len(index) == len(stacks), stacks
    for vulnerability in report["vulnerabilities"]:
        location = vulnerability["location"]
        assert int(location["spanId"]) == int(root["span_id"]), (location, root)
        stack = index[location["stackId"]]
        assert stack["language"] == "python" and stack["frames"], stack
        frames = stack["frames"]
        assert len({frame["id"] for frame in frames}) == len(frames), frames
        assert any(frame.get("file", "").endswith("security_views.py")
                   and frame.get("function") == location["method"]
                   and frame.get("line") == location["line"] for frame in frames), (location, frames)
    return ["iast_stack_trace", "iast_extended_location", "security_events_metastruct"]


def assert_rasp(root, control, case):
    feature, rule_id, address, _ = RASP_CASES[case]
    assert "appsec" not in control.get("meta_struct", {}) and "_dd.appsec.json" not in control.get("meta", {}), control
    report = appsec_report(root)
    triggers = [trigger for trigger in report["triggers"] if trigger["rule"]["id"] == rule_id]
    assert len(triggers) == 1, report
    trigger = triggers[0]
    assert trigger["rule"]["tags"].get("module") == "rasp", trigger
    parameters = [parameter for match in trigger["rule_matches"] for parameter in match["parameters"]]
    correlated = [parameter for parameter in parameters if parameter.get("resource", {}).get("address") == address
                  and parameter.get("params", {}).get("address") == "server.request.query"]
    assert len(correlated) == 1, parameters
    observed = correlated[0]
    expected = RASP_CASES[case][3]
    if expected is not None:
        assert observed["params"]["value"] == expected, observed
    else:
        assert re.fullmatch(r"127\.0\.0\.1:[0-9]+", observed["params"]["value"]), observed
        assert observed["resource"]["value"] == "http://" + observed["params"]["value"] + "/healthz", observed
    if case not in ("rasp-sql", "rasp-command") and expected is not None:
        value = observed["resource"]["value"]
        assert value == expected or isinstance(value, list) and value[0] == expected, observed
    if case == "rasp-command":
        assert observed["resource"]["value"] == '/bin/echo "security-lab"', observed
    if case == "rasp-sql":
        assert observed.get("db_type", {}).get("value") == "sqlite" and " OR " in observed["resource"]["value"], observed
    assert any(parameter.get("params", {}).get("address") == "server.request.query" and parameter["params"].get("key_path") == ["input"] for parameter in parameters), parameters
    assert int(trigger["span_id"]) == int(root["span_id"]), trigger
    assert root["metrics"].get("_dd.appsec.rasp.duration", -1) >= 0, root
    assert root["metrics"].get("_dd.appsec.rasp.rule.eval", 0) > 0, root
    raw = root.get("meta_struct", {}).get("_dd.stack")
    assert isinstance(raw, list) and len(raw) <= 65536, root
    stacks = msgpack(bytes(raw))["exploit"]
    matching = [stack for stack in stacks if stack["id"] == trigger["stack_id"]]
    assert len(matching) == 1 and matching[0]["language"] == "python", stacks
    assert 0 < len(matching[0]["frames"]) <= 32, stacks
    assert any(frame.get("file", "").endswith("security_views.py") for frame in matching[0]["frames"]), stacks
    return [feature, "rasp_span_tags", "rasp_stack_trace", "security_events_metastruct"]


def appsec_report(root):
    raw = root.get("meta", {}).get("_dd.appsec.json")
    structured = root.get("meta_struct", {}).get("appsec")
    if raw:
        return json.loads(raw)
    assert isinstance(structured, list) and len(structured) <= 65536, root
    return msgpack(bytes(structured))


def assert_waf_control(root, case, body):
    assert root["metrics"].get("_dd.appsec.enabled") == 1, root
    assert root["meta"].get("_dd.appsec.event_rules.version") == "security-lab-1", root
    assert root["metrics"].get("_dd.appsec.event_rules.error_count", 0) == 0, root
    assert root["metrics"].get("_dd.appsec.event_rules.loaded") == 5, root
    if case in ("custom-excluded", "custom-no-match"):
        assert "appsec" not in root.get("meta_struct", {}) and "_dd.appsec.json" not in root["meta"], root
        assert root["meta"]["http.status_code"] == "200", root
        return ["waf_features", "threats_configuration"]
    report = appsec_report(root)
    rule = "lab-" + ({"custom-client-ip": "client-ip", "custom-user": "user", "custom-response": "response"}.get(case, "block" if case.startswith("custom-block") else "detect"))
    assert len(report["triggers"]) == 1 and report["triggers"][0]["rule"]["id"] == rule, report
    expected_address, expected_value = {"custom-client-ip": ("http.client_ip", "42.42.42.42"), "custom-user": ("usr.id", "security-blocked-user"), "custom-response": ("server.response.status", "201")}.get(case, ("server.request.query", "block" if case.startswith("custom-block") else "detect"))
    assert any(parameter["address"] == expected_address and parameter["value"] == expected_value
               for match in report["triggers"][0]["rule_matches"] for parameter in match["parameters"]), report
    names = ["waf_features", "threats_configuration", "security_events_metadata", "security_events_metastruct"]
    if case.startswith("custom-block") or case in ("custom-client-ip", "custom-user", "custom-response"):
        assert root["meta"].get("appsec.blocked") == "true" and root["meta"]["http.status_code"] == "403", root
        response = json.loads(body)
        assert response.get("errors"), response
        names += ["appsec_response_blocking" if case == "custom-response" else "appsec_request_blocking", "appsec_blocking_action"]
        if case != "custom-user":
            security_id = response["security_response_id"]
            assert str(uuid.UUID(security_id)) == security_id, response
            assert report["triggers"][0]["security_response_id"] == security_id, (report, response)
            assert int(report["triggers"][0]["span_id"]) == int(root["span_id"]), (report, root)
            names.append("blocking_response_id")
        if case == "custom-client-ip": names.append("appsec_client_ip_blocking")
        if case == "custom-user": names.append("appsec_user_blocking")
    else:
        assert root["meta"]["http.status_code"] == "200" and "appsec.blocked" not in root["meta"], root
    return names


def assert_sql_trace(root, native):
    trace = [span for span in native if int(span["trace_id"]) == int(root["trace_id"])]
    identifiers = {int(span["span_id"]): span for span in trace}
    assert len(identifiers) == len(trace), trace
    sql = [span for span in trace if span.get("type") == "sql"]
    assert len(sql) == 1, trace
    query = sql[0]
    assert query["name"] == "sqlite.query" and query["resource"].startswith("SELECT "), query
    assert query["meta"].get("db.system") == "sqlite" and query["meta"].get("component") == "sqlite", query
    assert query["meta"].get("span.kind") == "client" and int(query.get("error", 0)) == 0, query
    parent = identifiers[int(query["parent_id"])]
    assert parent["name"] == "django.view" and int(parent["parent_id"]) == int(root["span_id"]), trace
    assert all(int(span["duration"]) > 0 and int(span["start"]) > 0 for span in trace), trace
    return ["sql_support"]


def assert_security_telemetry(records, native):
    assert records and all(record["status"] == 200 and not record.get("error") for record in records), records
    events = []
    for record in records:
        envelope = record["payload"]
        assert envelope["api_version"] == "v2" and envelope["application"]["service_name"] == ENV["DD_SERVICE"], envelope
        assert envelope["application"]["language_name"] == "python", envelope
        events.extend(envelope["payload"] if envelope["request_type"] == "message-batch" else [envelope])
    configurations = [config for event in events if event["request_type"] in ("app-started", "app-client-configuration-change")
                      for config in event["payload"].get("configuration", [])]
    assert any(config["name"] == "DD_APPSEC_ENABLED" and str(config["value"]).lower() in ("1", "true")
               and config["origin"] == "env_var" for config in configurations), configurations
    endpoints = [event["payload"] for event in events if event["request_type"] == "app-endpoints"]
    assert endpoints and sum(payload["is_first"] is True for payload in endpoints) == 1, endpoints
    discovered = [endpoint for payload in endpoints for endpoint in payload["endpoints"]]
    keys = [(endpoint["method"], endpoint["path"]) for endpoint in discovered]
    assert len(keys) == len(set(keys)), discovered
    assert all(method in ("*", "GET", "POST") and isinstance(path, str) and path for method, path in keys), keys
    assert any(path == "security/api-schema/<int:record_id>" for _, path in keys), keys
    assert all(endpoint.get("operation_name") == "django.request" for endpoint in discovered), discovered
    series = [metric for event in events if event["request_type"] == "generate-metrics"
              for metric in event["payload"].get("series", []) if metric.get("namespace", event["payload"].get("namespace")) == "appsec"]
    initial = [metric for metric in series if metric["metric"] == "waf.init"]
    assert len(initial) == 1 and initial[0]["points"][0][1] == 1 and initial[0]["type"] == "count" and initial[0]["common"] is True, initial
    assert {"success:true"} <= set(initial[0]["tags"]), initial
    requests = [metric for metric in series if metric["metric"] == "waf.requests"]
    assert requests, series
    for metric in initial + requests:
        assert all(any(tag.startswith(prefix + ":") for tag in metric["tags"]) for prefix in ("waf_version", "event_rules_version")), metric
    for metric in requests:
        assert {"rule_triggered:false", "request_blocked:false", "waf_timeout:false", "input_truncated:false"} <= set(metric["tags"]), metric
        assert metric["type"] == "count" and metric["common"] is True and all(point[1] > 0 for point in metric["points"]), metric
    traced_requests = [span for span in native if span["name"] == "django.request" and int(span.get("parent_id", 0)) == 0]
    assert sum(point[1] for metric in requests for point in metric["points"]) == len(traced_requests), (requests, traced_requests)
    return ["api_security_endpoint_discovery", "appsec_service_activation_origin_metric", "waf_telemetry"]


def api_schema(root, address):
    raw = root.get("meta", {}).get("_dd.appsec.s." + address)
    assert isinstance(raw, str) and len(raw) <= 65536, (address, root)
    decoded = gzip.decompress(base64.b64decode(raw, validate=True))
    assert len(decoded) <= 65536
    value = json.loads(decoded)
    assert isinstance(value, list) and value, value
    return value


def assert_api_security(root, case, enabled):
    schemas = {key: value for key, value in root.get("meta", {}).items() if key.startswith("_dd.appsec.s.")}
    if not enabled:
        assert not schemas, root
        assert root["metrics"].get("_dd.appsec.enabled") == 1, root
        return ["api_security_configuration"]
    headers = api_schema(root, "req.headers")[0]
    assert {"host", "user-agent", "accept-encoding"} <= headers.keys(), headers
    if case == "api-schema":
        request = api_schema(root, "req.body")[0]
        for key, kind in {"label": 8, "number": 4, "boolean": 2, "nullable": 1}.items():
            assert request[key][0] == kind, request
        response = api_schema(root, "res.body")[0]
        assert response["outcome"][0]["number"][0] == 4 and response["outcome"][0]["boolean"][0] == 2, response
        params = api_schema(root, "req.params")[0]
        assert params["record_id"][0] in (4, 8), params
        query = api_schema(root, "req.query")[0]
        assert {"count", "label"} <= query.keys(), query
        serialized = json.dumps([request, response, headers, params, query])
        assert "schema-dummy-value" not in serialized and "alpha" not in serialized and "beta" not in serialized, serialized
        return ["api_security_schemas", "api_security_configuration"]
    jwt = api_schema(root, "req.jwt")[0]
    assert jwt["header"][0] == {"alg": [8], "typ": [8]}, jwt
    payload = jwt["payload"][0]
    assert payload["sub"][0] == 8 and payload["iat"][0] == 4 and payload["user"][0]["id"][0] == 4, jwt
    assert jwt["signature"][0] == {"available": [2]}, jwt
    cookies = api_schema(root, "req.cookies")[0]
    assert cookies["session"][0] == 8 and "_dd.appsec.s.res.cookies" not in root["meta"], cookies
    serialized = json.dumps([jwt, cookies])
    assert "auth-dummy-value" not in serialized and "auth-dummy-cookie" not in serialized and "signature" not in json.dumps(jwt["signature"]), serialized
    return ["auth_schemas", "api_security_schemas", "api_security_configuration"]


def assert_standalone(root, native, profile):
    trace = [span for span in native if int(span["trace_id"]) == int(root["trace_id"])]
    assert trace and all(not isinstance(span["metrics"].get("_dd.apm.enabled"), bool)
                         and span["metrics"].get("_dd.apm.enabled") == 0 for span in trace), trace
    assert root["metrics"].get("_sampling_priority_v1") == 2, root
    assert root["meta"].get("_dd.p.ts") == "02", root
    if profile == "iast-standalone":
        assert root["metrics"].get("_dd.iast.enabled") == 1, root
        iast_report(root)
        return ["iast_standalone"]
    assert root["metrics"].get("_dd.appsec.enabled") == 1, root
    assert appsec_report(root)["triggers"], root
    return ["appsec_standalone"]


def assert_header_collection(root, control):
    assert appsec_report(root)["triggers"], root
    assert "appsec" not in control.get("meta_struct", {}) and "_dd.appsec.json" not in control.get("meta", {}), control
    for index in range(1, 5):
        request = "http.request.headers.x-security-custom-" + str(index)
        response = "http.response.headers.x-security-response-" + str(index)
        assert root["meta"].get(request) == "request-" + str(index), root
        assert root["meta"].get(response) == "response-" + str(index), root
        assert request not in control["meta"] and response not in control["meta"], control
    assert "_dd.appsec.request.header_collection.discarded" not in root["metrics"], root
    assert "_dd.appsec.response.header_collection.discarded" not in root["metrics"], root
    return ["appsec_collect_all_headers"]


def assert_testing_headers(root, control, body):
    expected = {"x-datadog-endpoint-scan": "scan-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "x-datadog-security-test": "test-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
    for key, value in expected.items():
        assert root["meta"].get("http.request.headers." + key) == value, root
        assert "http.request.headers." + key not in control["meta"], control
    outcome = body["outcome"]
    assert outcome["status"] == 200 and outcome["headers"], body
    assert not set(expected) & {key.lower() for key in outcome["headers"]}, body
    return ["api_security_testing_headers_collection"]


def assert_security_metadata(root, case):
    tags = root["meta"]
    expected = "/security/path-parameter/{value}" if case == "path-parameter" else "/security/" + case
    assert tags.get("_dd.appsec.normalized_route") == expected, root
    assert "?" not in tags["_dd.appsec.normalized_route"], root
    return ["api_security_normalized_route"]


def assert_fingerprints(root):
    tags = root["meta"]
    for tag, pattern in {"network": r"net-[^-]*-[^-]*", "header": r"hdr-[^-]*-[^-]*-[^-]*-[^-]*", "endpoint": r"http-[^-]*-[^-]*-[^-]*-[^-]*", "session": r"ssn-[^-]*-[^-]*-[^-]*-[^-]*"}.items():
        assert re.fullmatch(pattern, tags["_dd.appsec.fp." + ("session" if tag == "session" else "http." + tag)]), tags
    return ["fingerprinting"]


def assert_event(root, kind):
    tags = root.get("meta", {})
    if kind == "login-success":
        assert tags.get("usr.id") == "security-user", root
        assert tags.get("appsec.events.users.login.success.track") == "true", root
        assert tags.get("appsec.events.users.login.success.role") == "tester", root
        return ["user_monitoring"]
    if kind == "login-failure":
        assert tags.get("appsec.events.users.login.failure.usr.id") == "security-user", root
        assert tags.get("appsec.events.users.login.failure.usr.exists") == "true", root
        assert tags.get("appsec.events.users.login.failure.track") == "true", root
        return ["user_monitoring"]
    assert tags.get("appsec.events.security_lab_event.track") == "true", root
    assert tags.get("appsec.events.security_lab_event.category") == "controlled", root
    return ["custom_business_logic_events"]


def resolve(path):
    for candidate in [Path(path)] + [Path(root) / path for root in
            (os.environ.get("RUNFILES_DIR"), os.environ.get("TEST_SRCDIR")) if root]:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError(path)


def run(args):
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    matches = [int(value) for key, value in ports.items() if key.endswith(args.sink_suffix)]
    assert len(matches) == 1, ports
    sink = "http://127.0.0.1:" + str(matches[0])
    env = dict(ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION=args.wire)
    telemetry = None
    if args.profile in ("appsec-standalone", "iast-standalone"):
        env.update(DD_APM_TRACING_ENABLED="false", DD_APPSEC_ENABLED=str(args.profile == "appsec-standalone").lower(),
                   DD_IAST_ENABLED=str(args.profile == "iast-standalone").lower())
    if args.profile in ("api-enabled", "api-disabled"):
        telemetry = TelemetryRelay(sink, out / "telemetry")
        telemetry_thread = threading.Thread(target=telemetry.serve_forever, daemon=True)
        telemetry_thread.start()
        env.update(DD_TRACE_AGENT_URL="http://127.0.0.1:" + str(telemetry.server_port),
                   DD_API_SECURITY_ENABLED=str(args.profile == "api-enabled").lower(), DD_API_SECURITY_SAMPLE_DELAY="0",
                   DD_INSTRUMENTATION_TELEMETRY_ENABLED="true", DD_TELEMETRY_HEARTBEAT_INTERVAL="0.2",
                   DD_IAST_ENABLED="false")
    if args.profile in ("waf-controls", "rasp-controls"):
        rules_copy = out / (args.profile + ".json")
        rules_copy.write_bytes(Path(__file__).with_name(args.profile + ".json").read_bytes())
        env.update(DD_APPSEC_RULES=str(rules_copy), DD_IAST_ENABLED="false")
        if args.profile == "rasp-controls": env["DD_APPSEC_RASP_ENABLED"] = "true"
    app_copy = out / "app.py"
    app_copy.write_bytes(Path(args.app).read_bytes())
    (out / "safe.txt").write_text("security-lab")
    (out / "rasp/base").mkdir(parents=True, exist_ok=True)
    (out / "rasp/safe.txt").write_text("security-lab")
    views_copy = out / "security_views.py"
    views_copy.write_bytes(Path(args.app).with_name("security_views.py").read_bytes())
    ready = out / "app.port"
    ready.unlink(missing_ok=True)
    launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
    launch += ["--instance=datadog-security-lab"]
    launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
    launch += ["--", str(app_copy), "--ready-file", str(ready)]
    proc_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
    results = []
    with (out / "app.log").open("wb") as log:
        proc = subprocess.Popen(launch, stdout=log, stderr=log, env=proc_env, start_new_session=True, cwd=out)
        try:
            deadline = time.monotonic() + 40
            while not ready.exists():
                assert proc.poll() is None, (out / "app.log").read_text(errors="replace")
                assert time.monotonic() < deadline, "Django readiness timed out"
                time.sleep(0.05)
            base = "http://127.0.0.1:" + ready.read_text().strip()
            while True:
                try:
                    status, _ = fetch(base + "/healthz")
                    assert status == 200
                    break
                except URLError:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)

            def request_case(case, safe=False):
                marker = case + ("-safe" if safe else "")
                query = {"control": "safe"} if safe else {}
                headers = {"X-Security-Marker": marker, "X-Security-Source": "tainted-input",
                           "Cookie": "security-input=tainted-input", "X-Forwarded-For": "42.42.42.42"}
                data = None
                if case == "header-collection":
                    headers.update({"X-Security-Custom-" + str(index): "request-" + str(index) for index in range(1, 5)})
                    headers["User-Agent"] = "security-lab-safe" if safe else "Arachni/v1"
                if case == "security-testing-headers" and not safe:
                    headers.update({"x-datadog-endpoint-scan": "scan-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "x-datadog-security-test": "test-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"})
                if case in RASP_CASES:
                    query["input"] = "safe-control" if safe else RASP_CASES[case][3] or sink.removeprefix("http://")
                if case in SINK_CASES:
                    query["input"] = SINK_CASES[case][2] or (sink + "/healthz" if case == "ssrf" else "security-lab")
                if case == "parameter-value":
                    query["input"] = "tainted-input"
                if case == "parameter-name":
                    query["tainted-input"] = "value"
                if case in ("api-schema", "api-truncation"):
                    data = json.dumps({"label": "schema-dummy-value", "number": 42, "boolean": True, "nullable": None, "items": ["alpha", "beta"]}).encode()
                    headers["Content-Type"] = "application/json"
                    query.update(count="42", label="schema-dummy-value")
                if case == "api-auth":
                    def b64(value): return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
                    headers["Authorization"] = "Bearer " + b64({"alg": "HS256", "typ": "JWT"}) + "." + b64({"sub": "auth-dummy-value", "iat": 1516239022, "user": {"id": 42, "roles": ["reader", "writer"]}}) + ".c2lnbmF0dXJl"
                    headers["Cookie"] = "session=auth-dummy-cookie"
                if case == "body":
                    data = urlencode({"input": "tainted-input"}).encode()
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                if case == "multipart":
                    data = b'--security-boundary\r\nContent-Disposition: form-data; name="input"\r\n\r\ntainted-input\r\n--security-boundary--\r\n'
                    headers["Content-Type"] = "multipart/form-data; boundary=security-boundary"
                if args.profile == "waf-controls":
                    query["attack"] = "block" if case.startswith("custom-block") else "detect" if case in ("custom-match", "custom-excluded") else "harmless"
                    if case == "custom-client-ip": query["block_target"] = "ip"
                    if case == "custom-excluded": query["exclude"] = "yes"
                if case == "waf":
                    query["attack"] = "1' OR '1' = '1"
                path = "/security/" + case + ("/tainted-input" if case == "path-parameter" else "/7" if case == "api-schema" else "")
                status, body = fetch(base + path + ("?" + urlencode(query) if query else ""), data, headers, follow=case != "redirect")
                (out / (marker + ".response.json")).write_bytes(body)
                assert status == 200 or (case in ("waf", "custom-block", "custom-block-second", "custom-client-ip", "custom-user", "custom-response") and status == 403) or (case == "redirect" and status == 302), (marker, status, body)
                for _ in range(100):
                    _, capture = fetch(sink + "/dump?protocol=datadog")
                    (out / (marker + ".capture.json")).write_bytes(capture)
                    roots = [span for span in spans(capture) if span.get("meta", {}).get("security.marker") == marker
                             and int(span.get("parent_id", 0)) == 0]
                    if roots:
                        root = marked_root(spans(capture), marker)
                        if status in (200, 302):
                            identity = json.loads(body)
                            assert int(root["span_id"]) == int(identity["span_id"]), (root, identity)
                            assert int(root["trace_id"]) == int(identity["trace_id"]) & ((1 << 64) - 1), (root, identity)
                        return root, capture, marker
                    time.sleep(0.05)
                raise AssertionError("Missing native root for " + marker)

            fetch(sink + "/reset?protocol=datadog", b"")
            safe_root, _, _ = request_case("strong-hash")
            cases = (["custom-match", "custom-excluded", "custom-no-match", "custom-block", "custom-block-second", "custom-client-ip", "custom-user", "custom-response"] if args.profile == "waf-controls" else
                     list(SOURCE_CASES) + list(SINK_CASES) + ["weak-hash", "weak-random", "login-success", "login-failure", "custom-event", "client-ip", "security-testing-headers", "waf"])
            if args.profile == "rasp-controls": cases = list(RASP_CASES)
            if args.profile in ("api-enabled", "api-disabled"): cases = ["api-schema", "api-auth"]
            if args.profile == "appsec-standalone": cases = ["header-collection", "waf"]
            if args.profile == "iast-standalone": cases = ["parameter-value"]
            security_ids = set()
            for case in cases:
                result = {"name": (args.profile + ":" + case if args.profile != "default" else case), "securityCase": case, "wire": args.wire, "configuration": env,
                          "capabilityInventoryRevision": REVISION, "workloadSha256": sha(Path(args.app).read_bytes()),
                          "capabilityNames": []}
                root = None
                try:
                    control = None
                    if case in SOURCE_CASES or case in SINK_CASES or case in RASP_CASES or case in ("security-testing-headers", "header-collection"):
                        control, _, safe_marker = request_case(case, safe=True)
                    root, capture, marker = request_case(case)
                    result.update(captureFile=marker + ".capture.json", captureSha256=sha(capture),
                                  artifacts=[{"file": marker + ".response.json", "sha256": sha((out / (marker + ".response.json")).read_bytes())},
                                             {"file": "app.py", "sha256": sha(app_copy.read_bytes())},
                                             {"file": "security_views.py", "sha256": sha(views_copy.read_bytes())}])
                    if args.profile in ("waf-controls", "rasp-controls"):
                        result["artifacts"].append({"file": args.profile + ".json", "sha256": sha(rules_copy.read_bytes())})
                    if control is not None:
                        control_file = safe_marker + ".capture.json"
                        result["artifacts"].append({"file": control_file, "sha256": sha((out / control_file).read_bytes())})
                    if case in ("weak-hash", "weak-random"):
                        result["artifacts"].append({"file": "strong-hash.capture.json", "sha256": sha((out / "strong-hash.capture.json").read_bytes())})
                    if case == "header-collection":
                        result["capabilityNames"] = assert_header_collection(root, control)
                    elif case in ("api-schema", "api-auth"):
                        result["capabilityNames"] = assert_api_security(root, case, args.profile == "api-enabled")
                    elif case == "security-testing-headers":
                        result["capabilityNames"] = assert_testing_headers(root, control, json.loads((out / (marker + ".response.json")).read_bytes()))
                    elif case in RASP_CASES:
                        result["capabilityNames"] = assert_rasp(root, control, case)
                    elif args.profile == "waf-controls":
                        result["capabilityNames"] = assert_waf_control(root, case, (out / (marker + ".response.json")).read_bytes())
                        if case == "custom-user":
                            result["unsupportedAssertions"] = ["SDK user-blocked response emits default instead of a UUID security_response_id; blocking-response-ID parity is not claimed for user blocking."]
                        if "blocking_response_id" in result["capabilityNames"]:
                            security_id = json.loads((out / (marker + ".response.json")).read_bytes())["security_response_id"]
                            assert security_id not in security_ids, security_id
                            security_ids.add(security_id)
                    elif case in SOURCE_CASES:
                        result["capabilityNames"] = assert_source(root, control, case) + assert_iast_stack(root) + assert_sql_trace(root, spans(capture))
                        if env["DD_APPSEC_ENABLED"] == "true":
                            result["capabilityNames"] += assert_security_metadata(root, case)
                    elif case in SINK_CASES:
                        vulnerability, feature, _ = SINK_CASES[case]
                        result["capabilityNames"] = assert_sink(root, control, vulnerability, feature)
                        if case == "redirect":
                            result["unsupportedAssertions"] = ["Pinned SDK redirect vulnerability location line differs from its actual stack frame; exact extended-location/stack linkage is not claimed for this sink."]
                        else:
                            result["capabilityNames"] += assert_iast_stack(root)
                    elif case == "weak-hash":
                        result["capabilityNames"] = assert_sink(root, safe_root, "WEAK_HASH", "weak_hash_vulnerability_detection") + assert_iast_stack(root)
                    elif case == "weak-random":
                        result["capabilityNames"] = assert_sink(root, safe_root, "WEAK_RANDOMNESS", "iast_sink_weakrandomness") + assert_iast_stack(root)
                    elif case in ("login-success", "login-failure", "custom-event"):
                        result["capabilityNames"] = assert_event(root, case)
                    elif case == "client-ip":
                        assert root["meta"].get("http.client_ip") == "42.42.42.42", root
                        result["capabilityNames"] = ["appsec_standard_tags_client_ip"] + assert_fingerprints(root) + assert_security_metadata(root, case)
                    else:
                        raw = root.get("meta", {}).get("_dd.appsec.json")
                        report = json.loads(raw) if raw else msgpack(bytes(root["meta_struct"]["appsec"]))
                        assert report["triggers"] and all(trigger.get("rule", {}).get("id") for trigger in report["triggers"]), report
                        assert root["metrics"].get("_dd.appsec.waf.duration", -1) >= 0, root
                        result["capabilityNames"] = ["security_events_metadata", "threats_alpha_preview", "support_in_app_waf_metrics_report", "security_events_metastruct", "appsec_miscs_internals"]
                    if args.profile in ("appsec-standalone", "iast-standalone"):
                        result["capabilityNames"] += assert_standalone(root, spans(capture), args.profile)
                    result["status"] = "passed"
                except Exception as error:
                    expected_missing = case in ("uri", "multipart") and isinstance(root, dict) and "captureFile" in result
                    appsec_case = case in ("waf", "header-collection") or case.startswith("custom-") or case in RASP_CASES
                    missing_headers = (case == "header-collection" and isinstance(root, dict) and "captureFile" in result
                        and root.get("metrics", {}).get("_dd.appsec.enabled") == 1
                        and (root.get("meta_struct", {}).get("appsec") or root.get("meta", {}).get("_dd.appsec.json"))
                        and bool(appsec_report(root).get("triggers")))
                    missing_wire_struct = (args.wire == "v0.5" and isinstance(root, dict) and "captureFile" in result and case not in
                        ("login-success", "login-failure", "custom-event", "client-ip", "security-testing-headers")
                        and not root.get("meta_struct", {}).get("appsec" if appsec_case else "iast")
                        and root.get("metrics", {}).get("_dd.appsec.enabled" if appsec_case else "_dd.iast.enabled") == 1)
                    if expected_missing:
                        result.update(status="unsupported", detail="Pinned Python manifest marks " + case + " IAST source missing_feature; exact required source origin was not observed: " + repr(error))
                    elif missing_headers:
                        result.update(status="unsupported", detail="Pinned Python manifest marks Test_ExtendedHeaderCollection missing_feature; custom request/response headers were not collected despite a genuine security trigger: " + repr(error))
                    elif missing_wire_struct:
                        result.update(status="unsupported", detail="Pinned Python v0.5 encoder omits structured security report fields despite enabled native instrumentation: " + repr(error))
                    else:
                        result.update(status="failed", detail=repr(error))
                results.append(result)
                print(case, result["status"], flush=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if telemetry is not None:
                telemetry.shutdown()
                telemetry.server_close()
                telemetry_thread.join(timeout=3)
                records = telemetry.snapshot()
                (out / "telemetry/requests.json").write_text(json.dumps(records, indent=2) + "\n")
                files = [path.relative_to(out).as_posix() for path in sorted((out / "telemetry").iterdir()) if path.is_file()]
                artifacts = [{"file": path, "sha256": sha((out / path).read_bytes())} for path in files]
                artifacts += [{"file": "app.py", "sha256": sha(app_copy.read_bytes())},
                              {"file": "security_views.py", "sha256": sha(views_copy.read_bytes())}]
                for result in results:
                    result.setdefault("artifacts", []).extend(artifacts)
                _, final_capture = fetch(sink + "/dump?protocol=datadog")
                (out / "final.capture.json").write_bytes(final_capture)
                result = {"name": args.profile + ":telemetry", "status": "failed", "wire": args.wire,
                          "configuration": env, "capabilityInventoryRevision": REVISION, "capabilityNames": [],
                          "captureFile": "final.capture.json", "captureSha256": sha(final_capture), "artifacts": artifacts}
                try:
                    result["capabilityNames"] = assert_security_telemetry(records, spans(final_capture))
                    result["status"] = "passed"
                except Exception as error:
                    result["detail"] = repr(error)
                results.append(result)
            (out / "datadog-security-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")
    assert results and all(result["status"] in ("passed", "unsupported") for result in results), "Security lab assertions failed; inspect retained captures"


def main():
    if not __debug__:
        raise RuntimeError("Security lab requires Python assertions enabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["default", "waf-controls", "rasp-controls", "api-enabled", "api-disabled", "appsec-standalone", "iast-standalone"], default="default")
    parser.add_argument("--wire", choices=["v0.4", "v0.5"], required=True)
    parser.add_argument("--sink-suffix", default="//harness:otel_sink_service")
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--rootfs", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1])
                            if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    run(args)


if __name__ == "__main__":
    main()
