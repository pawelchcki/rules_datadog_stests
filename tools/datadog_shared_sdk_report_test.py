import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import datadog_shared_sdk_report as report
from update_shared_sdk_cases import applies, declaration_reason, resolve_manifest, mapping_for_registry

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_both_formats_retained_when_complete_matrix_gate_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Rendering shares the validated report; requiring all cases must
            # still reject an empty set and leave both diagnostic formats.
            data = dict(ciProfile='full', selectedCaseCount=0)
            with patch.object(report, 'load', return_value=[]), \
                    patch.object(report, 'build_report', return_value=data), \
                    patch.object(report, 'sdk_cases', return_value=[dict(name='required-case')]), \
                    patch.object(report, 'markdown', return_value='missing evidence\n'):
                with self.assertRaisesRegex(SystemExit, 'missing or unknown registered cases'):
                    report.main(['--output', str(root/'report.json'),
                                 '--markdown-output', str(root/'report.md'),
                                 '--require-complete-matrix'])
            self.assertEqual(json.loads((root/'report.json').read_text()),
                             dict(ciProfile='full', selectedCaseCount=1))
            self.assertEqual('missing evidence\n', (root/'report.md').read_text())

    def test_version_conditions_at_the_go_pin(self):
        self.assertTrue(applies('>=1.52.0 <2.7.0-dev', '2.6.0'))
        self.assertFalse(applies('>=1.52.0 <2.7.0-dev', '2.10.1'))
        self.assertTrue(applies('>=2.10.1-dev', '2.10.1'))
        self.assertFalse(applies('<2.10.1-dev', '2.10.1'))
        self.assertTrue(applies('v4.13.0rc1', '4.15.5'))
        with self.assertRaises(ValueError):
            applies('unsupported expression', '2.10.1')

    def test_operator_prefixed_availability_cannot_mask_supported_failures(self):
        for declaration in ('>=2.5.0', '>2.5.0', '<2.11.0', '<=2.10.1', '=2.10.1',
                            '==2.10.1', '>=2.5.0 <2.11.0', 'v2.5.0 (available)'):
            with self.subTest(declaration=declaration):
                self.assertIsNone(declaration_reason(declaration, '2.10.1'))
        for declaration in ('>=2.11.0', '<2.10.1', '>=2.5.0 <2.7.0-dev'):
            with self.subTest(declaration=declaration):
                self.assertTrue(declaration_reason(declaration, '2.10.1').startswith('missing_feature'))
        self.assertEqual(declaration_reason('bug (SDK-1)', '2.10.1'), 'bug (SDK-1)')

    def test_bare_component_versions_do_not_exclude_later_sdks(self):
        for release in ('4.13.1', '4.14.1', '2.16.0', '2.16.1'):
            with self.subTest(release=release):
                self.assertTrue(applies(release, release))
                self.assertFalse(applies(release, '4.15.5'))
        self.assertTrue(applies('v4.13.1', '4.15.5'))
        declarations, unresolved = resolve_manifest({'manifest': {
            'tests/example.py': [
                {'component_version': '4.13.1', 'declaration': 'flaky (SDK-1)'},
                {'component_version': '4.15.5', 'declaration': 'bug (SDK-2)'},
                {'component_version': '>=4.15.5', 'declaration': 'bug (SDK-3)'}],
            'tests/baggage.py': '>=2.5.0'}}, '4.15.5')
        self.assertEqual(declarations, [
            {'selector': 'tests/example.py', 'reason': 'bug (SDK-2)'},
            {'selector': 'tests/example.py', 'reason': 'bug (SDK-3)'},
            {'selector': 'tests/baggage.py', 'reason': None}])
        self.assertFalse(unresolved)

    def test_framework_conditions_do_not_become_global_exclusions(self):
        declarations, unresolved = resolve_manifest({'manifest': {
            'tests/example.py': [{'declaration': 'missing_feature', 'weblog': ['fiber']},
                                 {'declaration': 'bug (X-1)', 'component_version': '<2.5.0'},
                                 {'declaration': 'irrelevant', 'component_version': '>=2.7.0'}],
            'tests/supported.py': 'v2.7.0'}}, '2.10.1')
        self.assertEqual(declarations, [{'selector': 'tests/example.py', 'reason': 'irrelevant'},
                                        {'selector': 'tests/supported.py', 'reason': None}])
        self.assertEqual(len(unresolved), 1)

    def test_every_registered_feature_has_the_same_named_cases_in_both_languages(self):
        registry = report.load(ROOT/'harness/shared_sdk/cases.json')
        mapping = report.load(ROOT/'docs/datadog-shared-sdk-capabilities-mapping.json')
        self.assertEqual(mapping_for_registry(registry), mapping)
        bzl = (ROOT/'harness/shared_sdk/cases.bzl').read_text()
        self.assertEqual(json.loads(bzl.split(' = ', 1)[1]), [row['name'] for row in registry])
        self.assertEqual(len({row['name'] for row in registry}), len(registry))

    def test_missing_feature_declarations_are_never_runtime_passes(self):
        feature = dict(name='example', testCases=['tests/a.py::TestA::test_one', 'tests/a.py::TestA::test_two'])
        manifest = {'declarations': [{'selector': 'tests/a.py::TestA', 'reason': 'missing_feature'}]}
        self.assertEqual(len(report.manifest_exclusions(feature, manifest)), 2)
        self.assertEqual(report.manifest_exclusions(dict(feature, testCases=['tests/another.py::TestA::test_one']), manifest), [])


if __name__ == '__main__':
    unittest.main()
