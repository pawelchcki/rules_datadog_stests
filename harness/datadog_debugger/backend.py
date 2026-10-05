"""Retain real Agent debugger requests alongside signed RC deliveries."""
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from harness.datadog_backend.backend import API_KEY, MAX_BODY, decompress
from harness.datadog_remote_config.backend import Handler as RCHandler

FIXTURES = json.loads(Path(__file__).with_name("signed-fixtures.json").read_text())


def decode_document(body, content_type):
    if content_type.startswith("multipart/form-data"):
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        assert message.is_multipart() and not message.defects
        parts = list(message.iter_parts())
        assert len(parts) == 1 and parts[0].get_content_type() == "application/json"
        assert parts[0].get_param("name", header="Content-Disposition") == "event"
        body = parts[0].get_payload(decode=True)
    else:
        assert content_type.split(";", 1)[0] == "application/json", content_type
    value = json.loads(body)
    assert isinstance(value, list)
    return value


class Handler(RCHandler):
    def do_POST(self):
        target = urlsplit(self.path)
        if target.path != "/api/v2/debugger":
            return super().do_POST()
        assert self.headers.get("DD-API-KEY") == API_KEY
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 <= length <= MAX_BODY and not self.headers.get("Transfer-Encoding")
        body = self.rfile.read(length)
        document = decode_document(decompress(body, self.headers.get("Content-Encoding", "")), self.headers.get("Content-Type", ""))
        headers = {key.lower(): value for key, value in self.headers.items()}
        self.server.capture(target.path, headers, body, 200, {"document": document, "ddtags": parse_qs(target.query).get("ddtags", [])})
        self.respond(200, {})
