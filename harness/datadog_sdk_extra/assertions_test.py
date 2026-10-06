"""Mutation controls over retained native SDK exports."""
import base64
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from harness.datadog_sdk_extra import probe

class NativeAssertions(unittest.TestCase):
    def setUp(self):
        self.data = {i['name']: i for i in json.loads(Path(__file__).with_name('golden.json').read_text())}
        # Replay the original capture version; fresh runtime checks keep the
        # upgraded pinned version and retain its actual binary exports.
        version = patch.object(probe, 'SDK_VERSION', '4.14.0')
        version.start()
        self.addCleanup(version.stop)

    def check(self, name, data=None):
        item = self.data[name] if data is None else data
        check = next(p[3] for p in probe.PROFILES if p[0] == name)
        check(item['records'], item['identity'], item['env'])

    def test_retained_native_exports(self):
        for name in self.data:
            with self.subTest(name=name): self.check(name)

    def test_blocking_deny_cannot_become_an_observed_nonblocking_success(self):
        item = copy.deepcopy(self.data['ai-guard'])
        item['identity']['evaluations'][2]['blocked'] = False
        with self.assertRaises(AssertionError): self.check('ai-guard', item)

    def test_baggage_injection_cannot_reference_an_unrelated_span(self):
        item = copy.deepcopy(self.data['baggage'])
        item['identity']['carrier']['x-datadog-parent-id'] = '999'
        with self.assertRaises(AssertionError): self.check('baggage', item)

    def test_stats_reject_an_extra_hit(self):
        original = probe.decode
        def corrupt(record):
            payload = original(record)
            if record['path'] == '/v0.6/stats': payload['Stats'][0]['Stats'][0]['Hits'] += 1
            return payload
        with patch.object(probe, 'decode', side_effect=corrupt), self.assertRaises(AssertionError):
            self.check('client-stats-enabled')

    def test_sca_requires_reached_metadata_after_automatic_instrumentation(self):
        item = copy.deepcopy(self.data['sca-reachability'])
        for record in item['records']:
            if record['path'] != '/telemetry/proxy/api/v2/apmtelemetry': continue
            document = json.loads(base64.b64decode(record['body']))
            events = document['payload'] if document['request_type'] == 'message-batch' else [document]
            for event in events:
                if event['request_type'] == 'app-dependencies-loaded':
                    for dependency in event['payload']['dependencies']:
                        for metadata in dependency.get('metadata') or []:
                            if metadata['type'] == 'reachability':
                                value = json.loads(metadata['value']); value['reached'] = []
                                metadata['value'] = json.dumps(value)
            record['body'] = base64.b64encode(json.dumps(document).encode()).decode()
        with self.assertRaises(AssertionError): self.check('sca-reachability', item)

if __name__ == '__main__': unittest.main()
