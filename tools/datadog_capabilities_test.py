"""Regression tests for inherited feature discovery and honest coverage gates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from datadog_capabilities import REVISION, coverage, evidence_results, inventory, sha256, validate_mapping


class InventoryTest(unittest.TestCase):
    def test_inherited_class_mark_and_inherited_method_are_collected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "utils").mkdir()
            (root / "tests/parametric").mkdir(parents=True)
            (root / "utils/_features.py").write_text('''class _Features:
    def first(obj):
        """First feature"""
        return _mark_test_object(obj, feature_id=1, owner=_Owner.sdk)
    def second(obj):
        return _mark_test_object(obj, feature_id=1, owner=_Owner.sdk)
    def hidden(obj):
        return _mark_test_object(obj, feature_id=NOT_REPORTED_ID, owner=_Owner.sdk)
    def not_reported(obj):
        return _mark_test_object(obj, feature_id=NOT_REPORTED_ID, owner=_Owner.sdk)
''')
            (root / "tests/base.py").write_text('''@features.first
class Base:
    def test_inherited(self): pass
''')
            (root / "tests/parametric/test_child.py").write_text('''from tests.base import Base as Parent
@features.second
class Test_Child(Parent):
    @features.hidden
    def test_own(self): pass
@features.not_reported
def test_control(): pass
''')
            result = inventory(root)
            rows = {row["name"]: row for row in result["features"]}
            self.assertEqual({"first", "second", "hidden"}, set(rows))
            inherited, own = result["testCases"]
            self.assertEqual(["first", "second"], inherited["capabilityNames"])
            self.assertEqual("tests/base.py", inherited["sourceFile"])
            self.assertEqual(["first", "hidden", "second"], own["capabilityNames"])
            self.assertEqual(2, len(rows["first"]["testCases"]))
            self.assertIn("parametric", rows["first"]["scopes"])
            self.assertEqual(-1, rows["hidden"]["id"])
            self.assertEqual(4, result["rawDecoratorCount"])

    def test_unknown_feature_fails_instead_of_silently_dropping_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "utils").mkdir()
            (root / "tests").mkdir()
            (root / "utils/_features.py").write_text("class _Features: pass")
            (root / "tests/test_new.py").write_text("@features.new\ndef test_new(): pass")
            with self.assertRaisesRegex(ValueError, "Unknown upstream feature"):
                inventory(root)


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.data = {"revision": REVISION, "features": [
            {"name": "first", "id": 1, "scopes": ["all", "parametric"], "testCases": ["test_a", "test_b"]},
            {"name": "second", "id": 1, "scopes": ["all"], "testCases": ["test_c"]},
        ]}
        self.mapping = {"upstreamRevision": REVISION, "capabilities": [{
            "name": "first", "status": "implemented", "requiredCases": ["case-a", "case-b"],
            "checks": [{"path": "probe.py", "symbol": "assert_feature", "target": "//fixtures:lab"}],
        }]}
        self.result = {"name": "case-a", "status": "passed", "capabilityNames": ["first"],
                       "capabilityInventoryRevision": REVISION, "captureSha256": "a" * 64,
                       "configuration": {}, "_captureVerified": True}

    def test_scope_and_numeric_id_collision_keep_distinct_named_features(self):
        all_scope = coverage(self.data, self.mapping)
        narrow = coverage(self.data, self.mapping, scope="parametric")
        self.assertEqual(2, all_scope["denominator"])
        self.assertEqual(50, all_scope["implementedPercent"])
        self.assertEqual(1, narrow["denominator"])
        self.assertEqual(0, all_scope["verifiedCapabilities"])
        self.assertFalse(all_scope["fullUpstreamCaseParity"])

    def test_every_required_case_must_pass_with_verified_capture(self):
        second = dict(self.result, name="case-b")
        self.assertEqual(1, coverage(self.data, self.mapping, results=[self.result, second])["verifiedCapabilities"])
        self.assertEqual(0, coverage(self.data, self.mapping, results=[self.result])["verifiedCapabilities"])
        for mutation in ({"status": "unsupported"}, {"status": "failed"},
                         {"captureSha256": None}, {"configuration": None}, {"_captureVerified": False}):
            with self.subTest(mutation=mutation):
                bad = dict(second, **mutation)
                self.assertEqual(0, coverage(self.data, self.mapping, results=[self.result, bad])["verifiedCapabilities"])
        # A passing duplicate cannot erase a failure from another profile/wire.
        bad = dict(second, status="failed")
        self.assertEqual(0, coverage(self.data, self.mapping, results=[self.result, second, bad])["verifiedCapabilities"])
        outside_claim = dict(second, name="optional-unmapped-case", status="unsupported")
        self.assertEqual(1, coverage(self.data, self.mapping, results=[
            self.result, second, outside_claim])["verifiedCapabilities"])

    def test_wire_scope_is_explicit_and_does_not_erase_same_wire_failure(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["capabilities"][0]["requiredCases"] = [{"name": "case-a", "wire": "v0.5"}]
        passed = dict(self.result, wire="v0.5")
        excluded = dict(self.result, wire="v0.4", status="unsupported")
        self.assertEqual(1, coverage(self.data, mapping, results=[passed, excluded])["verifiedCapabilities"])
        failed = dict(passed, status="failed")
        self.assertEqual(0, coverage(self.data, mapping, results=[passed, excluded, failed])["verifiedCapabilities"])
        self.assertEqual(0, coverage(self.data, mapping, results=[excluded])["verifiedCapabilities"])

    def test_empty_claim_duplicate_cannot_hide_failure_or_invalid_capture(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["capabilities"][0]["requiredCases"] = [{"name": "case-a", "wire": "v0.5"}]
        passed = dict(self.result, wire="v0.5")
        for mutation in ({"status": "failed"}, {"_captureVerified": False}, {}):
            with self.subTest(mutation=mutation):
                empty = dict(passed, capabilityNames=[], **mutation)
                result = coverage(self.data, mapping, results=[passed, empty])
                self.assertEqual(0, result["verifiedCapabilities"])
                self.assertEqual(mapping["capabilities"][0]["requiredCases"],
                                 result["capabilities"][0]["failedEvidenceCases"])
                # An explicitly excluded wire remains outside the claim.
                self.assertEqual(1, coverage(self.data, mapping, results=[
                    passed, dict(empty, wire="v0.4")])["verifiedCapabilities"])
        plain = copy.deepcopy(mapping)
        plain["capabilities"][0]["requiredCases"] = ["case-a"]
        self.assertEqual(0, coverage(self.data, plain, results=[
            passed, dict(passed, wire="v0.4", capabilityNames=[], status="failed")])["verifiedCapabilities"])

    def test_partial_unsupported_and_missing_mappings_do_not_count(self):
        second = dict(self.result, name="case-b")
        for status in ("partial", "unsupported", "missing"):
            with self.subTest(status=status):
                mapping = copy.deepcopy(self.mapping)
                mapping["capabilities"][0]["status"] = status
                result = coverage(self.data, mapping, results=[self.result, second])
                self.assertEqual(0, result["verifiedCapabilities"])
                self.assertEqual(0, result["implementedCapabilities"])

    def test_stale_revision_and_unknown_claims_fail(self):
        with self.assertRaisesRegex(ValueError, "revision"):
            coverage(self.data, self.mapping, results=[dict(self.result, capabilityInventoryRevision="stale")])
        with self.assertRaisesRegex(ValueError, "unknown"):
            coverage(self.data, self.mapping, results=[dict(self.result, capabilityNames=["invented"])])
        self.assertEqual(0, coverage(self.data, self.mapping, results=[
            dict(self.result, capabilityNames=["second"])])["verifiedCapabilities"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            coverage(self.data, self.mapping, results=[dict(self.result, capabilityNames=["first", "first"])])

    def test_local_assertion_mapping_cannot_point_outside_repo_or_to_absent_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "probe.py").write_text("def assert_feature(): pass")
            validate_mapping(self.data, self.mapping, root)
            bad = copy.deepcopy(self.mapping)
            bad["capabilities"][0]["checks"][0]["symbol"] = "missing_assertion"
            with self.assertRaisesRegex(ValueError, "Missing local assertion"):
                validate_mapping(self.data, bad, root)
            bad["capabilities"][0]["checks"][0]["path"] = "../probe.py"
            with self.assertRaisesRegex(ValueError, "within local"):
                validate_mapping(self.data, bad, root)

    def test_every_additional_artifact_must_exist_and_match_its_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = b"native trace capture"
            backend = b"agent backend payload"
            (root / "case-a.capture.json").write_bytes(capture)
            (root / "backend.json").write_bytes(backend)
            result = dict(self.result, captureSha256=sha256(capture), artifacts=[
                {"file": "backend.json", "sha256": sha256(backend)}])
            receipt = root / "results.json"
            receipt.write_text(json.dumps({"results": [result]}))
            self.assertTrue(list(evidence_results([receipt]))[0]["_captureVerified"])
            (root / "backend.json").write_bytes(backend + b"tampering")
            self.assertFalse(list(evidence_results([receipt]))[0]["_captureVerified"])
            (root / "backend.json").unlink()
            self.assertFalse(list(evidence_results([receipt]))[0]["_captureVerified"])
            result["artifacts"][0]["file"] = "../outside.json"
            receipt.write_text(json.dumps({"results": [result]}))
            with self.assertRaisesRegex(ValueError, "evidence directory"):
                list(evidence_results([receipt]))

    def test_retained_capture_hash_is_checked_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = b'[{"payload": {"traces": []}}]'
            (root / "case-a.capture.json").write_bytes(capture)
            receipt = root / "results.json"
            result = dict(self.result, captureSha256=sha256(capture), _captureVerified=False)
            receipt.write_text(json.dumps({"results": [result]}))
            self.assertTrue(list(evidence_results([receipt]))[0]["_captureVerified"])
            (root / "case-a.capture.json").write_bytes(capture + b" ")
            self.assertFalse(list(evidence_results([receipt]))[0]["_captureVerified"])
            result["captureFile"] = "../outside.json"
            receipt.write_text(json.dumps({"results": [result]}))
            with self.assertRaisesRegex(ValueError, "evidence directory"):
                list(evidence_results([receipt]))


if __name__ == "__main__":
    unittest.main()
