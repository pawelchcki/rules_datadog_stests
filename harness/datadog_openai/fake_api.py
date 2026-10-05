"""A loopback-only OpenAI wire fixture; it never forwards model requests."""
import json
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler
from harness.datadog_backend.backend import BackendServer

PATHS = {"/v1/completions", "/v1/chat/completions", "/v1/responses", "/v1/embeddings"}
ANSWER = "Hello from the local model fixture."


def response(path, request):
    if request["model"] == "bad-model":
        return 400, {"error": {"message": "Model bad-model does not exist", "type": "invalid_request_error", "param": "model", "code": "model_not_found"}}
    usage = {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16, "prompt_tokens_details": {"cached_tokens": 2}}
    common = {"id": "local-response", "created": 1700000000, "model": request["model"] + "-fixture"}
    if path == "/v1/completions":
        return 200, dict(common, object="text_completion", choices=[{"text": ANSWER, "index": 0, "finish_reason": "stop", "logprobs": None}], usage=usage)
    if path == "/v1/chat/completions":
        return 200, dict(common, object="chat.completion", choices=[{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop", "logprobs": None}], usage=usage)
    if path == "/v1/embeddings":
        return 200, {"object": "list", "model": common["model"], "data": [{"object": "embedding", "index": 0, "embedding": [0.125, 0.25, 0.5]}], "usage": {"prompt_tokens": 7, "total_tokens": 7}}
    return 200, {"id": "resp_local", "object": "response", "created_at": 1700000000, "status": "completed", "model": common["model"],
                 "output": [{"id": "msg_local", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": ANSWER, "annotations": []}]}],
                 "usage": {"input_tokens": 7, "output_tokens": 9, "total_tokens": 16, "input_tokens_details": {"cached_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 0}}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        request = json.loads(body)
        target = urlsplit(self.path)
        assert target.scheme == "http" and target.hostname == "api.openai.com" and not target.query
        path = target.path
        assert path in PATHS, path
        assert self.headers.get("Authorization") == "Bearer local-openai-test-key"
        status, document = response(path, request)
        headers = dict(self.headers)
        headers["Authorization"] = "[redacted]"
        self.server.capture(path, headers, body, status, {"request": request, "response": document}, None)
        encoded = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("x-request-id", "req-local-fixture")
        self.end_headers()
        self.wfile.write(encoded)


def server(output):
    instance = BackendServer(("127.0.0.1", 0), output)
    instance.RequestHandlerClass = Handler
    return instance
