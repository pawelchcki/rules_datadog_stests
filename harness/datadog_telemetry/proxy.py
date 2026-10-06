"""Retain original tracer telemetry before forwarding to the real Agent."""
from http.server import BaseHTTPRequestHandler
from http.client import HTTPConnection
import json
import time
from urllib.parse import urlsplit

from harness.datadog_backend.backend import BackendServer, decompress, read_request_body

MAX_BODY = 8 * 1024 * 1024
HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length"}


class CaptureProxy(BackendServer):
    def __init__(self, agent_url, output_dir):
        super().__init__(("127.0.0.1", 0), output_dir)
        self.agent_url = agent_url
        self.RequestHandlerClass = Handler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_PUT(self):
        self.forward()

    def forward(self):
        headers = {key.lower(): value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
        data, payload = b"", None
        try:
            data = read_request_body(self.headers, self.rfile, MAX_BODY)
            if self.path == "/telemetry/proxy/api/v2/apmtelemetry":
                payload = {"document": json.loads(decompress(data, headers.get("content-encoding", ""))),
                           "received_at": time.monotonic()}
            target = urlsplit(self.server.agent_url)
            connection = HTTPConnection(target.hostname, target.port, timeout=10)
            try:
                # urllib adds Connection: close, which the Agent telemetry
                # forwarder clones to its backend request. Keep the observed
                # tracer headers unchanged instead of adding that hop header.
                connection.request(self.command, self.path, data, headers)
                response = connection.getresponse()
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ValueError("oversized Agent response")
                status = response.status
                response_headers = {key: value for key, value in response.headers.items() if key.lower() not in HOP_HEADERS}
            finally:
                connection.close()
            self.server.capture(self.path, headers, data, status, payload)
            self.send_response(status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            self.server.capture(self.path, headers, data, 502, payload, repr(error))
            self.send_error(502, "telemetry capture proxy failed")

    def log_message(self, *_args):
        pass
