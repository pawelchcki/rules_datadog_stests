import copy
import unittest

from harness.datadog_llmobs.assertions import check_cost_tags, check_enablement, check_prompts


def fixture():
    names = ["enablement", "prompt-chat", "prompt-string", "prompt-update", "prompt-context", "prompt-task",
             "cost-tags", "cost-dedupe", "cost-invalid", "cost-empty", "cost-existing", "cost-context-one", "cost-context-two", "cost-late"]
    result = {}
    for name in names:
        result[name] = {"name": name, "tags": ["ml_app:llmobs-lab", "agent_service:llmobs-lab", "service:llmobs-lab", "env:llmobs-env", "version:llmobs-version"],
                        "status": "ok", "duration": 1, "start_ns": 1,
                        "meta": {"span": {"kind": "task" if name in ("enablement", "prompt-task") else "llm"}, "metadata": {}}}
    for name in ("prompt-chat", "prompt-string", "prompt-update", "prompt-context"):
        meta = result[name]["meta"]
        meta.update(model_name="local-test-model", model_provider="test")
        prompt = {"version": "1", "variables": {"query": "test query"}, "id": "llmobs-lab_unnamed-prompt"}
        prompt.update({"template": "This is a {{query}}"} if name == "prompt-string" else {"chat_template": [{"role": "user", "content": "This is a {{query}}"}]})
        if name == "prompt-update":
            prompt["tags"] = {"foo": "bar"}
        meta["input"] = {"prompt": prompt, "messages": [{"content": "This is a test query", "role": ""}]}
    result["prompt-task"]["meta"]["input"] = {"value": "This is a test query"}
    for name, wanted in {"cost-tags": ["team", "feature"], "cost-dedupe": ["team", "feature", "project"],
                         "cost-invalid": ["team"], "cost-existing": ["team"], "cost-context-one": ["team", "feature"],
                         "cost-context-two": ["team", "feature"]}.items():
        result[name]["meta"]["metadata"] = {"_dd": {"cost_tags": wanted}}
        result[name]["tags"] += ["team:ml", "feature:chatbot", "project:alpha"]
    return result


class LLMObsAssertionsTest(unittest.TestCase):
    def test_valid_sdk_events(self):
        spans = fixture()
        for check in (check_enablement, check_prompts, check_cost_tags):
            check(spans, None)
            check(spans, "")

    def test_enablement_checks_tags_and_valid_span_timing(self):
        for mutation in (lambda span: span["tags"].append("ml_app:other"), lambda span: span.update(duration=0),
                         lambda span: span.update(status="error")):
            spans = fixture()
            mutation(spans["enablement"])
            with self.assertRaises(AssertionError):
                check_enablement(spans, None)
        with self.assertRaises(AssertionError):
            check_enablement(fixture(), "override")

    def test_prompts_require_actual_template_variables_and_context(self):
        for name in ("prompt-chat", "prompt-context", "prompt-string", "prompt-update"):
            for field in ("version", "variables", "id"):
                spans = fixture()
                spans[name]["meta"]["input"]["prompt"][field] = "wrong"
                with self.subTest(name=name, field=field), self.assertRaises(AssertionError):
                    check_prompts(spans, None)
        spans = fixture()
        spans["prompt-task"]["meta"]["input"]["prompt"] = {"template": "leaked"}
        with self.assertRaises(AssertionError):
            check_prompts(spans, None)

    def test_cost_tag_controls_reject_missing_duplicates_invalid_and_late_entries(self):
        for name, wrong in [("cost-tags", ["team"]), ("cost-dedupe", ["team", "feature", "feature", "project"]),
                            ("cost-invalid", ["team", "missing", 123]), ("cost-empty", []), ("cost-existing", None),
                            ("cost-context-two", ["team"]), ("cost-late", ["feature"])]:
            spans = fixture()
            spans[name]["meta"]["metadata"] = {"_dd": {"cost_tags": wrong}}
            with self.subTest(name=name), self.assertRaises(AssertionError):
                check_cost_tags(spans, None)

    def test_cost_tags_must_reference_observed_values(self):
        spans = fixture()
        spans["cost-tags"]["tags"] = [tag for tag in spans["cost-tags"]["tags"] if not tag.startswith("team:")]
        spans["cost-tags"]["tags"].append("team:wrong")
        with self.assertRaises(AssertionError):
            check_cost_tags(spans, None)


if __name__ == "__main__":
    unittest.main()
