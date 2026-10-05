"""Dedicated routes on the existing Django runtime for native AppSec/IAST evidence."""
import argparse
from pathlib import Path
import sys
import signal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wsgiref.simple_server import make_server

from django.conf import settings
settings.configure(
    DEBUG=False, SECRET_KEY="datadog-security-lab", ROOT_URLCONF="security_views",
    ALLOWED_HOSTS=["*"], MIDDLEWARE=[], INSTALLED_APPS=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
import django
django.setup()
from django.core.wsgi import get_wsgi_application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    def terminate(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    with make_server("127.0.0.1", 0, get_wsgi_application()) as server:
        Path(args.ready_file).write_text(str(server.server_port))
        server.serve_forever()


if __name__ == "__main__":
    main()
