"""Reject corrupt routing, AMQP frames and native DSM parent edges."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from harness.datadog_messaging import probe

class MessagingAssertions(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path(__file__).with_name('golden.json').read_text())

    def check(self):
        probe.check_messaging(self.data['records'], self.data['identity'], self.data['env'])

    def test_retained_native_topic_export(self): self.check()

    def test_missing_routed_delivery_is_rejected(self):
        self.data['identity']['publications'][0]['queues'].pop()
        with self.assertRaises(AssertionError): self.check()

    def test_corrupt_amqp_frame_is_rejected(self):
        self.data['identity']['amqpFrames'][0]['sha256'] = '0' * 64
        with self.assertRaises(AssertionError): self.check()

    def test_dsm_consumer_parent_must_match_the_producer_pathway(self):
        original = probe.decode
        def corrupt(record):
            document = original(record)
            if record['path'] == '/v0.1/pipeline_stats':
                document = copy.deepcopy(document)
                for bucket in document['Stats']:
                    for point in bucket['Stats']:
                        if point['ParentHash']: point['ParentHash'] += 1
            return document
        with patch.object(probe, 'decode', side_effect=corrupt), self.assertRaises(AssertionError): self.check()

if __name__ == '__main__': unittest.main()
