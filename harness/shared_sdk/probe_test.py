"""Guard manifest exclusions and the evidence boundary, independent of SDK behavior."""
import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("shared_sdk_probe", Path(__file__).with_name("probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
from sdk_expected_failures import BAGGAGE_CASE, BAGGAGE_SOURCE_SHA256, BAGGAGE_REASON, local_failure_reason
from baggage_cases import UPSTREAM_CASE, UPSTREAM_SELECTOR, adapt_case


def raised(source, filename):
    try:
        exec(compile(source, filename, "exec"))
    except Exception as error:
        return error
    raise AssertionError("Expected an exception")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.case = dict(file="test_real.py", **{"class": "TestReal"}, method="test_feature", name="test_real.TestReal.test_feature[0]")
        self.library = SimpleNamespace(lang="go", sdk_version="2.10.1", operations=[])
        self.agent = SimpleNamespace(captures=[])
        self.reason = dict(selector="tests/parametric/test_real.py", reason="missing_feature")

    def match(self, error, reason=None):
        return probe.expected_failure(self.case, error, self.library, self.agent, reason, "unused", "v0.4")

    def test_original_failure_requires_a_manifest_declaration(self):
        error = raised("assert False", "test_real.py")
        self.assertFalse(self.match(error))
        self.assertTrue(self.match(error, self.reason))

    def test_manifest_cannot_mask_adapter_or_rpc_failures(self):
        self.assertFalse(self.match(raised("assert False, 'health timeout'", "probe.py"), self.reason))
        error = raised("from urllib.error import HTTPError\nraise HTTPError('http://local/sdk', 500, 'broken', {}, None)", "test_real.py")
        self.assertFalse(self.match(error, self.reason))
        self.assertFalse(self.match(raised("raise AttributeError('absent API')", "test_real.py"), self.reason))

    def test_specific_rule_and_inherited_skip(self):
        child = dict(selector=self.reason['selector'] + "::TestReal", reason="bug (EXAMPLE-1)")
        manifest = {"declarations": [self.reason, child]}
        self.assertEqual(probe.manifest_reason(self.case, manifest), child)
        child["reason"] = None
        self.assertEqual(probe.manifest_reason(self.case, manifest), self.reason)
        self.assertIsNone(probe.manifest_reason(dict(self.case, file="other.py"), manifest))

    def test_local_supplement_uses_an_explicit_upstream_selector(self):
        self.case['upstreamSelector'] = 'tests/test_tags.py::TestReferrer::test_referrer'
        reason = dict(selector='tests/test_tags.py::TestReferrer', reason='missing_feature')
        self.assertEqual(probe.manifest_reason(self.case, {"declarations": [reason]}), reason)

    def test_corrected_byte_limit_assertion_does_not_inherit_the_old_skip(self):
        case = adapt_case(dict(name=UPSTREAM_CASE))
        reason = dict(selector=UPSTREAM_SELECTOR, reason='missing_feature')
        self.assertIsNone(probe.manifest_reason(case, {'declarations': [reason]}))
        error = raised("assert False, 'overflow'", "baggage_cases.py")
        self.assertFalse(probe.expected_failure(case, error, self.library, self.agent, None, "unused", "v0.4"))


class BaggageDefectTests(unittest.TestCase):
    def setUp(self):
        baggage = {"user.id": "doggo", "session.id": "controlled-session", "other": "private"}
        self.failure = dict(type="KeyError", file="portable_cases.py", function="baggage_tags",
                            line=61, message="'baggage.user.id'")
        self.operations = [dict(operation="http_request", arguments=dict(status=200, query={}, headers={
            "baggage": "user.id=doggo,session.id=controlled-session,other=private"}), result=dict(
                control=dict(span_id=1, trace_id=42), target=dict(span_id=3, trace_id=42),
                extracted_baggage=baggage, target_baggage=baggage))]
        self.spans = [dict(span_id=1, trace_id=42, meta={"baggage.user.id": "doggo", "baggage.session.id": "controlled-session"}),
                      dict(span_id=2, trace_id=42, parent_id=1, meta={"component": "aiohttp_client", "http.status_code": "200"}),
                      dict(span_id=3, trace_id=42, parent_id=2, meta={"component": "aiohttp", "http.status_code": "200"})]

    def match(self, version="4.15.5", source_hash=BAGGAGE_SOURCE_SHA256):
        captures = [[dict(payload=dict(wire_version="v0.4", traces=[self.spans]))]]
        return local_failure_reason(BAGGAGE_CASE, version, "v0.4", self.failure, captures, self.operations, source_hash)

    def test_exact_defect_is_version_and_source_bound(self):
        self.assertEqual(self.match(), BAGGAGE_REASON)
        self.assertIsNone(self.match(version="4.15.6"))
        self.assertIsNone(self.match(source_hash="changed"))
        self.failure["message"] = "'another.tag'"
        self.assertIsNone(self.match())

    def test_missing_baggage_transport_or_parentage_is_not_an_xfail(self):
        original = deepcopy(self.operations)
        self.operations[0]["result"]["target_baggage"] = {}
        self.assertIsNone(self.match())
        self.operations = original
        self.spans[2]["parent_id"] = 999
        self.assertIsNone(self.match())

    def test_working_tags_or_missing_native_sdk_control_is_not_an_xfail(self):
        self.spans[2]["meta"]["baggage.user.id"] = "doggo"
        self.assertIsNone(self.match())
        del self.spans[2]["meta"]["baggage.user.id"]
        del self.spans[0]["meta"]["baggage.user.id"]
        self.assertIsNone(self.match())


if __name__ == '__main__':
    unittest.main()
