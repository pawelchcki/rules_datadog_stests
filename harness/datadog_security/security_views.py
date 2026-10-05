"""First-party Django views imported through the SDK IAST AST instrumentation."""
import hashlib
import json
import os
import pickle
import random
import subprocess
import sys
import requests
import sqlite3

from django.http import HttpResponse, JsonResponse
from django.urls import path
from ddtrace import tracer
from ddtrace.appsec.trace_utils import track_custom_event, track_user_login_success_event, track_user_login_failure_event


def health(request):
    return JsonResponse({"ready": True})


def source(request, case, value):
    if case == "parameter-value":
        return request.GET["input"]
    if case == "parameter-name":
        return next(key for key in request.GET if key != "control")
    if case == "header-value":
        return request.headers["X-Security-Source"]
    if case == "header-name":
        return next(key for key in request.headers if key.lower() == "x-security-source")
    if case == "cookie-value":
        return request.COOKIES["security-input"]
    if case == "cookie-name":
        return next(iter(request.COOKIES))
    if case == "body":
        return request.POST["input"]
    if case == "path":
        return request.path
    if case == "path-parameter":
        return value
    if case == "uri":
        return request.get_full_path()
    if case == "multipart":
        return request.POST["input"]
    raise ValueError("Unsupported source: " + case)


