"""A loopback-only Google Gemini REST fixture; it never forwards model requests.

Shapes mirror the Gemini API (mldev surface) that the pinned google-genai SDK
1.55.0 speaks: POST /v1beta/models/{model}:generateContent, the
:streamGenerateContent?alt=sse variant, and :batchEmbedContents. Responses use
camelCase wire fields and only keys the SDK's pydantic models accept.
"""
import json
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler

from harness.datadog_backend.backend import BackendServer

API_KEY = "local-google-genai-test-key"
ANSWER = "Hello from the local model fixture."
REASONING_TEXT = "The user wants to solve x + 9 = 10. Subtracting 9 from both sides isolates x, so x = 1."
REASONING_ANSWER = "Subtract 9 from both sides: x = 10 - 9, so x = 1."
WEATHER_ANSWER = "The weather in Tokyo is sunny with a temperature of 78°F.\n"
EXECUTABLE_CODE = "def is_prime(n):\n    return n > 1 and all(n % i for i in range(2, int(n ** 0.5) + 1))\nprint(sum(p for p in range(2, 250) if is_prime(p)))"
CODE_SUMMARY = "The code enumerates the first 50 prime numbers and sums them."
CODE_OUTPUT = "5117"
CODE_CONCLUSION = "The sum of the first 50 prime numbers is 5117."
GET_WEATHER_CALL_ID = "call-get-weather-1"

USAGE = {
    "generate": {"promptTokenCount": 7, "candidatesTokenCount": 9, "totalTokenCount": 16},
    "reasoning": {"promptTokenCount": 12, "candidatesTokenCount": 20, "thoughtsTokenCount": 15, "totalTokenCount": 47},
    "tools_call": {"promptTokenCount": 11, "candidatesTokenCount": 8, "totalTokenCount": 19},
    "weather": {"promptTokenCount": 21, "candidatesTokenCount": 12, "totalTokenCount": 33},
    "code": {"promptTokenCount": 18, "candidatesTokenCount": 25, "totalTokenCount": 43},
}


def generate_document(kind, model):
    candidates = {
        "generate": [{"content": {"role": "model", "parts": [{"text": ANSWER}]}, "finishReason": "STOP", "index": 0}],
        "reasoning": [{"content": {"role": "model", "parts": [{"thought": True, "thoughtSignature": "c2lnLWxvY2Fs",
            "text": REASONING_TEXT}, {"text": REASONING_ANSWER}]}, "finishReason": "STOP", "index": 0}],
        "tools_call": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "get_weather",
            "args": {"location": "Tokyo"}, "id": GET_WEATHER_CALL_ID}}]}, "finishReason": "STOP", "index": 0}],
        "weather": [{"content": {"role": "model", "parts": [{"text": WEATHER_ANSWER}]}, "finishReason": "STOP", "index": 0}],
        "code": [{"content": {"role": "model", "parts": [{"text": CODE_SUMMARY},
            {"executableCode": {"language": "PYTHON", "code": EXECUTABLE_CODE}},
            {"codeExecutionResult": {"outcome": "OUTCOME_OK", "output": CODE_OUTPUT}},
            {"text": CODE_CONCLUSION}]}, "finishReason": "STOP", "index": 0}],
    }
    return {"candidates": candidates[kind], "modelVersion": model, "usageMetadata": USAGE[kind]}


def stream_chunks(kind, model):
    document = generate_document(kind, model)
    usage = document.pop("usageMetadata")
    parts = document["candidates"][0]["content"]["parts"]
    first = {"candidates": [{"content": {"role": "model", "parts": parts[:1]}, "index": 0}], "modelVersion": model}
    second = {"candidates": [{"content": {"role": "model", "parts": parts[1:]}, "finishReason": "STOP", "index": 0}],
              "modelVersion": model, "usageMetadata": usage}
    return [first, second]


def embed_document(model, request):
    dimensionality = request.get("outputDimensionality") or request.get("requests", [{}])[0].get("outputDimensionality") or 8
    return {"embeddings": [{"values": [round(0.125 + index * 0.25, 6) for index in range(dimensionality)]}]}


def classify_generate(model, request):
    if model == "bad-model":
        return None
    if model == "gemini-2.5-pro":
        return "reasoning"
    if model == "gemini-2.5-flash":
        return "code"
    serialized = json.dumps(request.get("contents", []))
    if "functionResponse" in serialized or "functionCall" in serialized:
        return "weather"
    if request.get("tools"):
        return "tools_call"
    return "generate"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        request = json.loads(body)
        target = urlsplit(self.path)
        assert target.scheme == "http" and target.hostname == "generativelanguage.googleapis.com", self.path
        path = target.path
        assert path.startswith("/v1beta/models/"), path
        model, _, action = path.rsplit("/", 1)[-1].partition(":")
        assert action in ("generateContent", "streamGenerateContent", "embedContent", "batchEmbedContents"), path
        if action == "streamGenerateContent":
            assert target.query == "alt=sse", target.query
        assert self.headers.get("x-goog-api-key") == API_KEY, self.headers
        headers = dict(self.headers)
        headers["x-goog-api-key"] = "[redacted]"
        if action in ("embedContent", "batchEmbedContents"):
            if model == "bad-model":
                document = {"error": {"code": 400, "message": "Model bad-model does not exist", "status": "INVALID_ARGUMENT"}}
                status = 400
            else:
                document, status = embed_document(model, request), 200
            self.server.capture(path, headers, body, status, {"request": request, "response": document}, None)
            self._respond(status, document, "application/json")
            return
        kind = classify_generate(model, request)
        if kind is None:
            document = {"error": {"code": 400, "message": "Model bad-model does not exist", "status": "INVALID_ARGUMENT"}}
            self.server.capture(path, headers, body, 400, {"request": request, "response": document}, None)
            self._respond(400, document, "application/json")
            return
        if action == "generateContent":
            document = generate_document(kind, model)
            self.server.capture(path, headers, body, 200, {"request": request, "response": document}, None)
            self._respond(200, document, "application/json")
        else:
            chunks = stream_chunks(kind, model)
            self.server.capture(path, headers, body, 200, {"request": request, "response": {"stream": chunks}}, None)
            payload = "".join("data: %s\r\n\r\n" % json.dumps(chunk) for chunk in chunks)
            self._respond(200, payload, "text/event-stream")

    def _respond(self, status, document, content_type):
        encoded = document if isinstance(document, bytes) else (document.encode() if content_type == "text/event-stream" else json.dumps(document).encode())
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
