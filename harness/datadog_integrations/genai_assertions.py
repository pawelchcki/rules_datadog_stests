"""Checks against emitted SDK and Agent evidence, with controlled Gemini fixtures."""
import json

from harness.datadog_integrations.genai_api import (ANSWER, CODE_CONCLUSION, CODE_OUTPUT, CODE_SUMMARY,
    EXECUTABLE_CODE, GET_WEATHER_CALL_ID, REASONING_ANSWER, REASONING_TEXT, USAGE, WEATHER_ANSWER)

CHICKEN = "Why did the chicken cross the road?"
GENERATE_QUESTION = "If x + 9 = 10, what is the value of x?"
WEATHER_QUESTION = "What is the weather in Tokyo?"
CODE_QUESTION = ("What is the sum of the first 50 prime numbers? Generate and run code for the calculation, "
                 "and make sure you get all 50.")
TOOL_RESULT = {"weather": "sunny", "temperature": "78°F"}

PATHS = {"generate": "/v1beta/models/gemini-2.0-flash:generateContent",
         "generate_stream": "/v1beta/models/gemini-2.0-flash:streamGenerateContent",
         "generate_error": "/v1beta/models/bad-model:generateContent",
         "reasoning_output": "/v1beta/models/gemini-2.5-pro:generateContent",
         "reasoning_input": "/v1beta/models/gemini-2.0-flash:generateContent",
         "tools_call": "/v1beta/models/gemini-2.0-flash:generateContent",
         "tool_responses": "/v1beta/models/gemini-2.0-flash:generateContent",
         "executable_code": "/v1beta/models/gemini-2.5-flash:generateContent",
         "embed": "/v1beta/models/text-embedding-004:batchEmbedContents",
         "embed_dimensions": "/v1beta/models/text-embedding-004:batchEmbedContents"}
RESOURCES = {"generate": "Models.generate_content", "generate_stream": "Models.generate_content_stream",
             "generate_error": "Models.generate_content", "embed": "Models.embed_content",
             "embed_dimensions": "Models.embed_content"}
PROVIDERS = {"bad-model": "unknown"}


def provider(model):
    return PROVIDERS.get(model, "google")


def check_client(identity, records):
    assert identity["client_version"] == "1.55.0", identity
    assert identity["dependencies"]["httpx"] is not None, identity
    assert len(identity["calls"]) == len(records), (identity["calls"], records)
    for call, record in zip(identity["calls"], records):
        case, request = call["case"], record["payload"]["request"]
        assert record["path"] == PATHS[case], record
        assert record["status"] == (400 if call["error"] else 200), record
        if case.startswith("embed"):
            assert request["requests"][0]["model"] == "models/" + call["model"], request
            assert request["requests"][0]["content"] == {"role": "user", "parts": [{"text": CHICKEN}]}, request
            if case == "embed_dimensions":
                assert request["requests"][0]["outputDimensionality"] == 10, request
            values = record["payload"]["response"]["embeddings"][0]["values"]
            assert call["output"] == "[%d embedding(s) returned with size %d]" % (1, len(values)), call
            continue
        assert call["model"] in ("bad-model",) or request["contents"], request
        if case == "generate":
            assert request["contents"] == [{"role": "user", "parts": [{"text": CHICKEN}]}], request
            assert {"temperature": 0.1, "maxOutputTokens": 50}.items() <= request["generationConfig"].items(), request
            assert call["output"] == ANSWER, call
        elif case == "generate_stream":
            assert request["contents"] == [{"role": "user", "parts": [{"text": CHICKEN}]}], request
            assert call["chunks"] == 2 and call["output"] == ANSWER, call
        elif case == "generate_error":
            assert call["exception"] == "ClientError", call
            assert "bad-model" in record["payload"]["response"]["error"]["message"], record
        elif case == "reasoning_output":
            assert request["contents"] == [{"role": "user", "parts": [{"text": GENERATE_QUESTION}]}], request
            assert {"temperature": 0.1,
                    "thinkingConfig": {"thinking_budget": 1024, "include_thoughts": True}}.items() \
                <= request["generationConfig"].items(), request
            assert call["output"] == REASONING_ANSWER, call
        elif case == "reasoning_input":
            contents = request["contents"]
            assert [content["role"] for content in contents] == ["user", "model", "model", "user"], contents
            assert contents[1]["parts"] == [{"text": "Since 1 + 9 = 10, the value of x is 1.", "thought": True}], contents
            assert call["output"] == ANSWER, call
        elif case == "tools_call":
            assert request["contents"] == [{"role": "user", "parts": [{"text": WEATHER_QUESTION}]}], request
            assert request["tools"][0]["functionDeclarations"][0]["name"] == "get_weather", request
            assert request["generationConfig"]["maxOutputTokens"] == 50, request
        elif case == "tool_responses":
            parts = [content["parts"] for content in request["contents"]]
            assert parts[1] == [{"functionCall": {"name": "get_weather", "args": {"location": "Tokyo"}, "id": "abc123"}}], parts
            assert parts[2] == [{"functionResponse": {"name": "get_weather", "response": TOOL_RESULT, "id": "abc123"}}], parts
            assert call["output"] == WEATHER_ANSWER, call
        elif case == "executable_code":
            assert request["contents"] == [{"role": "user", "parts": [{"text": CODE_QUESTION}]}], request
            assert request["tools"] == [{"codeExecution": {}}], request
            assert call["output"] == CODE_SUMMARY + CODE_CONCLUSION, call


def check_apm(spans, identity, cases=None):
    calls = {call["case"]: call for call in identity["calls"]
             if cases is None or call["case"] in cases}
    if cases is None:
        assert len([s for s in spans if s["name"] == "google_genai.request"]) == len(calls), spans
    for case, call in calls.items():
        matches = [s for s in spans if s["name"] == "google_genai.request" and s.get("parent_id") == int(call["parent_id"])]
        assert len(matches) == 1, (case, matches, spans)
        span = matches[0]
        assert span["resource"] == RESOURCES[case], span
        assert span["trace_id"] == int(call["trace_id"]) & ((1 << 64) - 1), span
        assert span["duration"] > 0 and span["start"] > 0 and span["span_id"] > 0, span
        assert span["service"] == "genai-lab", span
        assert span["meta"]["google_genai.request.model"] == call["model"], span
        assert span["meta"]["google_genai.request.provider"] == provider(call["model"]), span
        assert span.get("error", 0) == int(call["error"]), span
        if call["error"]:
            assert "ClientError" in span["meta"]["error.type"], span
            assert "bad-model" in span["meta"]["error.message"], span


def span_for_call(spans, call):
    candidates = [s for s in spans if int(s["_dd"]["apm_trace_id"], 16) == int(call["trace_id"])]
    assert len(candidates) == 1, (call, candidates)
    return candidates[0]


def check_llm_common(span, call):
    meta = span["meta"]
    assert span["name"] == "google_genai.request", span
    assert meta["model_name"] == call["model"] and meta["model_provider"] == provider(call["model"]), span
    assert span["duration"] > 0 and span["start_ns"] > 0 and int(span["span_id"]) > 0, span
    assert span["status"] == "ok", span
    assert "integration:google_genai" in span["tags"] and "ml_app:genai-lab" in span["tags"], span


def metrics_of(kind):
    usage = USAGE[kind]
    metrics = {"input_tokens": usage["promptTokenCount"],
               "output_tokens": usage["candidatesTokenCount"] + usage.get("thoughtsTokenCount", 0),
               "total_tokens": usage["totalTokenCount"]}
    if "thoughtsTokenCount" in usage:
        metrics["reasoning_output_tokens"] = usage["thoughtsTokenCount"]
    return metrics


