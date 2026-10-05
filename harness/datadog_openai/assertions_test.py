"""Mutations of real pinned-client captures must invalidate capability evidence."""
import copy
import json
from pathlib import Path
import unittest
from harness.datadog_openai import assertions


class AssertionsTest(unittest.TestCase):
    def setUp(self):
        self.golden = json.loads(Path(__file__).with_name("golden.json").read_text())

    def test_real_capture(self):
        for mode, checks in assertions.CHECKS.items():
            for _, check in checks:
                check(self.golden[mode]["spans"], self.golden[mode]["identity"])

    def test_reject_apm_parent_mismatch(self):
        evidence = self.golden["apm"]
        for span in evidence["spans"]:
            if span["name"] == "openai.request":
                span["parent_id"] = 0
        with self.assertRaises(AssertionError):
            assertions.check_completions(evidence["spans"], evidence["identity"])

    def test_reject_lost_error_status(self):
        evidence = self.golden["apm"]
        for span in evidence["spans"]:
            span["error"] = 0
        with self.assertRaises(AssertionError):
            assertions.check_embeddings(evidence["spans"], evidence["identity"])

    def test_reject_llm_content_metrics_provider_and_identity(self):
        for mutation in ("output", "metric", "provider", "identity"):
            evidence = copy.deepcopy(self.golden["llmobs"])
            span = evidence["spans"][0]
            if mutation == "output": span["meta"]["output"]["messages"][0]["content"] = "invented"
            if mutation == "metric": span["metrics"]["total_tokens"] += 1
            if mutation == "provider": span["meta"]["model_provider"] = "unknown"
            if mutation == "identity": span["_dd"]["apm_trace_id"] = "0"
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                assertions.check_llm_interactions(evidence["spans"], evidence["identity"])

    def test_reject_embedding_shape(self):
        evidence = self.golden["llmobs"]
        for span in evidence["spans"]:
            if span["meta"]["span"]["kind"] == "embedding" and span["status"] == "ok":
                span["meta"]["output"]["value"] = "[1 embedding(s) returned with size 1536]"
        with self.assertRaises(AssertionError):
            assertions.check_llm_embeddings(evidence["spans"], evidence["identity"])


if __name__ == "__main__":
    unittest.main()
