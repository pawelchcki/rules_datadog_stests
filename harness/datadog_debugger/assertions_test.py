"""Mutate real debugger captures to prove capability checks reject regressions."""
import copy
import json
from pathlib import Path
import unittest
from harness.datadog_debugger import assertions
from harness.datadog_debugger.backend import decode_document


class DebuggerAssertionsTest(unittest.TestCase):
    def setUp(self):
        data = json.loads(Path(__file__).with_name("golden.json").read_text())
        self.events, self.identities = data["events"], data["identities"]

    def snapshot(self, name):
        return next(e["debugger"]["snapshot"] for e in self.events if e.get("debugger", {}).get("snapshot", {}).get("probe", {}).get("id") == name)

    def test_real_captures(self):
        for _, check, _ in assertions.CHECKS:
            check(self.events, self.identities)

    def test_reject_wrong_return(self):
        self.snapshot("method-probe")["captures"]["return"]["locals"]["@return"]["value"] = "wrong"
        with self.assertRaises(AssertionError): assertions.check_method(self.events, self.identities)

    def test_reject_wrong_line(self):
        self.snapshot("line-probe")["probe"]["location"]["lines"] = ["999"]
        with self.assertRaises(AssertionError): assertions.check_line(self.events, self.identities)

    def test_reject_false_condition_message(self):
        for event in self.events:
            if event.get("debugger", {}).get("snapshot", {}).get("probe", {}).get("id") == "expression-probe": event["message"] = "value=1"
        with self.assertRaises(AssertionError): assertions.check_expressions(self.events, self.identities)

    def test_reject_password_capture(self):
        self.snapshot("method-probe")["captures"]["return"]["arguments"]["password"] = {"type": "str", "value": assertions.SECRET}
        with self.assertRaises(AssertionError): assertions.check_redaction(self.events, self.identities)

    def test_reject_budget_overflow(self):
        event = next(e for e in self.events if e.get("debugger", {}).get("snapshot", {}).get("probe", {}).get("id") == "budget-probe")
        self.events.extend(copy.deepcopy(event) for _ in range(21))
        with self.assertRaises(AssertionError): assertions.check_budgets(self.events, self.identities)

    def test_reject_emission_after_removal(self):
        for event in self.events:
            if "snapshot" in event.get("debugger", {}):
                event["dd"]["span_id"] = self.identities[2]["span_id"]
        with self.assertRaises(AssertionError): assertions.check_method(self.events, self.identities)

    def test_reject_non_json_part(self):
        with self.assertRaises(AssertionError): decode_document(b'[]', 'text/plain')
        with self.assertRaises(AssertionError): decode_document(b'{}', 'application/json')


if __name__ == "__main__":
    unittest.main()