def check_generate_llm(spans, identity):
    cases = {call["case"]: call for call in identity["calls"] if call["case"] in ("generate", "generate_stream")}
    for case, call in cases.items():
        span = span_for_call(spans, call)
        check_llm_common(span, call)
        meta = span["meta"]
        assert meta["span"]["kind"] == "llm", span
        assert {"temperature": 0.1, "max_output_tokens": 50}.items() <= meta["metadata"].items(), span
        assert meta["input"]["messages"] == [{"role": "user", "content": CHICKEN}], span
        assert meta["output"]["messages"] == [{"role": "assistant", "content": ANSWER}], span
        assert span["metrics"] == metrics_of("generate"), span


def check_reasoning_llm(spans, identity):
    cases = {call["case"]: call for call in identity["calls"] if call["case"].startswith("reasoning")}
    output_span = span_for_call(spans, cases["reasoning_output"])
    check_llm_common(output_span, cases["reasoning_output"])
    meta = output_span["meta"]
    assert meta["span"]["kind"] == "llm", output_span
    assert {"temperature": 0.1}.items() <= meta["metadata"].items(), output_span
    assert meta["input"]["messages"] == [{"role": "user", "content": GENERATE_QUESTION}], output_span
    assert meta["output"]["messages"] == [{"role": "reasoning", "content": REASONING_TEXT},
                                          {"role": "assistant", "content": REASONING_ANSWER}], output_span
    assert output_span["metrics"] == metrics_of("reasoning"), output_span
    input_span = span_for_call(spans, cases["reasoning_input"])
    check_llm_common(input_span, cases["reasoning_input"])
    meta = input_span["meta"]
    assert meta["span"]["kind"] == "llm", input_span
    assert {"temperature": 0.1, "max_output_tokens": 50}.items() <= meta["metadata"].items(), input_span
    assert meta["input"]["messages"] == [
        {"role": "user", "content": GENERATE_QUESTION},
        {"role": "reasoning", "content": "Since 1 + 9 = 10, the value of x is 1."},
        {"role": "assistant", "content": "The value of x is 1."},
        {"role": "user", "content": "What is that number plus 3?"},
    ], input_span
    assert meta["output"]["messages"] == [{"role": "assistant", "content": ANSWER}], input_span
    assert input_span["metrics"] == metrics_of("generate"), input_span


def check_tools_llm(spans, identity):
    cases = {call["case"]: call for call in identity["calls"]
             if call["case"] in ("tools_call", "tool_responses", "executable_code")}
    call_span = span_for_call(spans, cases["tools_call"])
    check_llm_common(call_span, cases["tools_call"])
    meta = call_span["meta"]
    assert meta["span"]["kind"] == "llm", call_span
    assert {"max_output_tokens": 50}.items() <= meta["metadata"].items(), call_span
    assert meta["input"]["messages"] == [{"role": "user", "content": WEATHER_QUESTION}], call_span
    assert meta["output"]["messages"] == [{"role": "assistant", "tool_calls": [{"name": "get_weather",
        "arguments": {"location": "Tokyo"}, "tool_id": GET_WEATHER_CALL_ID, "type": "function_call"}]}], call_span
    assert meta["tool_definitions"][0]["name"] == "get_weather"
    assert meta["tool_definitions"][0]["description"] == "Get the weather in a given location"
    assert call_span["metrics"] == metrics_of("tools_call"), call_span
    responses_span = span_for_call(spans, cases["tool_responses"])
    check_llm_common(responses_span, cases["tool_responses"])
    meta = responses_span["meta"]
    assert meta["span"]["kind"] == "llm", responses_span
    assert {"temperature": 0.1, "max_output_tokens": 50}.items() <= meta["metadata"].items(), responses_span
    assert meta["input"]["messages"] == [
        {"role": "user", "content": WEATHER_QUESTION},
        {"role": "assistant", "tool_calls": [{"name": "get_weather", "arguments": {"location": "Tokyo"},
                                              "tool_id": "abc123", "type": "function_call"}]},
        {"role": "user", "tool_results": [{"name": "get_weather",
                                           "result": json.dumps(TOOL_RESULT),
                                           "tool_id": "abc123", "type": "function_response"}]},
    ], responses_span
    assert meta["output"]["messages"] == [{"role": "assistant", "content": WEATHER_ANSWER}], responses_span
    assert responses_span["metrics"] == metrics_of("weather"), responses_span
    code_span = span_for_call(spans, cases["executable_code"])
    check_llm_common(code_span, cases["executable_code"])
    meta = code_span["meta"]
    assert meta["span"]["kind"] == "llm", code_span
    assert meta["input"]["messages"] == [{"role": "user", "content": CODE_QUESTION}], code_span
    assert [message["role"] for message in meta["output"]["messages"]] == ["assistant"] * 4, code_span
    code_messages = [json.loads(message["content"]) for message in meta["output"]["messages"][1:3]]
    assert code_messages[0] == {"language": "Language.PYTHON", "code": EXECUTABLE_CODE}, code_span
    assert code_messages[1] == {"outcome": "Outcome.OUTCOME_OK", "output": CODE_OUTPUT}, code_span
    assert meta["output"]["messages"][0]["content"] == CODE_SUMMARY, code_span
    assert meta["output"]["messages"][3]["content"] == CODE_CONCLUSION, code_span
    assert code_span["metrics"] == metrics_of("code"), code_span


