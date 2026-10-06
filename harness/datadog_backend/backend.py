"""Local fake Datadog intake with authenticated, retained Agent requests."""
import gzip
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlsplit
import zlib

from harness.datadog_backend.wire import msgpack, protobuf

# Deliberately fake: only for the loopback integration harness.
API_KEY = "00000000000000000000000000000001"
ORG_UUID = "00000000-0000-0000-0000-000000000001"
MAX_BODY = 32 * 1024 * 1024
JSON_PATHS = {"/api/v2/apmtelemetry", "/api/v2/apmtelemetry/", "/api/v2/llmobs", "/api/v2/series", "/api/v1/series", "/api/v1/check_run", "/api/v1/metadata", "/intake/"}
METRIC_SCHEMA = json.loads(Path(__file__).with_name("metrics_schema.json").read_text())["messages"]
PROTOBUF_PATHS = {"/api/v2/series": ".datadog.agentpayload.MetricPayload", "/api/beta/sketches": ".datadog.agentpayload.SketchPayload"}


def read_request_body(headers, stream, limit):
    """Read bounded SDK bodies, including native writer chunked uploads."""
    lengths = headers.get_all("Content-Length", [])
    encodings = headers.get_all("Transfer-Encoding", [])
    if encodings:
        if lengths or len(encodings) != 1 or encodings[0].lower() != "chunked":
            raise ValueError("ambiguous or unsupported request framing")
        chunks, total = [], 0
        while True:
            line = stream.readline(1025)
            if not line.endswith(b"\r\n") or len(line) > 1024:
                raise ValueError("invalid chunk size line")
            size_text = line[:-2].split(b";", 1)[0]
            if not re.fullmatch(rb"[0-9a-fA-F]{1,16}", size_text):
                raise ValueError("invalid chunk size")
            size = int(size_text, 16)
            if size == 0:
                # The native SDK writer emits no trailers. Do not silently
                # discard additional request metadata from an unknown sender.
                if stream.readline(1025) != b"\r\n":
                    raise ValueError("unsupported or truncated chunk trailers")
                return b"".join(chunks)
            total += size
            if total > limit:
                raise ValueError("oversized chunked request")
            chunk = stream.read(size)
            if len(chunk) != size or stream.read(2) != b"\r\n":
                raise ValueError("truncated chunk")
            chunks.append(chunk)
    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
        raise ValueError("invalid Content-Length")
    size = int(lengths[0]) if lengths else 0
    if size > limit:
        raise ValueError("oversized request")
    body = stream.read(size)
    if len(body) != size:
        raise ValueError("truncated request")
    return body


