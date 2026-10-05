"""Django request entrypoints and ordinary application functions; no manufactured SDK state."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen
from wsgiref.simple_server import make_server
sys.path.insert(0, str(Path(__file__).parent))
import target
import ddtrace
from ddtrace import tracer
from ddtrace.internal.runtime import get_runtime_id
from django.conf import settings
settings.configure(DEBUG=False, SECRET_KEY="public-debugger-extra-fixture", ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"], MIDDLEWARE=[], INSTALLED_APPS=[])
import django
django.setup()
from django.http import HttpResponse
from django.urls import path
from django.core.wsgi import get_wsgi_application

identities = []
current_stage = 0

def normal(request):
    root = tracer.current_root_span()
    if root:
        root.set_tag("extra.stage", str(current_stage))
        identities.append({"stage": current_stage, "span_id": str(root.span_id), "trace_id": str(root.trace_id), "kind": "normal"})
    assert target.calculate(7) == 14
    return HttpResponse("14")

def error(request):
    root = tracer.current_root_span()
    if root:
        root.set_tag("extra.stage", str(current_stage))
        identities.append({"stage": current_stage, "span_id": str(root.span_id), "trace_id": str(root.trace_id), "kind": "error"})
    try:
        with tracer.trace("extra.exception"):
            target.fail(current_stage)
    except ValueError:
        pass
    return HttpResponse("handled")

urlpatterns = [path("normal", normal), path("error", error)]
parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
args = parser.parse_args()
root = Path(args.directory)
os.chdir(root)
with make_server("127.0.0.1", 0, get_wsgi_application()) as server:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (root / "ready.json").write_text(json.dumps({"runtime_id": get_runtime_id(), "tracer_version": ddtrace.__version__}))
    for stage in (1, 2, 3, 4, 5):
        deadline = time.monotonic() + 100
        while not (root / (str(stage) + ".command")).exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        current_stage = stage
        for endpoint in ("normal", "error"):
            with urlopen("http://127.0.0.1:" + str(server.server_port) + "/" + endpoint) as response:
                assert response.status == 200
        tracer.flush()
        time.sleep(2)
        (root / (str(stage) + ".identity.json")).write_text(json.dumps([i for i in identities if i["stage"] == stage]))
    server.shutdown()
tracer.shutdown()
