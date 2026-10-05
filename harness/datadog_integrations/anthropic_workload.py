"""Run the real Anthropic SDK against the lab's loopback Messages API service."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
for name in ("sdk-overlay", "api-url", "identity-file", "mode"):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
sys.path.insert(0, args.sdk_overlay)
import anthropic
import httpx
import ddtrace
from ddtrace import tracer
from ddtrace.llmobs import LLMObs

ddtrace.patch(anthropic=True)
if args.mode == "llmobs":
    LLMObs.enable(integrations_enabled=False, agentless_enabled=False, ml_app="anthropic-lab", service="anthropic-lab")
# The real HTTP transport uses only the loopback proxy. Retain the provider's
# hostname so ddtrace resolves the anthropic model provider from the SDK base
# URL, just as the upstream weblog points at the test-agent VCR rewrite.
client = anthropic.Anthropic(api_key="local-anthropic-test-key", base_url="http://api.anthropic.com", max_retries=0,
                             http_client=httpx.Client(proxy=args.api_url, trust_env=False))
identity = {"client_version": anthropic.__version__, "tracer_version": ddtrace.__version__, "calls": [],
            "dependencies": {name: importlib.metadata.version(name) for name in ("anthropic", "httpx", "pydantic", "anyio")}}
MODEL, MESSAGES = "claude-sonnet-4-5-20250929", [{"role": "user", "content": "What is 2+2?"}]
PARAMS = {"max_tokens": 100, "temperature": 0.5}
EXPECTED = "2+2 = 4"


def invoke(case):
    error = case == "error"
    with tracer.trace("anthropic.lab.case", resource=case) as control:
        record = {"case": case, "trace_id": str(control.trace_id), "parent_id": str(control.span_id)}
        try:
            if case == "create_stream":
                stream = client.messages.create(model=MODEL, messages=MESSAGES, stream=True, **PARAMS)
                # The traced raw stream is iterated directly; text deltas prove
                # the pinned tracer rebuilt the response from stream chunks.
                record["output"] = "".join(chunk.delta.text for chunk in stream
                                           if chunk.type == "content_block_delta" and chunk.delta.type == "text_delta")
            elif case == "stream_method":
                with client.messages.stream(model=MODEL, messages=MESSAGES, **PARAMS) as stream:
                    record["output"] = "".join(stream.text_stream)
            else:
                response = client.messages.create(model="bad-model" if error else MODEL, messages=MESSAGES, **PARAMS)
                record["output"] = response.content[0].text
            assert not error, "Expected real SDK BadRequestError"
            assert record["output"] == EXPECTED, record
        except anthropic.BadRequestError as exc:
            assert error and exc.status_code == 400
            record["exception"] = type(exc).__name__
        identity["calls"].append(record)


invoke("create")
invoke("create_stream")
invoke("stream_method")
invoke("error")
client.close()
if args.mode == "llmobs":
    LLMObs.flush()
    LLMObs.disable()
else:
    tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity, indent=2) + "\n")