def decompress(body, encoding):
    if encoding in ("", "identity"):
        return body
    if encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            result = stream.read(MAX_BODY + 1)
    elif encoding == "deflate":
        decoder = zlib.decompressobj()
        result = decoder.decompress(body, MAX_BODY + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("oversized, truncated or trailing deflate body")
    elif encoding == "zstd":
        # The pinned Agent binary always uses Zstandard. The library may be
        # supplied from its pinned rootfs, avoiding a host package dependency.
        library = ctypes.CDLL(os.environ.get("DATADOG_BACKEND_ZSTD_LIBRARY", "libzstd.so.1"))
        library.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        library.ZSTD_decompress.restype = ctypes.c_size_t
        library.ZSTD_isError.argtypes = [ctypes.c_size_t]
        library.ZSTD_isError.restype = ctypes.c_uint
        destination = ctypes.create_string_buffer(MAX_BODY)
        size = library.ZSTD_decompress(destination, MAX_BODY, body, len(body))
        if library.ZSTD_isError(size):
            raise ValueError("invalid or oversized zstd body")
        result = destination.raw[:size]
    else:
        raise ValueError("unsupported content encoding: " + encoding)
    if len(result) > MAX_BODY:
        raise ValueError("decompressed payload exceeds size limit")
    return result


def decode_payload(path, headers, body):
    body = decompress(body, headers.get("content-encoding", "").lower())
    content_type = headers.get("content-type", "").split(";", 1)[0].lower()
    if path in PROTOBUF_PATHS and content_type == "application/x-protobuf":
        return protobuf(body, PROTOBUF_PATHS[path], catalog=METRIC_SCHEMA)
    if path == "/api/v0.2/traces":
        if content_type != "application/x-protobuf":
            raise ValueError("Agent traces require application/x-protobuf")
        if headers.get("dd-protocol") == "otlp":
            raise ValueError("OTLP is not AgentPayload")
        result = protobuf(body)
        if not result.get("tracer_payloads") and not result.get("idx_tracer_payloads"):
            raise ValueError("Agent trace intake has no tracer payloads")
        return result
    if path == "/api/v0.2/stats":
        if content_type != "application/msgpack":
            raise ValueError("Agent stats require application/msgpack")
        result = msgpack(body)
        if not isinstance(result, dict):
            raise ValueError("Agent stats must be a map")
        return result
    if path in JSON_PATHS:
        return json.loads(body) if body else None
    raise ValueError("unsupported intake route")


class BackendServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, output_dir, api_key=API_KEY):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.records = []
        self.lock = threading.Lock()
        super().__init__(address, Handler)

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def capture(self, path, headers, body, status, payload=None, error=None):
        # Neither keys nor query strings are persisted in evidence.
        headers = {key: value for key, value in headers.items()
                   if key not in ("dd-api-key", "authorization", "proxy-authorization", "cookie")}
        with self.lock:
            index = len(self.records)
            stem = f"request-{index:06d}"
            record = {"index": index, "path": path, "headers": headers,
                      "status": status, "raw_sha256": hashlib.sha256(body).hexdigest(),
                      "raw_size": len(body), "raw_file": stem + ".body", "payload": payload}
            if error:
                record["error"] = error
            (self.output_dir / (stem + ".body")).write_bytes(body)
            (self.output_dir / (stem + ".json")).write_text(json.dumps(record, indent=2) + "\n")
            self.records.append(record)
            # A final summary is always available even if the process is killed.
            temporary = self.output_dir / "requests.json.tmp"
            temporary.write_text(json.dumps(self.records, indent=2) + "\n")
            temporary.replace(self.output_dir / "requests.json")
            return record


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        # Core Agent validation uses the query key; APM uses the header. Both
        # remain secret in retained captures, and conflicting keys fail closed.
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get("api_key")
        header = self.headers.get("DD-API-KEY")
        if query is not None and (query != [self.server.api_key] or header is not None and header != self.server.api_key):
            return False
        return header == self.server.api_key or query == [self.server.api_key]

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.respond(200, {"status": "ok"})
        elif path == "/dump":
            self.respond(200, self.server.snapshot())
        elif path in ("/api/v1/validate", "/api/v2/validate"):
            valid = self.authenticated()
            status = 200 if valid else 403
            payload = {"data": {"id": ORG_UUID}} if path.endswith("v2/validate") and valid else {"valid": valid}
            headers = {key.lower(): value for key, value in self.headers.items()}
            self.server.capture(path, headers, b"", status, payload, None if valid else "invalid API key")
            self.respond(status, payload)
        else:
            self.respond(404, {"error": "unknown route"})

    def do_CONNECT(self):
        # Core labs use this server as a deny proxy for unrelated destinations.
        # Known loopback intake URLs bypass it via explicit no_proxy config.
        self.server.capture("/forbidden-connect", {}, b"", 403, error="external destination forbidden")
        self.respond(403, {"error": "external destination forbidden"})

    def do_POST(self):
        path = urlsplit(self.path).path
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, payload, error = 200, None, None
        # Agent writers provide Content-Length; disallow ambiguous/chunked framing.
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or "transfer-encoding" in headers:
                raise ValueError("one Content-Length and no Transfer-Encoding required")
            length = int(lengths[0])
            if length < 0 or length > MAX_BODY:
                raise ValueError("request exceeds size limit")
        except ValueError as exc:
            self.close_connection = True
            self.respond(400, {"error": str(exc)})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self.respond(400, {"error": "truncated request"})
            return
        if not self.authenticated():
            status, error = 403, "invalid API key"
        elif path not in JSON_PATHS | PROTOBUF_PATHS.keys() | {"/api/v0.2/traces", "/api/v0.2/stats"}:
            status, error = 404, "unknown intake route"
        else:
            try:
                payload = decode_payload(path, headers, body)
            except (ValueError, UnicodeError, OSError, EOFError, zlib.error) as exc:
                status, error = 400, str(exc)
        self.server.capture(path, headers, body, status, payload, error)
        self.respond(status, {"error": error} if error else {})
