"""First-party Django views exercised by the security_extra probe.

Every view returns the current root span identity so the probe can correlate
captured native spans with the request that produced them.
"""
import base64
import json
import os
import subprocess
import sys

import security_controls

import requests
from django.contrib.auth import authenticate, login
from django.http import HttpResponse, JsonResponse
from django.urls import path
from ddtrace import tracer
from ddtrace.appsec import track_user_sdk
from ddtrace.contrib.trace_utils import set_user


def identity(request, root, outcome=None, status=200):
    body = {"case": root.get_tag("security.case"), "marker": root.get_tag("security.marker"),
            "span_id": str(root.span_id), "trace_id": str(root.trace_id)}
    if outcome is not None:
        body["outcome"] = outcome
    response = JsonResponse(body, status=status)
    response["Content-Language"] = "en-US"
    for i in range(1, 6):
        response["X-Test-Header-" + str(i)] = "value" + str(i)
    return response


def root_span(request, case):
    root = tracer.current_root_span()
    assert root is not None, "Django instrumentation must create the request root"
    root.set_tag("security.case", case)
    return root


def health(request):
    return JsonResponse({"ready": True})


def waf(request):
    """Generic WAF target: attacks arrive via query, headers, or JSON body."""
    root = root_span(request, "waf")
    body = None
    if request.method == "POST" and request.body:
        try:
            body = json.loads(request.body)
        except ValueError:
            body = None
    return identity(request, root, {"query": dict(request.GET), "body": body,
                                    "headers": {key.lower(): value for key, value in request.headers.items()
                                                if key.lower().startswith("x-security-")}})


def weak_cipher(request, safe=False):
    root = root_span(request, "weak-cipher")
    if safe:
        from Crypto.Cipher import AES

        cipher = AES.new(b"0123456789abcdef", AES.MODE_EAX)
        outcome = cipher.encrypt(b"abcdefgh").hex()
    else:
        from Crypto.Cipher import ARC4

        outcome = ARC4.new(b"12345678").encrypt(b"abcdefgh").hex()
    return identity(request, root, outcome)


def sampling(request, idx):
    root = root_span(request, "sampling")
    if request.method == "GET":
        tainted = request.GET.get("param", "")
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    else:
        tainted = request.POST.get("param", "")
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
        subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    return identity(request, root, "sampling-witness")


def sampling_second(request, idx):
    root = root_span(request, "sampling-second")
    tainted = request.GET.get("param", "")
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    subprocess.run([sys.executable, "-c", "pass", "inert-" + tainted], check=True, env=child_environment())
    return identity(request, root, "sampling-witness")

def identify(request):
    root = root_span(request, "identify")
    set_user(tracer, user_id="usr.id", email="usr.email", name="usr.name",
             session_id="usr.session_id", role="usr.role", scope="usr.scope")
    return identity(request, root)


def identify_propagate(request):
    root = root_span(request, "identify-propagate")
    set_user(tracer, user_id="usr.id", email="usr.email", name="usr.name",
             session_id="usr.session_id", role="usr.role", scope="usr.scope", propagate=True)
    downstream = os.environ.get("SECURITY_EXTRA_DOWNSTREAM", "")
    outcome = None
    if downstream:
        outcome = requests.get(downstream + "/witness", timeout=5).json()
    return identity(request, root, outcome)


def login_view(request):
    root = root_span(request, "login")
    auth = request.GET.get("auth", "local")
    user = None
    credentials_ok = False
    if auth == "local":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        credentials_ok = username != "" and password != ""
    elif auth == "basic":
        header = request.headers.get("Authorization", "")
        try:
            decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            username, password = "", ""
        user = authenticate(request, username=username, password=password)
        credentials_ok = username != "" and password != ""
    sdk_trigger = request.GET.get("sdk_trigger")
    if sdk_trigger:
        sdk_user = request.GET.get("sdk_user", "sdkUser")
        if request.GET.get("sdk_event", "success") == "success":
            track_user_sdk.track_login_success(login=sdk_user, user_id=sdk_user)
        else:
            track_user_sdk.track_login_failure(login=sdk_user, exists=True, user_id=sdk_user)
    if user is not None:
        # Django accepts numeric string PKs; exercise string-ID anonymization
        # just as the upstream weblog's string primary-key user model does.
        user.pk = str(user.pk)
        login(request, user)
        return identity(request, root, {"user": user.username}, status=200)
    return identity(request, root, {"credentials_present": credentials_ok}, status=401)


