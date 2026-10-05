"""Exercise public LLMObs SDK APIs without an external model or client library."""
import argparse
import json
from pathlib import Path

import ddtrace
from ddtrace.llmobs import LLMObs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ml-app")
    parser.add_argument("--identity-file", required=True)
    args = parser.parse_args()
    LLMObs.enable(ml_app=args.ml_app, integrations_enabled=False, agentless_enabled=False, service="llmobs-lab")
    identities = {}

    def record(name, kind="llm", annotations=()):
        kwargs = {"name": name}
        if kind == "llm":
            kwargs.update(model_name="local-test-model", model_provider="test")
        with getattr(LLMObs, kind)(**kwargs) as span:
            identities[name] = LLMObs.export_span(span)
            for annotation in annotations:
                LLMObs.annotate(span=span, **annotation)

    record("enablement", kind="task")
    prompt = {"chat_template": [{"role": "user", "content": "This is a {{query}}"}],
              "version": "1", "variables": {"query": "test query"}}
    record("prompt-chat", annotations=[{"input_data": "This is a test query", "prompt": prompt}])
    record("prompt-task", kind="task", annotations=[{"input_data": "This is a test query", "prompt": prompt}])
    record("prompt-string", annotations=[{"input_data": "This is a test query", "prompt": {
        "template": "This is a {{query}}", "version": "1", "variables": {"query": "test query"}}}])
    record("prompt-update", annotations=[{"input_data": "This is a test query", "prompt": prompt}, {"prompt": {"tags": {"foo": "bar"}}}])
    with LLMObs.annotation_context(prompt=prompt):
        record("prompt-context", annotations=[{"input_data": "This is a test query"}])
    record("cost-tags", annotations=[{"tags": {"team": "ml", "feature": "chatbot", "debug_id": "abc"}, "cost_tags": ["team", "feature"]}])
    record("cost-dedupe", annotations=[{"tags": {"team": "ml", "feature": "chatbot"}, "cost_tags": ["team", "feature"]},
                                        {"tags": {"project": "alpha"}, "cost_tags": ["feature", "project"]}])
    record("cost-invalid", annotations=[{"tags": {"team": "ml"}, "cost_tags": ["team", "missing", 123]}])
    record("cost-empty", annotations=[{"tags": {"team": "ml"}, "cost_tags": []}])
    record("cost-existing", annotations=[{"tags": {"team": "ml"}}, {"cost_tags": ["team"]}])
    with LLMObs.annotation_context(tags={"team": "ml", "feature": "chatbot"}, cost_tags=["team", "feature"]):
        record("cost-context-one")
        record("cost-context-two")
    with LLMObs.annotation_context(cost_tags=["feature"]):
        record("cost-late", annotations=[{"tags": {"feature": "chatbot"}}])
    LLMObs.flush()
    LLMObs.disable()
    ddtrace.tracer.shutdown()
    Path(args.identity_file).write_text(json.dumps({"spans": identities, "tracer_version": ddtrace.__version__}, indent=2) + "\n")


if __name__ == "__main__":
    main()
