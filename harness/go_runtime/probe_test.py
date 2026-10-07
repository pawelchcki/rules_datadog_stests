"""Regression checks for native evidence and replacement activation boundaries."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import probe


def response(status=200):
    return dict(integer=68, float=3.75, text='abi', stackTotal=4224,
                callback=9.5, targetStatus=status, target=dict(traceparent='00-'+'a'*32+'-0000000000000002-01'))


def native(status=200):
    trace = []
    for sid, parent, kind, code in ((1, 0x123, 'web', 200), (2, 1, 'http', status), (3, 2, 'web', status)):
        trace.append(dict(trace_id=int('a'*16, 16), span_id=sid, parent_id=parent,
                          type=kind, service='fixture', duration=1, error=int(code >= 500 and kind == 'web'),
                          meta={'http.status_code': str(code)}))
    trace[0]['meta']['_dd.p.tid'] = 'a'*16
    return dict(datadog=[{'payload': {'traces': [trace]}}], otlp=[])


class NativeEvidenceTest(unittest.TestCase):
    def test_high_trace_bits_are_shared_by_chunk(self):
        probe.assert_trace(native(), 'a'*32, '0000000000000123', response(), 200)
        probe.assert_trace(native(503), 'a'*32, '0000000000000123', response(503), 503)

    def test_missing_span_broken_parent_and_status_fail(self):
        for mutation in ('missing', 'parent', 'status', 'error'):
            records = native(503)
            trace = records['datadog'][0]['payload']['traces'][0]
            if mutation == 'missing':
                trace.pop()
            elif mutation == 'parent':
                trace[2]['parent_id'] = 999
            elif mutation == 'status':
                trace[1]['meta']['http.status_code'] = '200'
            else:
                trace[2]['error'] = 0
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                probe.assert_trace(records, 'a'*32, '0000000000000123', response(503), 503)

    def test_no_native_spans_cannot_pass(self):
        with self.assertRaises(AssertionError):
            probe.assert_trace(dict(datadog=[], otlp=[]), 'a'*32, '0000000000000123', response(), 200)

    def test_otlp_native_attributes(self):
        attributes = lambda values: [{'key': key, 'value': {'value': {'string_value': value}}} for key, value in values.items()]
        records = dict(datadog=[], otlp=[dict(signal='traces', payload=dict(resource_spans=[dict(
            resource=dict(attributes=attributes({'service.name': 'fixture'})), scope_spans=[dict(spans=[dict(
                trace_id='a'*32, span_id='1'*16, parent_span_id='2'*16, kind=3,
                start_time_unix_nano='10', end_time_unix_nano='20', status={'code': 2},
                attributes=attributes({'http.response.status_code': '503'}))])])]))])
        self.assertEqual(probe.spans(records)[0]['status'], '503')
        self.assertEqual(probe.spans(records)[0]['duration'], 10)
        self.assertTrue(probe.spans(records)[0]['error'])


class ActivationTest(unittest.TestCase):
    def test_placeholder_substitution_preserves_json(self):
        self.assertEqual(probe.substitute('{"endpoint":"{sink}"}', {'sink': 'http://sink'}),
                         '{"endpoint":"http://sink"}')

    def test_configuration_does_not_publish_inherited_credentials(self):
        args = argparse.Namespace(environment={'MY_SECRET': 'hidden'})
        self.assertEqual(probe.configuration(args, {'GITHUB_TOKEN': 'hidden', 'DD_SERVICE': 'fixture', 'MY_SECRET': 'hidden'}),
                         {'DD_SERVICE': 'fixture', 'MY_SECRET': '<redacted>'})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        app = self.out/'app'
        app.write_bytes(b'fixture-binary')
        self.manifest = dict(runtime='go1.25.14', architecture='amd64', backend='plain', status='built',
                             elf={'sha256': probe.sha(app)})
        manifest = self.out/'manifest.json'
        manifest.write_text(json.dumps(self.manifest))
        self.args = argparse.Namespace(app=str(app), manifest=str(manifest), runtime='go1.25.14',
            control_app=None, control_manifest=None,
            arch='amd64', backend='custom', mode='attach', library=None, attacher=str(app),
            attach_args=['{pid}', '{sink}', '{app}'], environment={}, sink='http://sink', output=str(self.out))

    def test_attach_runs_after_readiness_and_keeps_pid(self):
        calls = []
        proc = Mock(pid=71)
        proc.poll.return_value = None

        @contextmanager
        def application(*args, **kwargs):
            calls.append(('ready', kwargs.get('disabled', False)))
            yield proc, 'http://app', {'pid': 71}, {}

        def activate(command, **kwargs):
            calls.append(('attach', command))

        captures = [dict(datadog=[], otlp=[])]*20
        with patch.object(probe, 'application', application), patch.object(probe, 'reset'), \
                patch.object(probe, 'capture', side_effect=captures), patch.object(probe.time, 'sleep'), \
                patch.object(probe, 'request', side_effect=lambda url, **kw: {'pid': 71} if url.endswith('/healthz') else response(503 if '503' in url else 200)), \
                patch.object(probe.subprocess, 'run', side_effect=activate), patch.object(probe, 'assert_trace'):
            probe.execute(self.args)
        self.assertEqual([call[0] for call in calls], ['ready', 'ready', 'attach', 'ready', 'ready', 'attach'])
        self.assertEqual(calls[2][1][1:], ['71', 'http://sink', self.args.app])

    def test_unsupported_requires_real_pinned_minimum(self):
        pin = json.loads(probe.LOCK.read_text())['instrumentation']['orchestrion']
        self.manifest.update(status='unsupported-build', backend='orchestrion', instrumentation=pin, reason='minimum Go')
        Path(self.args.manifest).write_text(json.dumps(self.manifest))
        self.args.backend, self.args.mode, self.args.attacher = 'orchestrion', 'startup', None
        # Go 1.25 is supported: a false unsupported manifest must fail.
        with self.assertRaises(AssertionError):
            probe.execute(self.args)
        self.assertEqual(json.loads((self.out/'go-runtime-results.json').read_text())['status'], 'failed')

    def test_tampered_binary_fails_before_launch(self):
        Path(self.args.app).write_bytes(b'tampered')
        with patch.object(probe, 'application') as launch, self.assertRaises(AssertionError):
            probe.execute(self.args)
        launch.assert_not_called()

    def test_custom_backend_rejects_compiled_tracer(self):
        self.manifest['backend'] = 'orchestrion'
        Path(self.args.manifest).write_text(json.dumps(self.manifest))
        with self.assertRaises(AssertionError):
            probe.execute(self.args)


if __name__ == '__main__':
    unittest.main()
