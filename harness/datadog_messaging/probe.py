"""Assertions over real AMQP transport and native instrumentation exports."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import struct

from harness.datadog_sdk_extra.probe import assert_export, decode, execute, resolve


def fnv(raw):
    value = 14695981039346656037
    for b in raw:
        value = ((value * 1099511628211) & ((1 << 64) - 1)) ^ b
    return value


def pathway_hash(tags, parent):
    node = fnv(('sdk-extra' + 'sdk-env' + ''.join(sorted(tags))).encode())
    return fnv(struct.pack('<QQ', node, parent))


def check_messaging(records, identity, env):
    spans = assert_export(records, identity)
    frames = identity['amqpFrames']
    assert frames and all(hashlib.sha256(base64.b64decode(f['body'])).hexdigest() == f['sha256'] for f in frames)
    publication, = identity['publications']
    assert json.loads(publication['body']) == {'message': 'controlled-body'}
    typ = next(iter(identity['exchanges'].values()))
    n = 1 if typ == 'direct' else 3
    assert len(publication['queues']) == len(identity['received']) == len(identity['deliveries']) == n
    assert all(r['body'] == {'message': 'controlled-body'} for r in identity['received'])
    assert all(v == 0 for v in identity['remaining'].values())
    if typ == 'topic':
        assert 'topic-queue-3' not in publication['queues']
        assert len(identity['bindings']) == 4
    root, = [s for s in spans if s['name'] == 'messaging.request']
    producer, = [s for s in spans if s['name'] == 'kombu.publish']
    consumers = [s for s in spans if s['name'] == 'kombu.receive']
    assert len(consumers) == n
    assert producer['parent_id'] == root['span_id'] and producer['trace_id'] == root['trace_id']
    assert producer['meta']['span.kind'] == 'producer'
    headers = publication['headers']
    assert int(headers['x-datadog-trace-id']) == producer['trace_id']
    assert int(headers['x-datadog-parent-id']) == producer['span_id']
    for consumer in consumers:
        assert consumer['trace_id'] == producer['trace_id'] and consumer['parent_id'] == producer['span_id']
        assert consumer['meta']['span.kind'] == 'consumer' and consumer['meta']['component'] == 'kombu'
        assert consumer['meta']['kombu.exchange'] == publication['exchange']
        assert consumer['meta']['kombu.routing_key'] == publication['routing_key']
    stats = [decode(r) for r in records if r['path'] == '/v0.1/pipeline_stats']
    if env['DD_DATA_STREAMS_ENABLED'] == 'false':
        assert not stats and 'dd-pathway-ctx-base64' not in headers
        return
    assert 'dd-pathway-ctx-base64' in headers
    outgoing = ['direction:out', 'exchange:' + publication['exchange'],
                'has_routing_key:' + str(bool(publication['routing_key'])).lower(), 'type:rabbitmq']
    parent = pathway_hash(outgoing, 0)
    assert struct.unpack('<Q', base64.b64decode(headers['dd-pathway-ctx-base64'])[:8])[0] == parent
    rows = [r for d in stats for bucket in d['Stats'] for r in bucket['Stats']]
    expected = [(outgoing, parent, 0)]
    for delivery in identity['deliveries']:
        tags = ['direction:in', 'topic:' + delivery['queue'], 'type:rabbitmq']
        expected.append((tags, pathway_hash(tags, parent), parent))
    assert len(rows) == len(expected), rows
    for tags, hash_, parent_ in expected:
        row, = [r for r in rows if set(r['EdgeTags']) == set(tags)]
        assert row['Hash'] == hash_ and row['ParentHash'] == parent_, row
        for sketch in ('PathwayLatency', 'EdgeLatency', 'PayloadSize'):
            assert row[sketch], row
    assert all(d['Service'] == 'sdk-extra' and d['Env'] == 'sdk-env' for d in stats)


PROFILES = []
for typ, feature in [('direct', 'datastreams_monitoring_support_for_rabbitmq'),
                     ('fanout', 'datastreams_monitoring_support_for_rabbitmq_fanout'),
                     ('topic', 'datastreams_monitoring_support_for_rabbitmq_topicexchange')]:
    for enabled in ('true', 'false'):
        PROFILES.append((typ + '-' + enabled, {'DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED': 'false', 'DD_DATA_STREAMS_ENABLED': enabled, 'DD_TRACE_PROPAGATION_STYLE_EXTRACT': 'datadog'},
                         [feature, 'rabbitmq_span_creationcontext_propagation_with_dd_trace'], check_messaging))


def main():
    if not __debug__:
        raise RuntimeError('Messaging assertions require optimization disabled')
    parser = argparse.ArgumentParser()
    for name in ('launcher', 'rootfs', 'app', 'overlay'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--injection-flag', action='append', default=[], dest='injection_flags')
    args = parser.parse_args()
    for name in ('launcher', 'rootfs', 'app', 'overlay'):
        setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ['--instrumentation-rootfs=' + resolve(f.split('=', 1)[1]) if f.startswith('--instrumentation-rootfs=') else f for f in args.injection_flags]
    out = Path(os.environ['TEST_UNDECLARED_OUTPUTS_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out, profiles=PROFILES, stable=False, receipt_name='datadog-messaging-results.json')


if __name__ == '__main__':
    main()
