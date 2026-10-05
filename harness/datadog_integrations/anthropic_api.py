"""A loopback-only Anthropic Messages wire fixture; it never forwards model requests."""
import json
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler

from harness.datadog_backend.backend import BackendServer

PATH = "/v1/messages"
ANSWER = "2+2 = 4"
API_KEY = "local-anthropic-test-key"
# Token accounting mirrors what the upstream VCR cassettes carry so the pinned
# SDK and ddtrace extract the same cache-token metric shapes upstream asserts.
USAGE = {"input_tokens": 7, "output_tokens": 9, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 2}
DELTAS = ("2+2", " = ", "4")


def completion(model):
    return {"id": "msg_local_fixture", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": ANSWER}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": dict(USAGE)}


def stream_events(model):
    return [
        ("message_start", {"type": "message_start", "message": {"id": "msg_local_fixture", "type": "message",
            "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": USAGE["input_tokens"],
                "cache_creation_input_tokens": USAGE["cache_creation_input_tokens"],
                "cache_read_input_tokens": USAGE["cache_read_input_tokens"]}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}),
        *[("content_block_delta", {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": delta}}) for delta in DELTAS],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": USAGE["output_tokens"]}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def encode_sse(events):
    return "".join("event: %s\ndata: %s\n\n" % (event, json.dumps(document)) for event, document in events).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        request = json.loads(body)
        target = urlsplit(self.path)
        assert target.scheme == "http" and target.hostname == "api.anthropic.com" and not target.query
        assert target.path == PATH, target.path
        assert self.headers.get("x-api-key") == API_KEY
        assert self.headers.get("anthropic-version")
        assert request["max_tokens"] > 0 and request["messages"], request
        headers = dict(self.headers)
        headers["x-api-key"] = "[redacted]"
        if request["model"] == "bad-model":
            status = 400
            document = {"type": "error", "error": {"type": "invalid_request_error",
                "message": "model: bad-model", "code": "model_not_found"}}
            encoded = json.dumps(document).encode()
            content_type = "application/json"
            self.server.capture(target.path, headers, body, status, {"request": request, "response": document}, None)
        elif request.get("stream"):
            events = stream_events(request["model"])
            status = 200
            encoded = encode_sse(events)
            content_type = "text/event-stream"
            self.server.capture(target.path, headers, body, status,
                                {"request": request, "response": {"stream": True, "events": [
                                    {"event": event, "data": data} for event, data in events]}}, None)
        else:
            status = 200
            document = completion(request["model"])
            encoded = json.dumps(document).encode()
            content_type = "application/json"
            self.server.capture(target.path, headers, body, status, {"request": request, "response": document}, None)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("x-request-id", "req-local-fixture")
        self.end_headers()
        self.wfile.write(encoded)


def server(output):
    instance = BackendServer(("127.0.0.1", 0), output)
    instance.RequestHandlerClass = Handler
    return instance
