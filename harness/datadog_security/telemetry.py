"""Retain genuine SDK telemetry while relaying native intake to its decoder."""
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from harness.datadog_backend.backend import BackendServer, Handler as BackendHandler


class Handler(BackendHandler):
    def do_GET(self):
        self.forward()

    def do_PUT(self):
        self.forward()

    def do_POST(self):
        if self.path in ("/telemetry/proxy/api/v2/apmtelemetry", "/telemetry/proxy/api/v2/apmtelemetry/"):
            self.path = "/api/v2/apmtelemetry"
            super().do_POST()
        else:
            self.forward()

    def forward(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 8 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
            self.send_error(413)
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400)
            return
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in {"host", "content-length", "connection", "transfer-encoding"}}
        try:
            response = urlopen(Request(self.server.sink + self.path, body if length else None,
                                       headers, method=self.command), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            result = response.read(8 * 1024 * 1024 + 1)
            assert len(result) <= 8 * 1024 * 1024
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)


class TelemetryRelay(BackendServer):
    def __init__(self, sink, output):
        # Agent-bound SDK telemetry has no API-key header. All targets are loopback.
        super().__init__(("127.0.0.1", 0), output, api_key=None)
        self.sink = sink
        self.RequestHandlerClass = Handler