def security(request, case, value="tainted-input", record_id=None):
    root = tracer.current_root_span()
    assert root is not None, "Django instrumentation must create the request root"
    root.set_tag("security.case", case)
    outcome = None
    if case in ("parameter-value", "parameter-name", "header-value", "header-name", "cookie-value", "cookie-name", "body", "path", "path-parameter", "uri", "multipart"):
        untrusted = source(request, case, value)
        # Both workloads execute real sqlite. The safe control uses SQL binding.
        connection = sqlite3.connect(":memory:")
        try:
            if request.GET.get("control") == "safe":
                connection.execute("SELECT ?", (untrusted,)).fetchall()
            else:
                connection.execute("SELECT '" + untrusted + "'").fetchall()
        finally:
            connection.close()
    elif case in ("command", "path-traversal", "code", "ssrf", "deserialize", "header", "redirect", "xss"):
        untrusted = request.GET["input"]
        safe = request.GET.get("control") == "safe"
        if case == "command":
            assert untrusted == "security-lab"
            outcome = subprocess.run([sys.executable, "-c", "print('security-lab')", "security-lab" if safe else untrusted], check=True, capture_output=True, text=True).stdout.strip()
        elif case == "path-traversal":
            assert untrusted == "safe.txt"
            with open("safe.txt" if safe else untrusted) as stream:
                outcome = stream.read()
        elif case == "code":
            assert untrusted == "2 + 3"
            outcome = eval("2 + 3" if safe else untrusted, {"__builtins__": {}})
        elif case == "ssrf":
            assert untrusted == os.environ["DD_TRACE_AGENT_URL"] + "/healthz"
            outcome = requests.get(os.environ["DD_TRACE_AGENT_URL"] + "/healthz" if safe else untrusted, timeout=5).status_code
        elif case == "deserialize":
            assert untrusted == "Vsecurity-lab\np0\n."
            outcome = pickle.loads(("Vsecurity-lab\np0\n." if safe else untrusted).encode())
        elif case == "header":
            response = JsonResponse({"span_id": str(root.span_id), "trace_id": str(root.trace_id)})
            if safe:
                response["X-Security-Value"] = "security-lab"
            else:
                # Match the pinned upstream Django vulnerable route: direct store access
                # bypasses framework sanitization, allowing real IAST header detection.
                response.headers._store["x-security-value"] = ("X-Security-Value", untrusted)
            return response
        elif case == "xss":
            from django.template import Context, Engine
            from django.utils.safestring import mark_safe
            outcome = Engine().from_string("{{ input }}").render(Context({"input": untrusted if safe else mark_safe(untrusted)}))
            assert outcome == ("&lt;b&gt;security-lab&lt;/b&gt;" if safe else "<b>security-lab</b>")
        elif case == "redirect":
            from django.shortcuts import redirect
            response = redirect("/healthz" if safe else untrusted)
            response.content = json.dumps({"span_id": str(root.span_id), "trace_id": str(root.trace_id)})
            response["X-Security-Span-Id"] = str(root.span_id)
            response["X-Security-Trace-Id"] = str(root.trace_id)
            return response
    elif case in ("cookie-insecure", "cookie-httponly", "cookie-samesite"):
        safe = request.GET.get("control") == "safe"
        response = JsonResponse({"span_id": str(root.span_id), "trace_id": str(root.trace_id)})
        response.set_cookie("security-cookie", "security-lab", secure=safe or case != "cookie-insecure", httponly=safe or case != "cookie-httponly", samesite="None" if not safe and case == "cookie-samesite" else "Strict")
        return response
    elif case == "weak-hash":
        outcome = hashlib.md5(b"security-lab").hexdigest()
    elif case == "strong-hash":
        outcome = hashlib.sha256(b"security-lab").hexdigest()
    elif case == "weak-random":
        outcome = random.randint(0, 100)
    elif case == "login-success":
        track_user_login_success_event(tracer, "security-user", {"role": "tester"})
    elif case == "login-failure":
        track_user_login_failure_event(tracer, "security-user", True, {"reason": "invalid-password"})
    elif case == "custom-event":
        track_custom_event(tracer, "security_lab_event", {"category": "controlled"})
    elif case in ("api-schema", "api-auth", "api-truncation"):
        if request.method == "POST":
            observed = json.loads(request.body)
            assert isinstance(observed, dict)
        outcome = {"label": "schema-dummy-value", "number": 42, "boolean": True,
                   "nullable": None, "items": ["alpha", "beta"], "record_id": record_id}
    elif case == "header-collection":
        response = JsonResponse({"span_id": str(root.span_id), "trace_id": str(root.trace_id)})
        for index in range(1, 5):
            response["X-Security-Response-" + str(index)] = "response-" + str(index)
        return response
    elif case == "security-testing-headers":
        session = requests.Session()
        prepared = session.prepare_request(requests.Request("GET", os.environ["DD_TRACE_AGENT_URL"] + "/healthz"))
        response = session.send(prepared, timeout=5)
        outcome = {"status": response.status_code, "headers": dict(prepared.headers)}
    elif case.startswith("rasp-"):
        untrusted = request.GET["input"]
        safe = request.GET.get("control") == "safe"
        if safe:
            assert untrusted == "safe-control"
        if case == "rasp-lfi":
            assert safe or untrusted == "rasp/base/../safe.txt"
            with open("rasp/safe.txt" if safe else untrusted) as stream:
                outcome = stream.read()
        elif case == "rasp-ssrf":
            assert safe or untrusted == os.environ["DD_TRACE_AGENT_URL"].removeprefix("http://")
            outcome = requests.get(os.environ["DD_TRACE_AGENT_URL"] + "/healthz" if safe else "http://" + untrusted + "/healthz", timeout=5).status_code
        elif case == "rasp-sql":
            assert safe or untrusted == "' OR 1 = 1 --"
            connection = sqlite3.connect(":memory:")
            try:
                outcome = connection.execute("SELECT ?" if safe else "SELECT 1 WHERE 'security-lab' = '" + untrusted + "'", ("security-lab",) if safe else ()).fetchall()
            finally:
                connection.close()
        elif case == "rasp-command":
            assert safe or untrusted == "/bin/echo"
            outcome = subprocess.run(["/bin/echo" if safe else untrusted, "security-lab"], check=True, capture_output=True, text=True).stdout.strip()
        elif case == "rasp-shell":
            assert safe or untrusted == "printf security-lab; printf security-lab"
            outcome = os.system("printf security-lab" if safe else untrusted)
    elif case == "custom-user":
        from ddtrace.contrib.trace_utils import set_user
        set_user(tracer, "security-blocked-user")
    elif case == "custom-response":
        return JsonResponse({"span_id": str(root.span_id), "trace_id": str(root.trace_id)}, status=201)
    elif case in ("client-ip", "waf", "safe", "custom-match", "custom-excluded", "custom-no-match", "custom-block", "custom-block-second", "custom-client-ip", "custom-user", "custom-response"):
        pass
    else:
        return HttpResponse("Unsupported security case", status=404)
    return JsonResponse({"case": case, "marker": root.get_tag("security.marker"),
                         "span_id": str(root.span_id), "trace_id": str(root.trace_id), "outcome": outcome})


CASE_NAMES = ["parameter-value", "parameter-name", "header-value", "header-name", "cookie-value",
              "cookie-name", "body", "path", "uri", "multipart", "weak-hash", "strong-hash",
              "command", "path-traversal", "code", "ssrf", "deserialize", "header", "redirect", "xss",
              "cookie-insecure", "cookie-httponly", "cookie-samesite", "weak-random", "login-success", "login-failure", "custom-event", "client-ip", "waf", "safe", "custom-match", "custom-excluded", "custom-no-match", "custom-block", "custom-block-second", "custom-client-ip", "custom-user", "custom-response", "security-testing-headers", "header-collection", "api-auth", "api-truncation", "rasp-lfi", "rasp-ssrf", "rasp-sql", "rasp-command", "rasp-shell"]
# IAST's route/method sampling is scoped to independent workload routes.
urlpatterns = [path("healthz", health)] + [
    path("security/" + case, security, {"case": case}) for case in CASE_NAMES
] + [path("security/path-parameter/<str:value>", security, {"case": "path-parameter"}),
     path("security/api-schema/<int:record_id>", security, {"case": "api-schema"})]
