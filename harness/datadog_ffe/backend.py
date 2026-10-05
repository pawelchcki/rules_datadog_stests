"""Local UFC delivery and EVP capture boundaries for real SDK feature flags."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
import gzip
import json
import threading
import time
from urllib.parse import urlsplit

CONFIG_PATH = "/api/v2/feature-flagging/config/rules-based/server"
API_KEY = "system-tests-mock-api-key"


class Backend(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, sink, configuration):
        super().__init__(("127.0.0.1", 0), Handler)
        self.sink = sink
        self.configuration = configuration
        self.version = 1
        self.records = []
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def capture(self, method, path, headers, body, status, payload=None):
        with self.lock:
            self.records.append({"method": method, "path": path, "headers": headers,
                "bodyHex": body.hex(), "status": status, "payload": json.loads(json.dumps(payload)), "receivedUnixNano": time.time_ns()})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/_lab/status":
            self.respond(200, {"configurationRequests": sum(record["path"].startswith(CONFIG_PATH) for record in self.server.snapshot())})
            return
        if urlsplit(self.path).path == CONFIG_PATH:
            etag = '"ufc-' + str(self.server.version) + '"'
            headers = {key.lower(): value for key, value in self.headers.items()}
            status = 304 if headers.get("if-none-match") == etag else 200
            # The real SDK intentionally omits credentials for custom origins.
            if "dd-api-key" in headers:
                status = 401
            document = {"data": {"type": "universal-flag-configuration", "id": str(self.server.version), "attributes": self.server.configuration}}
            body = json.dumps(document).encode() if status == 200 else b""
            self.server.capture("GET", self.path, headers, b"", status, document if status == 200 else None)
            self.respond(status, body, {"ETag": etag})
            return
        self.forward()

    def do_POST(self):
        if self.path == "/_lab/change":
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.configuration["flags"]["basic-flag"]["variations"]["on"]["value"] = False
            self.server.version += 1
            self.respond(200, {"changed": True})
            return
        if self.path.startswith("/evp_proxy/v2/api/v2/"):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {key.lower(): value for key, value in self.headers.items()}
            decoded = gzip.decompress(body) if headers.get("content-encoding") == "gzip" else body
            payload = json.loads(decoded)
            self.server.capture("POST", self.path, headers, body, 202, payload)
            self.respond(202, {})
            return
        self.forward()

    def forward(self):
        headers = {key.lower(): value for key, value in self.headers.items() if key.lower() not in ("host", "connection", "content-length")}
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        target = urlsplit(self.server.sink)
        connection = HTTPConnection(target.hostname, target.port, timeout=10)
        try:
            connection.request(self.command, self.path, body, headers)
            response = connection.getresponse()
            result = response.read()
            self.server.capture(self.command, self.path, headers, body, response.status)
            self.respond(response.status, result)
        finally:
            connection.close()

    def respond(self, status, body, headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass
