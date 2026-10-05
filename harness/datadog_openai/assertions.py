"""Checks against emitted SDK and Agent evidence, with controlled API fixtures."""
from harness.datadog_openai.fake_api import ANSWER

RESOURCES = {"completions": "createCompletion", "chat": "createChatCompletion", "responses": "createResponse", "embeddings": "createEmbedding"}
MODELS = {"completions": "gpt-3.5-turbo-instruct", "chat": "gpt-3.5-turbo", "responses": "gpt-4.1", "embeddings": "text-embedding-ada-002"}
PATHS = {"completions": "/v1/completions", "chat": "/v1/chat/completions", "responses": "/v1/responses", "embeddings": "/v1/embeddings"}


def check_client(identity, records):
    assert identity["client_version"] == "2.41.1" and len(identity["calls"]) == len(records) == 8, (identity, records)
    for call, record in zip(identity["calls"], records):
        assert record["path"] == PATHS[call["api"]], record
        assert record["status"] == (400 if call["error"] else 200), record
        request = record["payload"]["request"]
        assert request["model"] == ("bad-model" if call["error"] else MODELS[call["api"]]), request
        if call["api"] == "chat":
            assert request["messages"] == [{"role": "user", "content": "Hello OpenAI!"}]
        else:
            assert request["prompt" if call["api"] == "completions" else "input"] == "Hello OpenAI!"
        if call["error"]:
            assert call["exception"] == "BadRequestError", call
        else:
            assert call["output"] == ([0.125, 0.25, 0.5] if call["api"] == "embeddings" else ANSWER), call


def check_apm(spans, identity, api):
    calls = [c for c in identity["calls"] if c["api"] == api]
    assert len(calls) == 2
    for call in calls:
        matches = [s for s in spans if s["name"] == "openai.request" and s.get("parent_id") == int(call["parent_id"])]
        assert len(matches) == 1, (api, call, matches, spans)
        span = matches[0]
        assert span["resource"] == RESOURCES[api], span
        assert span["trace_id"] == int(call["trace_id"]) & ((1 << 64) - 1), span
        assert span["duration"] > 0 and span["start"] > 0 and span["span_id"] > 0, span
        assert span["service"] == "openai-lab" and span["meta"]["component"] == "openai", span
        assert span["meta"]["openai.request.model"] == ("bad-model" if call["error"] else MODELS[api]), span
        assert span.get("error", 0) == int(call["error"]), span
        if call["error"]:
            assert "BadRequestError" in span["meta"]["error.type"] and "bad-model" in span["meta"]["error.message"], span


def check_llm(spans, identity, apis):
    calls = [c for c in identity["calls"] if c["api"] in apis]
    assert len(spans) == 8, spans
    for call in calls:
        candidates = [s for s in spans if int(s["_dd"]["apm_trace_id"], 16) == int(call["trace_id"])]
        assert len(candidates) == 1, (call, candidates)
        span = candidates[0]
        api = call["api"]
        meta = span["meta"]
        assert span["name"] == "OpenAI." + RESOURCES[api], span
        assert meta["span"]["kind"] == ("embedding" if api == "embeddings" else "llm"), span
        assert meta["model_name"] == ("bad-model" if call["error"] else MODELS[api] + "-fixture"), span
        assert meta["model_provider"] == "openai" and span["duration"] > 0 and span["start_ns"] > 0, span
        assert int(span["span_id"]) > 0, span
        parameters = {"completions": {"max_tokens": 35}, "chat": {"max_tokens": 35, "stream": False},
                      "responses": {"max_output_tokens": 50, "temperature": 0.1}, "embeddings": {"encoding_format": "float"}}[api]
        assert parameters.items() <= meta["metadata"].items(), span
        assert "integration:openai" in span["tags"] and "ml_app:openai-lab" in span["tags"], span
        if api == "embeddings":
            assert meta["input"]["documents"] == [{"text": "Hello OpenAI!"}], span
        else:
            assert meta["input"]["messages"] == [{"role": "user" if api in ("chat", "responses") else "", "content": "Hello OpenAI!"}], span
        assert span["status"] == ("error" if call["error"] else "ok"), span
        if call["error"]:
            assert "BadRequestError" in meta["error"]["type"] and "bad-model" in meta["error"]["message"], span
            if api == "embeddings":
                assert "output" not in meta, span
            else:
                # Current Python emits an empty placeholder on failed text APIs.
                assert meta["output"] == {"messages": [{"role": "", "content": ""}]}, span
            assert span["metrics"] == {}, span
        else:
            assert span["metrics"]["input_tokens"] == 7, span
            assert span["metrics"]["total_tokens"] == (7 if api == "embeddings" else 16), span
            if api == "embeddings":
                assert meta["output"]["value"] == "[1 embedding(s) returned with size 3]", span
            else:
                assert span["metrics"]["output_tokens"] == 9 and span["metrics"]["cache_read_input_tokens"] == 2, span
                assert meta["output"]["messages"] == [{"role": "assistant" if api in ("chat", "responses") else "", "content": ANSWER}], span


def check_completions(spans, identity): check_apm(spans, identity, "completions")
def check_chat(spans, identity): check_apm(spans, identity, "chat")
def check_responses(spans, identity): check_apm(spans, identity, "responses")
def check_embeddings(spans, identity): check_apm(spans, identity, "embeddings")
def check_llm_interactions(spans, identity): check_llm(spans, identity, ("completions", "chat", "responses"))
def check_llm_embeddings(spans, identity): check_llm(spans, identity, ("embeddings",))

CHECKS = {
    "apm": [("apm_openai_completions", check_completions), ("apm_openai_chat_completions", check_chat),
            ("apm_openai_responses", check_responses), ("apm_openai_embeddings", check_embeddings)],
    "llmobs": [("llm_observability_openai_llm_interactions", check_llm_interactions), ("llm_observability_openai_embeddings", check_llm_embeddings)],
}
