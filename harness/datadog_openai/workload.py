"""Run the real OpenAI SDK against the lab's loopback API service."""
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
import openai
import httpx
import ddtrace
from ddtrace import tracer
from ddtrace.llmobs import LLMObs

ddtrace.patch(openai=True)
if args.mode == "llmobs":
    LLMObs.enable(integrations_enabled=False, agentless_enabled=False, ml_app="openai-lab", service="openai-lab")
# The real HTTP transport uses only the loopback proxy. Retain the provider's
# hostname so ddtrace identifies OpenAI from the SDK base URL, just as upstream.
client = openai.OpenAI(api_key="local-openai-test-key", base_url="http://api.openai.com/v1", max_retries=0,
                       http_client=httpx.Client(proxy=args.api_url, trust_env=False))
identity = {"client_version": openai.__version__, "tracer_version": ddtrace.__version__, "calls": [],
            "dependencies": {name: importlib.metadata.version(name) for name in ("openai", "httpx", "pydantic", "jiter")}}
for api in ("completions", "chat", "responses", "embeddings"):
    for error in (False, True):
        model = {"completions": "gpt-3.5-turbo-instruct", "chat": "gpt-3.5-turbo", "responses": "gpt-4.1", "embeddings": "text-embedding-ada-002"}[api]
        if error:
            model = "bad-model"
        with tracer.trace("openai.lab.case", resource=api + (".error" if error else ".success"), span_type="web" if args.mode == "appsec" else None) as control:
            record = {"api": api, "error": error, "trace_id": str(control.trace_id), "parent_id": str(control.span_id)}
            try:
                if api == "completions":
                    response = client.completions.create(model=model, prompt="Hello OpenAI!", max_tokens=35)
                    record["output"] = response.choices[0].text
                elif api == "chat":
                    response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Hello OpenAI!"}], max_tokens=35, stream=False)
                    record["output"] = response.choices[0].message.content
                elif api == "responses":
                    response = client.responses.create(model=model, input="Hello OpenAI!", max_output_tokens=50, temperature=0.1)
                    record["output"] = response.output_text
                else:
                    response = client.embeddings.create(model=model, input="Hello OpenAI!", encoding_format="float")
                    record["output"] = response.data[0].embedding
                assert not error, "Expected real SDK BadRequestError"
            except openai.BadRequestError as exc:
                assert error and exc.status_code == 400
                record["exception"] = type(exc).__name__
            identity["calls"].append(record)
client.close()
if args.mode == "llmobs":
    LLMObs.flush()
    LLMObs.disable()
else:
    tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity, indent=2) + "\n")
