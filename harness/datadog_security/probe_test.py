import copy
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import assert_event, assert_source, marked_root, iast_report, assert_iast_stack, assert_rasp


def packed(value):
    if isinstance(value, dict):
        return bytes([0x80 + len(value)]) + b"".join(packed(key) + packed(child) for key, child in value.items())
    if isinstance(value, list):
        return bytes([0x90 + len(value)]) + b"".join(packed(child) for child in value)
    if isinstance(value, str):
        raw = value.encode()
        return (bytes([0xa0 + len(raw)]) if len(raw) < 32 else bytes([0xd9, len(raw)])) + raw
    assert isinstance(value, int) and 0 <= value < 128
    return bytes([value])


class NativeSecurityAssertionsTest(unittest.TestCase):
    def test_source_requires_genuine_vulnerability_with_linked_source_and_safe_control(self):
        report = {"sources": [{"origin": "http.request.parameter", "value": "tainted-input"}],
                  "vulnerabilities": [{"type": "SQL_INJECTION", "hash": 1,
                    "location": {"path": "security_views.py", "line": 42, "spanId": 42},
                    "evidence": {"valueParts": [{"value": "SELECT '"}, {"value": "tainted-input", "source": 0}]}}]}
        root = {"meta": {"_dd.iast.json": json.dumps(report)}}
        self.assertIn("iast_sink_sql_injection", assert_source(root, {"meta": {}}, "parameter-value"))
        for mutation in [
            lambda data: data["sources"][0].update(origin="http.request.header"),
            lambda data: data["sources"][0].update(value="other-value"),
            lambda data: data["vulnerabilities"][0].update(type="OTHER_VULNERABILITY"),
            lambda data: data["vulnerabilities"][0]["evidence"]["valueParts"][1].update(source=9),
        ]:
            bad = copy.deepcopy(report)
            mutation(bad)
            with self.assertRaises(AssertionError):
                assert_source({"meta": {"_dd.iast.json": json.dumps(bad)}}, {"meta": {}}, "parameter-value")
        with self.assertRaises(AssertionError):
            assert_source(root, root, "parameter-value")

    def test_binary_iast_schema_and_stack_linkage_reject_mutations(self):
        report = {"sources": [], "vulnerabilities": [{"type": "WEAK_HASH", "hash": 1,
            "location": {"path": "security_views.py", "line": 42, "spanId": 7,
                         "stackId": "stack-1", "method": "security"},
            "evidence": {"value": "md5"}}]}
        stack = {"vulnerability": [{"id": "stack-1", "language": "python",
            "frames": [{"id": 1, "file": "security_views.py", "function": "security", "line": 42}]}]}
        root = {"span_id": 7, "meta_struct": {"iast": list(packed(report)), "_dd.stack": list(packed(stack))}}
        self.assertEqual(report, iast_report(root))
        self.assertIn("iast_extended_location", assert_iast_stack(root))
        bad = copy.deepcopy(report)
        bad["vulnerabilities"][0]["hash"] = True
        with self.assertRaises(AssertionError):
            iast_report({"meta": {"_dd.iast.json": json.dumps(bad)}})
        for mutation in [lambda value: value["vulnerability"][0].update(language="ruby"),
                         lambda value: value["vulnerability"][0]["frames"][0].update(line=41),
                         lambda value: value["vulnerability"][0]["frames"].append(value["vulnerability"][0]["frames"][0])]:
            bad = copy.deepcopy(stack)
            mutation(bad)
            broken = copy.deepcopy(root)
            broken["meta_struct"]["_dd.stack"] = list(packed(bad))
            with self.assertRaises(AssertionError):
                assert_iast_stack(broken)

    def test_rasp_requires_genuine_correlated_detector_and_linked_stack(self):
        trigger = {"rule": {"id": "rasp-930-100", "tags": {"module": "rasp"}},
                   "span_id": 7, "stack_id": "exploit-1", "rule_matches": [{"parameters": [{
                       "resource": {"address": "server.io.fs.file", "value": "rasp/base/../safe.txt"},
                       "params": {"address": "server.request.query", "value": "rasp/base/../safe.txt", "key_path": ["input"]}}]}]}
        stack = {"exploit": [{"id": "exploit-1", "language": "python", "frames": [
                    {"id": 1, "file": "security_views.py", "function": "security", "line": 1}]}]}
        root = {"span_id": 7, "metrics": {"_dd.appsec.rasp.duration": 1, "_dd.appsec.rasp.rule.eval": 1},
                "meta": {"_dd.appsec.json": json.dumps({"triggers": [trigger]})},
                "meta_struct": {"_dd.stack": list(packed(stack))}}
        self.assertIn("rasp_local_file_inclusion", assert_rasp(root, {}, "rasp-lfi"))
        for mutation in [lambda value: value.update(span_id=8),
                         lambda value: value["rule"]["tags"].update(module="waf"),
                         lambda value: value["rule_matches"][0]["parameters"][0]["params"].update(value="different-input"),
                         lambda value: value.update(stack_id="other-stack")]:
            bad = copy.deepcopy(trigger)
            mutation(bad)
            broken = copy.deepcopy(root)
            broken["meta"]["_dd.appsec.json"] = json.dumps({"triggers": [bad]})
            with self.assertRaises(AssertionError):
                assert_rasp(broken, {}, "rasp-lfi")
        with self.assertRaises(AssertionError):
            assert_rasp(root, root, "rasp-lfi")

    def test_request_ownership_and_event_tags_cannot_be_substituted(self):
        root = {"parent_id": 0, "meta": {"security.marker": "request-a", "usr.id": "security-user",
                "appsec.events.users.login.success.track": "true", "appsec.events.users.login.success.role": "tester"}}
        self.assertIs(marked_root([root], "request-a"), root)
        self.assertEqual(["user_monitoring"], assert_event(root, "login-success"))
        with self.assertRaises(AssertionError):
            marked_root([root], "request-b")
        with self.assertRaises(AssertionError):
            marked_root([root, root], "request-a")
        bad = copy.deepcopy(root)
        bad["meta"]["usr.id"] = "wrong-user"
        with self.assertRaises(AssertionError):
            assert_event(bad, "login-success")


if __name__ == "__main__":
    unittest.main()
