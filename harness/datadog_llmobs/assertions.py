"""Source-derived LLMObs assertions over events emitted by the genuine SDK."""


def tags(span):
    result = {}
    for tag in span["tags"]:
        name, separator, value = tag.partition(":")
        assert separator and name and name not in result, span["tags"]
        result[name] = value
    return result


def check_enablement(spans, ml_app):
    expected = ml_app or "llmobs-lab"
    assert spans and "enablement" in spans, spans
    for span in spans.values():
        observed = tags(span)
        assert observed["ml_app"] == observed["agent_service"] == expected, observed
        assert observed["service"] == "llmobs-lab" and observed["env"] == "llmobs-env", observed
        assert observed["version"] == "llmobs-version", observed
        assert span["status"] == "ok" and span["duration"] > 0 and span["start_ns"] > 0, span
    assert spans["enablement"]["meta"]["span"]["kind"] == "task", spans["enablement"]


def check_prompts(spans, ml_app):
    for name in ("prompt-chat", "prompt-string", "prompt-update", "prompt-context"):
        span = spans[name]
        assert span["meta"]["span"]["kind"] == "llm", span
        assert span["meta"]["model_name"] == "local-test-model" and span["meta"]["model_provider"] == "test", span
        prompt = span["meta"]["input"]["prompt"]
        if name == "prompt-string":
            assert prompt["template"] == "This is a {{query}}", prompt
        else:
            assert prompt["chat_template"] == [{"role": "user", "content": "This is a {{query}}"}], prompt
        assert prompt["version"] == "1" and prompt["variables"] == {"query": "test query"}, prompt
        assert prompt["id"] == (ml_app or "llmobs-lab") + "_unnamed-prompt", prompt
        assert span["meta"]["input"]["messages"] == [{"content": "This is a test query", "role": ""}], span
    assert spans["prompt-update"]["meta"]["input"]["prompt"]["tags"] == {"foo": "bar"}, spans["prompt-update"]
    task = spans["prompt-task"]
    assert task["meta"]["span"]["kind"] == "task", task
    assert "prompt" not in task["meta"]["input"] and task["meta"]["input"]["value"] == "This is a test query", task


def check_cost_tags(spans, _ml_app):
    expected = {"cost-tags": ["team", "feature"], "cost-dedupe": ["team", "feature", "project"],
                "cost-invalid": ["team"], "cost-empty": None, "cost-existing": ["team"],
                "cost-context-one": ["team", "feature"], "cost-context-two": ["team", "feature"], "cost-late": None}
    values = {"team": "ml", "feature": "chatbot", "project": "alpha"}
    for name, wanted in expected.items():
        span = spans[name]
        actual = span["meta"].get("metadata", {}).get("_dd", {}).get("cost_tags")
        assert actual == wanted, (name, actual, wanted)
        if wanted is not None:
            observed_tags = tags(span)
            for key in wanted:
                assert observed_tags[key] == values[key], (name, observed_tags)
