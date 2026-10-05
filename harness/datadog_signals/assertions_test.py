"""Mutation checks: signal receipts cannot pass with unrelated identities/tags."""
import copy
import unittest

from harness.datadog_signals.probe import IDENTITY, check_logs, check_runtime


class SignalAssertionsTest(unittest.TestCase):
    def fixture(self):
        trace = int("1234567890abcdef1234567890abcdef", 16)
        fields = dict(IDENTITY, **{"dd.trace_id": "0", "dd.span_id": "0"})
        result = {"identity": {"trace_id": str(trace), "span_id": "13", "parent_id": "7"},
                  "logs": [{"message": message, "fields": dict(fields)} for message in ["outside-before", "inside", "outside-after"]]}
        result["logs"][1]["fields"].update({"dd.trace_id": f"{trace:032x}", "dd.span_id": "13"})
        span = {"trace_id": trace & ((1 << 64) - 1), "span_id": 13, "parent_id": 7,
                "meta": {"_dd.p.tid": f"{trace >> 64:016x}"}}
        return result, [span]

    def test_log_identity_and_outside_context_mutations_fail(self):
        result, spans = self.fixture()
        check_logs(result, spans, True, True)
        for index, key, value in [(1, "dd.trace_id", "unrelated"), (1, "dd.span_id", "14"),
                                  (0, "dd.trace_id", "42"), (2, "dd.env", "wrong-env")]:
            mutated = copy.deepcopy(result)
            mutated["logs"][index]["fields"][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(AssertionError):
                check_logs(mutated, spans, True, True)

    def test_native_trace_high_bits_and_parent_mutations_fail(self):
        result, spans = self.fixture()
        for key, value in [("span_id", 42), ("parent_id", 42), ("trace_id", 42)]:
            mutated = copy.deepcopy(spans)
            mutated[0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_logs(result, mutated, True, True)
        spans[0]["meta"]["_dd.p.tid"] = "0000000000000000"
        with self.assertRaises(AssertionError):
            check_logs(result, spans, True, True)

    def test_udp_requires_runtime_metrics_and_identity_tags(self):
        tags = "service:signals-service,env:signals-env,version:signals-version"
        lines = "\n".join(f"runtime.python.gc.count.gen{i}:12|d|#{tags}" for i in range(3))
        check_runtime([lines], True)
        for messages in [[], [lines.replace("env:signals-env", "env:wrong")], [lines.replace("gen2", "unknown")], [lines.replace(":12|", ":-1|")]]:
            with self.subTest(messages=messages), self.assertRaises(AssertionError):
                check_runtime(messages, True)
        with self.assertRaises(AssertionError):
            check_runtime([lines], False)


if __name__ == "__main__":
    unittest.main()
