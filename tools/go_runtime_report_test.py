import json
from pathlib import Path
import tempfile
import unittest

import go_runtime_report as report
from retain_go_runtime_evidence import retain
from harness.go_runtime import probe


class ReportTest(unittest.TestCase):
    def test_representative_retention_excludes_stale_full_matrix_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ('go1.17.13', 'go1.18.10'):
                stem = version.replace('.', '_')
                outputs = root/'logs/fixtures'/('go_runtime_plain_suite_' + stem + '_test')/'test.outputs'
                outputs.mkdir(parents=True)
                (outputs/'capture.json').write_text('[]')
                app = root/'apps'/(stem + '_plain')
                app.mkdir(parents=True)
                (app/'manifest.json').write_text('{}')
            retain(root/'logs', root/'apps', root/'evidence', versions=['go1.17.13'])
            self.assertEqual(['go_runtime_plain_suite_go1_17_13_test'],
                             [path.name for path in (root/'evidence/tests/fixtures').iterdir()])
            self.assertEqual(['go1_17_13_plain'], [path.name for path in (root/'evidence/applications').iterdir()])

    def test_read_only_bazel_evidence_can_be_retained_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root/'logs'
            test = logs/'fixtures/go_runtime_plain_suite_go1_17_13_test'
            outputs = test/'test.outputs'
            outputs.mkdir(parents=True)
            (outputs/'capture.json').write_text('[]')
            (outputs/'capture.json').chmod(0o444)
            (test/'test.log').write_text('passed')
            (test/'test.log').chmod(0o444)
            outputs.chmod(0o555)
            try:
                retain(logs, root/'apps', root/'evidence')
                retain(logs, root/'apps', root/'evidence')
                retained = root/'evidence/tests/fixtures'/test.name
                self.assertEqual((retained/'test.log').read_text(), 'passed')
                self.assertEqual((retained/'capture.json').read_text(), '[]')
            finally:
                outputs.chmod(0o755)

    def test_hashes_and_required_control_artifacts_are_gated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root/'apps/go1_25_14_plain'
            app.mkdir(parents=True)
            (app/'app').write_bytes(b'application')
            manifest = dict(runtime='go1.25.14', architecture='amd64', backend='plain', status='built',
                            elf={'sha256': probe.sha(app/'app')})
            (app/'manifest.json').write_text(json.dumps(manifest))
            receipt = dict(runtime='go1.25.14', architecture='amd64', backend='plain', mode='startup',
                           status='control-only', repetitions=2, applicationManifest=manifest,
                           controlManifest=manifest, artifacts=[])
            for phase in ('primary', 'repeat'):
                control = dict(health={'runtime': 'go1.25.14', 'architecture': 'amd64'},
                               response=dict(integer=68, float=3.75, text='abi', stackTotal=4224,
                                             callback=9.5, targetStatus=200))
                for suffix, data in (('control.json', control), ('control.capture.json', dict(datadog=[], otlp=[]))):
                    name = phase+'.'+suffix
                    (root/name).write_text(json.dumps(data))
                    receipt['artifacts'].append({'file': name, 'sha256': probe.sha(root/name)})
            path = root/'go-runtime-results.json'
            path.write_text(json.dumps(receipt))
            self.assertEqual(report.validate(path, root/'apps')['status'], 'control-only')
            (root/'repeat.control.capture.json').write_text('{}')
            with self.assertRaisesRegex(AssertionError, 'changed artifact'):
                report.validate(path, root/'apps')

    def test_false_unsupported_claim_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root/'apps/go1_25_14_orchestrion'
            app.mkdir(parents=True)
            pin = json.loads(probe.LOCK.read_text())['instrumentation']['orchestrion']
            manifest = dict(runtime='go1.25.14', architecture='amd64', backend='orchestrion',
                            status='unsupported-build', instrumentation=pin)
            (app/'manifest.json').write_text(json.dumps(manifest))
            receipt = dict(runtime='go1.25.14', architecture='amd64', backend='orchestrion',
                           status='unsupported-build', applicationManifest=manifest)
            path = root/'go-runtime-results.json'
            path.write_text(json.dumps(receipt))
            with self.assertRaises(AssertionError):
                report.validate(path, root/'apps')


if __name__ == '__main__':
    unittest.main()
