"""Authenticated native RC protobuf backend with signed, pinned TUF stages."""
import hashlib
import json
from pathlib import Path
from harness.datadog_backend.backend import API_KEY, MAX_BODY, ORG_UUID, decompress
from harness.datadog_backend.wire import protobuf
from harness.datadog_telemetry.probe import TelemetryBackendHandler

FIXTURES = json.loads(Path(__file__).with_name("signed-fixtures.json").read_text())
SCHEMA = json.loads(Path(__file__).with_name("schema.json").read_text())["messages"]
ROUTES = {"/api/v0.1/configurations", "/api/v0.1/org", "/api/v0.1/status"}


def varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def scalar(field, number):
    return varint(field << 3) + varint(number)


def blob(field, value):
    if isinstance(value, str): value = value.encode()
    return varint((field << 3) | 2) + varint(len(value)) + value


def encode(document):
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def response(stage, fixtures=FIXTURES):
    top = lambda version, document: scalar(1, version) + blob(2, encode(document))
    metas = blob(1, top(1, fixtures["root"]))
    for field, name in [(2, "timestamp"), (3, "snapshot"), (4, "targets")]:
        metas += blob(field, top(stage["version"], stage[name]))
    return blob(1, metas) + blob(2, metas) + b"".join(blob(3, blob(1, path) + blob(2, encode(content))) for path, content in stage["files"].items())


class Handler(TelemetryBackendHandler):
    def handle(self):
        try:
            super().handle()
        except BrokenPipeError:
            # Controlled Agent shutdown can close a pooled TLS connection while
            # the HTTP server waits for its next request.
            pass

    def respond(self, status, payload):
        try:
            super().respond(status, payload)
        except BrokenPipeError:
            # Core Agent cancels unrelated metric uploads during test shutdown;
            # their request bytes are already retained by the base handler.
            pass

    def do_GET(self):
        if self.path in ROUTES: self.rc()
        else: super().do_GET()

    def do_POST(self):
        if self.path in ROUTES: self.rc()
        else: super().do_POST()

    def rc(self):
        assert self.headers.get("DD-API-KEY") == API_KEY, "RC API key mismatch"
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 <= length <= MAX_BODY and not self.headers.get("Transfer-Encoding")
        body = self.rfile.read(length)
        assert len(body) == length
        payload = None
        if self.path == "/api/v0.1/configurations":
            payload = protobuf(decompress(body, self.headers.get("Content-Encoding", "")), ".datadog.config.LatestConfigsRequest", catalog=SCHEMA)
            fixtures = getattr(self.server, "rc_fixtures", FIXTURES)
            stage = fixtures["stages"][self.server.rc_stage - 1]
            result = response(stage, fixtures)
            version = stage["version"]
        elif self.path == "/api/v0.1/org":
            result, version = blob(1, ORG_UUID), 0
        else:
            result, version = scalar(1, 1) + scalar(2, 1), 0
        digest = hashlib.sha256(result).hexdigest()
        response_file = "rc-response-" + digest + ".body"
        (self.server.output_dir / response_file).write_bytes(result)
        self.server.capture(self.path, {key.lower(): value for key, value in self.headers.items()}, body, 200,
                            {"request": payload, "version": version, "response_file": response_file, "response_sha256": digest})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)
