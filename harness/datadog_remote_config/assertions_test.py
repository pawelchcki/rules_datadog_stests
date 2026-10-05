"""Mutation tests against real signed-RC captures."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from harness.datadog_remote_config import assertions
from harness.datadog_remote_config.backend import FIXTURES, encode


class RCAssertionsTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path(__file__).with_name("golden.json").read_text())
        version = patch.object(assertions, "SDK_VERSION", "4.14.0")
        version.start()
        self.addCleanup(version.stop)

    def test_real_capture(self):
        d = self.data
        assertions.check_version(d["requests"], d["ready"])
        assertions.check_protocol(d["requests"], d["ready"], d["backend"])
        assertions.check_dynamic(d["spans"], d["identities"])
        assertions.check_sampling(d["spans"], d["identities"], d["agent_spans"])

    def test_missing_header_tag(self):
        for span in self.data["spans"]:
            span["meta"].pop("rc.header", None)
        with self.assertRaises(AssertionError):
            assertions.check_dynamic(self.data["spans"], self.data["identities"])

    def test_static_log_configuration(self):
        self.data["identities"][0]["log"] = self.data["identities"][1]["log"]
        with self.assertRaises(AssertionError):
            assertions.check_dynamic(self.data["spans"], self.data["identities"])

    def test_static_sampling(self):
        for span in self.data["spans"]:
            if span["resource"] == "stage-2": span["metrics"]["_sampling_priority_v1"] = 1
        with self.assertRaises(AssertionError):
            assertions.check_sampling(self.data["spans"], self.data["identities"], self.data["agent_spans"])

    def test_failed_rc_ack(self):
        for request in self.data["requests"]:
            for state in request["client"]["state"]["config_states"]:
                state["apply_state"] = 3
        with self.assertRaises(AssertionError):
            assertions.check_protocol(self.data["requests"], self.data["ready"], self.data["backend"])

    def test_missing_backend_client_state(self):
        for record in self.data["backend"]:
            record["payload"]["request"]["active_clients"] = []
        with self.assertRaises(AssertionError):
            assertions.check_protocol(self.data["requests"], self.data["ready"], self.data["backend"])

    def test_invalid_version(self):
        self.data["requests"][-1]["client"]["client_tracer"]["tracer_version"] = "4.14"
        with self.assertRaises(AssertionError):
            assertions.check_version(self.data["requests"], self.data["ready"])

    def test_signed_fixture_hash_chain(self):
        for stage in FIXTURES["stages"]:
            for document, dependency, name in (("timestamp", "snapshot", "snapshot.json"), ("snapshot", "targets", "targets.json")):
                reference = stage[document]["signed"]["meta"][name]
                raw = encode(stage[dependency])
                self.assertEqual(reference["hashes"]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(reference["length"], len(raw))
            for path, content in stage["files"].items():
                self.assertEqual(stage["targets"]["signed"]["targets"][path]["hashes"]["sha256"], hashlib.sha256(encode(content)).hexdigest())


if __name__ == "__main__":
    unittest.main()