def track_success_v2(request):
    root = root_span(request, "track-success-v2")
    payload = json.loads(request.body or b"{}")
    track_user_sdk.track_login_success(login=payload.get("login"), user_id=payload.get("user_id"),
                                       metadata=payload.get("metadata"))
    return identity(request, root)


def track_failure_v2(request):
    root = root_span(request, "track-failure-v2")
    payload = json.loads(request.body or b"{}")
    track_user_sdk.track_login_failure(login=payload.get("login"), exists=payload.get("exists", True),
                                       user_id=payload.get("user_id"), metadata=payload.get("metadata"))
    return identity(request, root)


def api10(request):
    root = root_span(request, "api10")
    queries = {key: str(value) for key, value in request.GET.items()}
    status = queries.pop("status", "200")
    url_extra = queries.pop("url_extra", "")
    body = request.body or None
    if body:
        queries["Content-Type"] = request.headers.get("content-type", "application/json")
    downstream = os.environ["SECURITY_EXTRA_DOWNSTREAM"]
    response = requests.request(request.method, downstream + "/mirror/" + status + url_extra,
                                data=body, headers=queries, timeout=10)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return identity(request, root, {"status": response.status_code, "payload": payload})


def circular(request):
    root = root_span(request, "circular")
    import circular_b

    return identity(request, root, circular_b.roundtrip())


def renaming_leaf(request, param):
    root = root_span(request, "renaming")
    return identity(request, root, param)


def child_environment():
    return {k: v for k, v in os.environ.items() if not k.startswith(("DD_", "OTEL_")) and k != "PYTHONPATH"}


def shell(request):
    root = root_span(request, "shell")
    mode = request.GET.get("mode", "exec")
    value = request.GET.get("input", "hello")
    if mode == "shell":
        command = "print('shell-witness')"
        outcome = subprocess.run(command, shell=True, executable=sys.executable, check=True, capture_output=True, text=True, env=child_environment()).stdout
    else:
        # Execute the pinned runtime itself, avoiding a host executable dependency.
        outcome = subprocess.run([sys.executable, "-c", "print('exec-witness')", "password", value],
                                 check=True, capture_output=True, text=True, env=child_environment()).stdout
    return identity(request, root, outcome)


def controls(request):
    root = root_span(request, "controls")
    value = request.GET.get("param", "witness")
    mode = request.GET.get("mode", "raw")
    if mode == "sanitize":
        value = security_controls.sanitize(value)
    elif mode == "validate":
        security_controls.validate(value)
    elif mode == "different":
        value = security_controls.different(value)
    # The argument is inert to Python, but taint reaches a real command sink.
    outcome = subprocess.run([sys.executable, "-c", "print('controls-witness')", value],
                             check=True, capture_output=True, text=True, env=child_environment()).stdout
    return identity(request, root, outcome)


urlpatterns = [
    path("x/shell", shell),
    path("x/controls", controls),
    path("healthz", health),
    path("x/waf", waf),
    path("x/weak-cipher", weak_cipher),
    path("x/weak-cipher-safe", weak_cipher, {"safe": True}),
    path("x/sampling/<int:idx>", sampling),
    path("x/sampling-second/<int:idx>", sampling_second),
    path("x/identify", identify),
    path("x/identify-propagate", identify_propagate),
    path("x/login", login_view),
    path("x/track-success-v2", track_success_v2),
    path("x/track-failure-v2", track_failure_v2),
    path("x/api10", api10),
    path("x/circular", circular),
    path("resource_renaming/some/url", renaming_leaf, {"param": "static"}),
    path("resource_renaming/int/<int:param>", renaming_leaf),
    path("resource_renaming/int_id/<str:param>", renaming_leaf),
    path("resource_renaming/hex/<str:param>", renaming_leaf),
    path("resource_renaming/hex_id/<str:param>", renaming_leaf),
    path("resource_renaming/files/<str:param>", renaming_leaf),
    path("resource_renaming/<str:param>", renaming_leaf),
]