def check_embed_llm(spans, identity):
    cases = {call["case"]: call for call in identity["calls"] if call["case"].startswith("embed")}
    embed_span = span_for_call(spans, cases["embed"])
    check_llm_common(embed_span, cases["embed"])
    meta = embed_span["meta"]
    assert meta["span"]["kind"] == "embedding", embed_span
    assert meta["input"]["documents"] == [{"text": CHICKEN}], embed_span
    assert meta["output"]["value"] == "[1 embedding(s) returned with size 8]", embed_span
    assert embed_span["metrics"] == {}, embed_span
    dimension_span = span_for_call(spans, cases["embed_dimensions"])
    check_llm_common(dimension_span, cases["embed_dimensions"])
    meta = dimension_span["meta"]
    assert meta["span"]["kind"] == "embedding", dimension_span
    assert meta["input"]["documents"] == [{"text": CHICKEN}], dimension_span
    assert meta["output"]["value"] == "[1 embedding(s) returned with size 10]", dimension_span
    assert {"output_dimensionality": 10}.items() <= meta["metadata"].items(), dimension_span
    assert dimension_span["metrics"] == {}, dimension_span


CHECKS = {
    "apm": [("apm_google_genai_generate_content", check_apm, ("generate", "generate_stream", "generate_error")),
            ("apm_google_genai_embed_content", check_apm, ("embed",))],
    "llmobs": [("llm_observability_google_genai_generate_content", check_generate_llm, ()),
               ("llm_observability_google_genai_generate_content_reasoning", check_reasoning_llm, ()),
               ("llm_observability_google_genai_generate_content_with_tools", check_tools_llm, ()),
               ("llm_observability_google_genai_embed_content", check_embed_llm, ())],
}

MISSING = {
    "apm_google_genai_generate_content": [],
    "apm_google_genai_embed_content": [],
    "llm_observability_google_genai_generate_content": [
        "upstream input variants not exercised: multiple strings, parts list, content block "
        "(test_generate_content_multiple_strings_input / _parts_input / _content_block_input)"],
    "llm_observability_google_genai_generate_content_reasoning": [],
    "llm_observability_google_genai_generate_content_with_tools": [],
    "llm_observability_google_genai_embed_content": [
        "upstream input variants not exercised: multiple strings, parts list, content block "
        "(test_embed_content_multiple_strings_input / _parts_input / _content_block_input)"],
}
