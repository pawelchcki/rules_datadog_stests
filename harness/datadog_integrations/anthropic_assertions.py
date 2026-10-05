"""Checks against emitted SDK and Agent evidence, with controlled Anthropic fixtures."""
from harness.datadog_integrations.anthropic_api import ANSWER, PATH

MODEL = "claude-sonnet-4-5-20250929"
PROMPT = "What is 2+2?"
INPUT_MESSAGES = [{"role": "user", "content": PROMPT}]
OUTPUT_MESSAGES = [{"role": "assistant", "content": ANSWER}]
ERROR_OUTPUT = [{"role": "", "content": ""}]
RESOURCES = {"create": "Messages.create", "create_stream": "Messages.create", "stream_method": "Messages.stream",
             "error": "Messages.create"}
STREAM_CASES = ("create_stream", "stream_method")
# Mirrors the fake fixture usage: input_tokens already normalizes cache tokens in.
METRICS = {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21, "cache_write_input_tokens": 3,
           "cache_read_input_tokens": 2, "ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0}


def check_client(identity, records):
    assert identity["client_version"] == "0.75.0" and len(identity["calls"]) == len(records) == 4, (identity, records)
    for call, record in zip(identity["calls"], records):
        assert record["path"] == PATH, record
        assert record["status"] == (400 if call["case"] == "error" else 200), record
        request = record["payload"]["request"]
        assert request["model"] == ("bad-model" if call["case"] == "error" else MODEL), request
        assert request["messages"] == INPUT_MESSAGES and request["max_tokens"] == 100, request
        assert request["temperature"] == 0.5 and request.get("stream", False) == (call["case"] in STREAM_CASES), request
        if call["case"] == "error":
            assert call["exception"] == "BadRequestError", call
            assert record["payload"]["response"]["error"]["type"] == "invalid_request_error", record
        else:
            assert call["output"] == ANSWER, call
            response = record["payload"]["response"]
            if call["case"] in STREAM_CASES:
                deltas = [event["data"]["delta"]["text"] for event in response["events"]
                          if event["event"] == "content_block_delta"]
                assert "".join(deltas) == ANSWER, response
            else:
                assert response["content"] == [{"type": "text", "text": ANSWER}], response
                assert response["usage"]["output_tokens"] == 9, response


def check_apm(spans, identity):
    assert len([s for s in spans if s["name"] == "anthropic.request"]) == 4, spans
    for call in identity["calls"]:
        error = call["case"] == "error"
        matches = [s for s in spans if s["name"] == "anthropic.request" and s.get("parent_id") == int(call["parent_id"])]
        assert len(matches) == 1, (call, matches, spans)
        span = matches[0]
        assert span["resource"] == RESOURCES[call["case"]], span
        assert span["trace_id"] == int(call["trace_id"]) & ((1 << 64) - 1), span
        assert span["duration"] > 0 and span["start"] > 0 and span["span_id"] > 0, span
        assert span["service"] == "anthropic-lab", span
        assert span["meta"]["anthropic.request.model"] == ("bad-model" if error else MODEL), span
        assert bool(span.get("error")) == error, span
        if error:
            assert "BadRequestError" in span["meta"]["error.type"] and "bad-model" in span["meta"]["error.message"], span


def check_llm(spans, identity):
    assert len(spans) == 4, spans
    for call in identity["calls"]:
        error = call["case"] == "error"
        candidates = [s for s in spans if int(s["_dd"]["apm_trace_id"], 16) == int(call["trace_id"])]
        assert len(candidates) == 1, (call, candidates)
        span = candidates[0]
        meta = span["meta"]
        assert span["name"] == "anthropic.request", span
        assert meta["span"]["kind"] == "llm", span
        assert meta["model_name"] == ("bad-model" if error else MODEL), span
        assert meta["model_provider"] == "anthropic" and span["duration"] > 0 and span["start_ns"] > 0, span
        assert int(span["span_id"]) > 0, span
        assert {"max_tokens": 100, "temperature": 0.5}.items() <= meta["metadata"].items(), span
        assert meta["input"]["messages"] == INPUT_MESSAGES, span
        assert "integration:anthropic" in span["tags"] and "ml_app:anthropic-lab" in span["tags"], span
        assert span["status"] == ("error" if error else "ok"), span
        if error:
            assert meta["output"] == {"messages": ERROR_OUTPUT}, span
            assert "BadRequestError" in meta["error"]["type"] and "bad-model" in meta["error"]["message"], span
            assert span.get("metrics", {}) == {}, span
        else:
            assert meta["output"] == {"messages": OUTPUT_MESSAGES}, span
            assert METRICS.items() <= span["metrics"].items(), span


CHECKS = {"apm": ("apm_anthropic_messages", check_apm), "llmobs": ("llm_observability_anthropic_messages", check_llm)}
