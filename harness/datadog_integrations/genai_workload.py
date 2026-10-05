"""Run the real google-genai SDK against the lab's loopback Gemini API service."""
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
import httpx
from google import genai
from google.genai import types
import ddtrace
from ddtrace import tracer
from ddtrace.llmobs import LLMObs

ddtrace.patch(google_genai=True)
if args.mode == "llmobs":
    LLMObs.enable(integrations_enabled=False, agentless_enabled=False, ml_app="genai-lab", service="genai-lab")
# The real HTTP transport uses only the loopback proxy. Retain the provider's
# hostname in the SDK base URL, exactly as upstream does with its VCR mount;
# ddtrace derives the provider from the model name, not the URL.
client = genai.Client(
    api_key="local-google-genai-test-key",
    http_options=types.HttpOptions(
        base_url="http://generativelanguage.googleapis.com/",
        httpx_client=httpx.Client(proxy=args.api_url, trust_env=False),
    ),
)

GET_WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the weather in a given location",
    "parameters": {
        "type": "object",
        "properties": {"location": {"type": "string", "description": "The location to get the weather for"}},
    },
}
CHICKEN = "Why did the chicken cross the road?"
REASONING_MESSAGES = [
    {"parts": [{"text": "If x + 9 = 10, what is the value of x?"}], "role": "user"},
    {"parts": [{"text": "Since 1 + 9 = 10, the value of x is 1.", "thought": True}], "role": "model"},
    {"parts": [{"text": "The value of x is 1."}], "role": "model"},
    {"parts": [{"text": "What is that number plus 3?"}], "role": "user"},
]
TOOL_RESPONSE_MESSAGES = [
    {"parts": [{"text": "What is the weather in Tokyo?"}], "role": "user"},
    {"parts": [{"function_call": {"name": "get_weather", "args": {"location": "Tokyo"}, "id": "abc123"}}], "role": "model"},
    {"parts": [{"function_response": {"name": "get_weather",
                                      "response": {"weather": "sunny", "temperature": "78°F"}, "id": "abc123"}}],
     "role": "user"},
]

CASES = {
    "apm": [
        {"case": "generate", "model": "gemini-2.0-flash", "contents": CHICKEN,
         "config": {"temperature": 0.1, "max_output_tokens": 50}},
        {"case": "generate_stream", "model": "gemini-2.0-flash", "contents": CHICKEN,
         "config": {"temperature": 0.1, "max_output_tokens": 50}, "stream": True},
        {"case": "generate_error", "model": "bad-model", "contents": CHICKEN,
         "config": {"temperature": 0.1, "max_output_tokens": 50}, "error": True},
        {"case": "embed", "model": "text-embedding-004", "contents": CHICKEN, "config": {}},
    ],
    "llmobs": [
        {"case": "generate", "model": "gemini-2.0-flash", "contents": CHICKEN,
         "config": {"temperature": 0.1, "max_output_tokens": 50}},
        {"case": "generate_stream", "model": "gemini-2.0-flash", "contents": CHICKEN,
         "config": {"temperature": 0.1, "max_output_tokens": 50}, "stream": True},
        {"case": "reasoning_output", "model": "gemini-2.5-pro", "contents": "If x + 9 = 10, what is the value of x?",
         "config": {"thinking_config": {"thinking_budget": 1024, "include_thoughts": True}, "temperature": 0.1}},
        {"case": "reasoning_input", "model": "gemini-2.0-flash", "contents": REASONING_MESSAGES,
         "config": {"temperature": 0.1, "max_output_tokens": 50}},
        {"case": "tools_call", "model": "gemini-2.0-flash", "contents": "What is the weather in Tokyo?",
         "config": {"max_output_tokens": 50, "tools": [{"function_declarations": [GET_WEATHER_TOOL]}]}},
        {"case": "tool_responses", "model": "gemini-2.0-flash", "contents": TOOL_RESPONSE_MESSAGES,
         "config": {"max_output_tokens": 50, "temperature": 0.1}},
        {"case": "executable_code", "model": "gemini-2.5-flash",
         "contents": "What is the sum of the first 50 prime numbers? Generate and run code for the calculation, and make sure you get all 50.",
         "config": {"tools": [{"code_execution": {}}]}},
        {"case": "embed", "model": "text-embedding-004", "contents": CHICKEN, "config": {}},
        {"case": "embed_dimensions", "model": "text-embedding-004", "contents": CHICKEN,
         "config": {"output_dimensionality": 10}},
    ],
}


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


identity = {"client_version": genai.__version__, "tracer_version": ddtrace.__version__, "calls": [],
            "dependencies": {name: package_version(name) for name in ("google-genai", "httpx", "pydantic")}}
for spec in CASES[args.mode]:
    with tracer.trace("genai.lab.case", resource=spec["case"]) as control:
        record = {"case": spec["case"], "model": spec["model"], "stream": bool(spec.get("stream")),
                  "error": bool(spec.get("error")), "trace_id": str(control.trace_id), "parent_id": str(control.span_id)}
        try:
            if spec["case"].startswith("embed"):
                response = client.models.embed_content(model=spec["model"], contents=spec["contents"], config=spec["config"])
                record["output"] = "[%d embedding(s) returned with size %d]" % (len(response.embeddings),
                                                                                len(response.embeddings[0].values))
            elif spec.get("stream"):
                chunks = list(client.models.generate_content_stream(model=spec["model"], contents=spec["contents"],
                                                                    config=spec["config"]))
                record["output"] = "".join(chunk.text or "" for chunk in chunks)
                record["chunks"] = len(chunks)
            else:
                response = client.models.generate_content(model=spec["model"], contents=spec["contents"],
                                                          config=types.GenerateContentConfig(**spec["config"]))
                record["output"] = response.text
            assert not spec.get("error"), "Expected real SDK ClientError"
        except genai.errors.ClientError as exc:
            assert spec.get("error") and exc.code == 400, exc
            record["exception"] = type(exc).__name__
        identity["calls"].append(record)
client.close()
if args.mode == "llmobs":
    LLMObs.flush()
    LLMObs.disable()
else:
    tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity, indent=2) + "\n")
