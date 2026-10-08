"""Regression tests for inherited feature discovery and honest coverage gates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from datadog_capabilities import REVISION, annotate_gap_issues, coverage, coverage_matrix, evidence_results, inventory, main, markdown, sha256, validate_mapping


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
                       "configuration": {}, "_captureVerified": True,
                       "repetitions": 2, "_repeatVerified": True, "_baselineVerified": True}

    def test_scope_and_numeric_id_collision_keep_distinct_named_features(self):
        all_scope = coverage(self.data, self.mapping)
        narrow = coverage(self.data, self.mapping, scope="parametric")
        self.assertEqual(2, all_scope["denominator"])
        self.assertEqual(50, all_scope["implementedPercent"])
        self.assertEqual(1, narrow["denominator"])
        self.assertEqual(0, all_scope["verifiedCapabilities"])
        self.assertFalse(all_scope["fullUpstreamCaseParity"])

    def _write_shared_case(self, root, name):
        files = {kind: root / f"{name}-{kind}.capture.json" for kind in ("primary", "repeat", "baseline")}
        for kind, path in files.items():
            path.write_bytes(f"{name} {kind} capture".encode())
        return dict(self.result, name=name, language="python",
                    captureFile=files["primary"].name, captureSha256=sha256(files["primary"].read_bytes()),
                    repeatCaptureFile=files["repeat"].name, repeatCaptureSha256=sha256(files["repeat"].read_bytes()),
                    baselineSha256=sha256(files["baseline"].read_bytes()),
                    artifacts=[{"file": files[kind].name, "sha256": sha256(files[kind].read_bytes())}
                               for kind in ("repeat", "baseline")])

    def test_shared_gate_rejects_omitted_or_reused_control_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self._write_shared_case(root, "case-a")
            receipt = root / "receipt.json"
            mapping = copy.deepcopy(self.mapping)
            mapping["capabilities"][0]["requiredCases"] = ["case-a"]

            def verified(value):
                receipt.write_text(json.dumps({"results": [value]}))
                return coverage_matrix(self.data, mapping, ["python"],
                                       results=evidence_results([receipt], True))["verifiedCapabilities"]

            self.assertEqual(1, verified(row))
            self.assertEqual(0, verified(dict(row, artifacts=row["artifacts"][:1])))
            self.assertEqual(0, verified(dict(row, baselineSha256="0" * 64)))
            reused = dict(row, baselineSha256=row["captureSha256"],
                          artifacts=row["artifacts"][:1] + [{"file": row["captureFile"], "sha256": row["captureSha256"]}])
            self.assertEqual(0, verified(reused))

    def test_cli_independent_case_gate_accepts_split_receipts_and_rejects_grouping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inventory.json").write_text(json.dumps(self.data))
            (root / "mapping.json").write_text(json.dumps(self.mapping))
            rows = [self._write_shared_case(root, name) for name in ("case-a", "case-b")]
            receipts = []
            for row in rows:
                path = root / (row["name"] + ".json")
                path.write_text(json.dumps({"results": [row]}))
                receipts += ["--evidence", str(path)]
            args = ["report", "--inventory", str(root / "inventory.json"),
                    "--mapping", str(root / "mapping.json"), "--require-language", "python",
                    "--require-independent-cases", "--require-all-implemented",
                    "--output", str(root / "report.json"),
                    "--markdown-output", str(root / "report.md")]
            self.assertEqual(0, main(args + receipts))
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["requiredIndependentCases"])
            self.assertEqual(1, report["verifiedCapabilities"])
            self.assertEqual(markdown(report), (root / "report.md").read_text())
            grouped = root / "grouped.json"
            for values in (rows, []):
                grouped.write_text(json.dumps({"results": values}))
                with self.assertRaisesRegex(ValueError, "exactly one case per receipt"):
                    main(args + ["--evidence", str(grouped)])
            grouped.write_text(json.dumps({"results": rows}))
            # Historical and broader suites may still use grouped receipts.
            self.assertEqual(2, len(list(evidence_results([grouped]))))

    def test_gap_issues_track_missing_features_without_increasing_coverage(self):
        report = coverage_matrix(self.data, self.mapping, ["python", "ruby", "go"])
        gaps = {"upstreamRevision": REVISION, "issues": [{
            "scope": ["shared"], "capabilities": ["second"],
            "url": "https://github.com/owner/repo/issues/12",
        }]}
        annotate_gap_issues(report, gaps)
        self.assertEqual(0, report["verifiedCapabilities"])
        self.assertEqual(2, report["denominator"])
        self.assertEqual([gaps["issues"][0]["url"]], report["capabilities"][1]["gapIssues"])
        for change in ("missing", "unknown", "revision"):
            broken = copy.deepcopy(gaps)
            if change == "missing": broken["issues"] = []
            if change == "unknown": broken["issues"][0]["capabilities"] = ["invented"]
            if change == "revision": broken["upstreamRevision"] = "wrong"
            fresh = coverage_matrix(self.data, self.mapping, ["python", "ruby", "go"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                annotate_gap_issues(fresh, broken)

    def test_cross_language_gate_requires_every_case_in_every_language(self):
        languages = ["python", "ruby", "go"]
        results = [dict(self.result, language=language, name=name)
                   for language in languages for name in ["case-a", "case-b"]]
        report = coverage_matrix(self.data, self.mapping, languages, results=results)
        self.assertEqual(1, report["verifiedCapabilities"])
        self.assertEqual(50, report["verifiedPercent"])
        for mutation in ({"status": "unsupported"}, {"status": "failed"},
                         {"language": None}, {"_captureVerified": False},
                         {"repetitions": 1}, {"_repeatVerified": False}, {"_baselineVerified": False}):
            with self.subTest(mutation=mutation):
                changed = results[:-1] + [dict(results[-1], **mutation)]
                report = coverage_matrix(self.data, self.mapping, languages, results=changed)
                self.assertEqual(0, report["verifiedCapabilities"])
                self.assertEqual(1, report["languageCoverage"]["python"]["verifiedCapabilities"])
                self.assertEqual(1, report["languageCoverage"]["ruby"]["verifiedCapabilities"])
                self.assertEqual(0, report["languageCoverage"]["go"]["verifiedCapabilities"])
        # A duplicate Python pass cannot fill the missing Go cell.
        self.assertEqual(0, coverage_matrix(self.data, self.mapping, languages,
                         results=results[:-1] + [results[0]])["verifiedCapabilities"])
        # Neither empty claims nor a successful duplicate can hide a failure.
        bad = dict(results[-1], status="failed", capabilityNames=[])
        self.assertEqual(0, coverage_matrix(self.data, self.mapping, languages,
                         results=results + [bad])["verifiedCapabilities"])

    def test_cross_language_gate_never_counts_unlabelled_legacy_receipts(self):
        results = [self.result, dict(self.result, name="case-b")]
        self.assertEqual(1, coverage(self.data, self.mapping, results=results)["verifiedCapabilities"])
        report = coverage_matrix(self.data, self.mapping, ["python", "ruby", "go"], results=results)
        self.assertEqual(2, report["denominator"])
        self.assertEqual(0, report["verifiedCapabilities"])
        for languages in ([], ["go", "go"], ["javascript"]):
            with self.subTest(languages=languages), self.assertRaises(ValueError):
                coverage_matrix(self.data, self.mapping, languages, results=results)

    def test_cli_100_percent_matrix_gate_fails_with_missing_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inventory.json").write_text(json.dumps(self.data))
            (root / "mapping.json").write_text(json.dumps(self.mapping))
            capture = b"native capture"
            (root / "capture.json").write_bytes(capture)
            results = [dict(self.result, name=name, language="python", captureFile="capture.json",
                            captureSha256=sha256(capture)) for name in ["case-a", "case-b"]]
            (root / "results.json").write_text(json.dumps({"results": results}))
            status = main(["report", "--inventory", str(root / "inventory.json"),
                           "--mapping", str(root / "mapping.json"), "--evidence", str(root / "results.json"),
                           "--require-language", "python", "--require-language", "ruby",
                           "--require-language", "go", "--require-percent", "100",
                           "--output", str(root / "report.json"),
                           "--markdown-output", str(root / "report.md")])
            self.assertEqual(1, status)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(["python", "ruby", "go"], report["requiredLanguages"])
            self.assertEqual(0, report["verifiedPercent"])
            self.assertEqual(markdown(report), (root / "report.md").read_text())

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

    def test_repeat_proof_requires_a_separate_retained_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture, repeat = b"first native capture", b"second native capture"
            (root / "case-a.capture.json").write_bytes(capture)
            (root / "repeat.capture.json").write_bytes(repeat)
            result = dict(self.result, captureSha256=sha256(capture),
                          repeatCaptureFile="repeat.capture.json", repeatCaptureSha256=sha256(repeat),
                          artifacts=[{"file": "repeat.capture.json", "sha256": sha256(repeat)}])
            receipt = root / "results.json"
            receipt.write_text(json.dumps({"results": [result]}))
            self.assertTrue(list(evidence_results([receipt]))[0]["_repeatVerified"])
            (root / "repeat.capture.json").write_bytes(repeat + b"tampering")
            self.assertFalse(list(evidence_results([receipt]))[0]["_repeatVerified"])
            result.update(repeatCaptureFile="case-a.capture.json", repeatCaptureSha256=sha256(capture),
                          artifacts=[{"file": "case-a.capture.json", "sha256": sha256(capture)}])
            receipt.write_text(json.dumps({"results": [result]}))
            self.assertFalse(list(evidence_results([receipt]))[0]["_repeatVerified"])

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
