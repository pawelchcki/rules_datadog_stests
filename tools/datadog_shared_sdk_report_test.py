import json
from pathlib import Path
import unittest

import datadog_shared_sdk_report as report
from update_shared_sdk_cases import applies, resolve_manifest, mapping_for_registry

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_version_conditions_at_the_go_pin(self):
        self.assertTrue(applies('>=1.52.0 <2.7.0-dev', '2.6.0'))
        self.assertFalse(applies('>=1.52.0 <2.7.0-dev', '2.10.1'))
        self.assertTrue(applies('>=2.10.1-dev', '2.10.1'))
        self.assertFalse(applies('<2.10.1-dev', '2.10.1'))
        self.assertTrue(applies('v4.13.0rc1', '4.15.5'))
        with self.assertRaises(ValueError):
            applies('unsupported expression', '2.10.1')

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
