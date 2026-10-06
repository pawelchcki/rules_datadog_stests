"""Observe tracer intake while forwarding it to the real APM Agent."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from harness.datadog_backend.backend import read_request_body

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length"}


class IntakeProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, agent_url, sink_url, output_dir):
        super().__init__(("127.0.0.1", 0), Handler)
        self.agent_url = agent_url
        self.sink_url = sink_url
        self.records = []
        self.errors = []
        self.total_bytes = 0
        self.lock = threading.Lock()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps({"records": self.records, "errors": self.errors}))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_PUT(self):
        self.forward()

    def forward(self):
        try:
            data = read_request_body(self.headers, self.rfile, MAX_REQUEST_BYTES)
            length = len(data)
            headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
            with self.server.lock:
                self.server.total_bytes += length
                if self.server.total_bytes > MAX_CAPTURE_BYTES:
                    raise ValueError("intake capture overflow")
            # Keep the sink's independent native-wire decoder as an observation
            # point. The response returned to the SDK is always the Agent's.
            if self.command in ("POST", "PUT") and self.path in ("/v0.4/traces", "/v0.5/traces"):
                with urlopen(Request(self.server.sink_url + self.path, data, headers, method=self.command), timeout=5) as response:
                    if response.status != 200:
                        raise ValueError("observation sink rejected native traces")
            request = Request(self.server.agent_url + self.path, data if length else None, headers, method=self.command)
            try:
                response = urlopen(request, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                body = response.read(MAX_REQUEST_BYTES + 1)
                if len(body) > MAX_REQUEST_BYTES:
                    raise ValueError("oversized Agent response")
                status = response.status
                response_headers = {key: value for key, value in response.headers.items() if key.lower() not in HOP_HEADERS}
            with self.server.lock:
                raw_file = "request-" + str(len(self.server.records)) + ".body"
                (self.server.output_dir / raw_file).write_bytes(data)
                self.server.records.append({
                    "method": self.command, "path": self.path,
                    "requestHeaders": headers, "requestBytes": length,
                    "requestSha256": hashlib.sha256(data).hexdigest(),
                    "rawFile": "intake/" + raw_file,
                    "status": status, "responseHeaders": response_headers,
                    "responseBody": body.decode("utf-8", errors="replace"),
                })
            self.send_response(status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            with self.server.lock:
                self.server.errors.append(repr(error))
            self.send_error(502, "Agent intake proxy failed")

    def log_message(self, *_):
        pass
