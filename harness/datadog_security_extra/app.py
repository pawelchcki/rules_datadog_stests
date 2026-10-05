"""Dedicated Django runtime for the security_extra AppSec/IAST evidence lab."""
import argparse
import os
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

# The weak-cipher workload runs against the same pycryptodome pin as the
# upstream django-poc weblog (3.23.0), from a hash-locked wheel overlay.
VENDOR = os.environ.get("SECURITY_EXTRA_VENDOR")
if VENDOR:
    sys.path.insert(0, VENDOR)

# Importing the pair exercises a controlled circular import: module b imports
# module a at call time, so instrumentation must not emit the Python
# "most likely due to a circular import" failure on stdout.
import circular_b  # noqa: F401

from wsgiref.simple_server import make_server

from django.conf import settings

DB_PATH = Path(os.environ.get("SECURITY_EXTRA_STATE", str(APP_DIR))) / "security_extra.sqlite3"
DB_PATH.unlink(missing_ok=True)
settings.configure(
    DEBUG=False, SECRET_KEY="datadog-security-extra-lab", ROOT_URLCONF="security_extra_views",
    ALLOWED_HOSTS=["*"],
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ],
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.sessions",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(DB_PATH)}},
)
import django

django.setup()

from django.core.management import call_command

call_command("migrate", run_syncdb=True, verbosity=0)

from django.contrib.auth.models import User

for username in ("test", "testuuid"):
    if not User.objects.filter(username=username).exists():
        user = User(username=username, email="testuser@ddog.com")
        user.set_password("1234")
        user.save()

from django.core.wsgi import get_wsgi_application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()

    import signal

    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    with make_server("127.0.0.1", 0, get_wsgi_application()) as server:
        Path(args.ready_file).write_text(str(server.server_port))
        server.serve_forever()


if __name__ == "__main__":
    main()
