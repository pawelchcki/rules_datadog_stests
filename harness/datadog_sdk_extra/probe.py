"""Retain native requests and verify missing SDK capabilities with controls."""
import argparse
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

from harness.datadog_agent.probe import BASE_ENV, resolve, server_thread
from harness.datadog_backend.wire import msgpack

REVISION = '098fe0967c587db8a16b74a1e711777d0a9d5867'
SDK_VERSION = '4.15.4'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Intake(ThreadingHTTPServer):
    def __init__(self, address):
        super().__init__(address, Handler)
        self.records = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/info':
            data = {'version': '7.83.1', 'endpoints': ['/v0.4/traces', '/v0.6/stats'],
                    'client_drop_p0s': True}
        else:
            data = {'query': parse_qs(urlsplit(self.path).query)}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        with self.server.lock:
            self.server.records.append({'path': urlsplit(self.path).path, 'headers': dict(self.headers),
                'body': base64.b64encode(body).decode(), 'sha256': sha(body), 'peer': self.client_address[0]})
        if urlsplit(self.path).path == '/guard/evaluate':
            messages = json.loads(body)['data']['attributes']['messages']
            action = messages[-1]['content'].split(':')[0]
            response = json.dumps({'data': {'attributes': {'action': action, 'reason': 'controlled-' + action,
                'tags': ['controlled-category'], 'is_blocking_enabled': True}}}).encode()
        else:
            response = b'{"rate_by_service":{}}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    do_PUT = do_POST


def decode(record):
    raw = base64.b64decode(record['body'])
    assert sha(raw) == record['sha256']
    if headers(record).get('content-encoding', '').lower() == 'gzip':
        raw = gzip.decompress(raw)
    return msgpack(raw)


def headers(record):
    return {name.lower(): value for name, value in record['headers'].items()}


def native_spans(records):
    return [span for record in records if record['path'] == '/v0.4/traces'
            for chunk in decode(record) for span in chunk]


def assert_export(records, identity):
    assert identity['sdkVersion'] == SDK_VERSION, identity
    spans = native_spans(records)
    index = {s['span_id']: s for s in spans}
    assert len(index) == len(spans), 'Duplicate span identities'
    for item in identity['identities']:
        span = index[item['span_id']]
        assert span['trace_id'] == item['trace_id'] & ((1 << 64) - 1)
        assert span.get('parent_id', 0) == item['parent_id']
        assert span['start'] > 0 and span['duration'] > 0
    assert len(identity['identities']) > 0
    return spans


def check_discovery(records, identity, env):
    spans = assert_export(records, identity)
    metadata = identity['metadata']
    assert len(metadata) == 1, metadata
    assert re.fullmatch(r'/memfd:datadog-tracer-info-[A-Za-z0-9]{8} \(deleted\)', metadata[0]['target'])
    data = msgpack(base64.b64decode(metadata[0]['bytes']))
    assert data['schema_version'] in (1, 2) and data['tracer_language'] == 'python'
    assert data['tracer_version'] == SDK_VERSION and isinstance(data['hostname'], str)
    uuid.UUID(data['runtime_id'])
    for field, key in [('service_name', 'DD_SERVICE'), ('service_env', 'DD_ENV'), ('service_version', 'DD_VERSION')]:
        assert data[field] == env[key], (data, env)
    assert any(s.get('meta', {}).get('runtime-id') == data['runtime_id'] for s in spans)
    if env['DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED'] == 'true':
        assert 'entrypoint.name' in data.get('process_tags', ''), data
    else:
        assert not data.get('process_tags'), data


def check_baggage(records, identity, env):
    spans = assert_export(records, identity)
    assert identity['extracted'] == {'foo': 'bar', 'hello': 'a b'}, identity
    assert identity['roundtrip'] == {'foo': 'bar', 'hello': 'a b', 'api': 'hello world/你好'}
    assert identity['removed'] == {'hello': 'a b', 'api': 'hello world/你好'}
    carrier = identity['carrier']
    assert carrier['x-datadog-trace-id'] == '123'
    assert int(carrier['x-datadog-parent-id']) == identity['identities'][0]['span_id']
    assert 'hello=a%20b' in carrier['baggage'] and 'api=hello%20world' in carrier['baggage']
    assert len(spans) == 1 and spans[0]['trace_id'] == 123 and spans[0]['parent_id'] == 456


def check_otel_propagator(records, identity, env):
    spans = assert_export(records, identity)
    full = int('11111111111111110000000000000002', 16)
    assert identity['extracted']['trace_id'] == full and identity['extracted']['span_id'] == 10
    assert 'foo=1' in identity['extracted']['tracestate']
    version, trace, parent, flags = identity['carrier']['traceparent'].split('-')
    assert version == '00' and int(trace, 16) == full
    assert int(parent, 16) == identity['identities'][0]['span_id'] and flags == '01'
    assert len(spans) == 1 and spans[0]['parent_id'] == 10


def check_identification(records, identity, env):
    spans = assert_export(records, identity)
    assert len(spans) == 2
    outgoing, = [s for s in spans if s['name'] == 'identify.outgoing']
    incoming, = [s for s in spans if s['name'] == 'identify.incoming']
    for field in ('id', 'name', 'email', 'session_id', 'role', 'scope'):
        assert outgoing['meta']['usr.' + field] == 'usr.' + field
    assert outgoing['meta']['_dd.p.usr.id'] == incoming['meta']['_dd.p.usr.id'] == 'dXNyLmlk'
    assert '_dd.p.usr.id=dXNyLmlk' in identity['carrier']['x-datadog-tags']
    assert incoming['trace_id'] == outgoing['trace_id'] and incoming['parent_id'] == outgoing['span_id']
    assert 'usr.id' not in incoming['meta']


def check_sca(records, identity, env):
    spans = assert_export(records, identity)
    assert identity['requestsVersion'] == '2.31.0'
    telemetry = [json.loads(base64.b64decode(r['body'])) for r in records
                 if r['path'] == '/telemetry/proxy/api/v2/apmtelemetry']
    events = [event for doc in telemetry for event in
              (doc['payload'] if doc['request_type'] == 'message-batch' else [doc])]
    configs = [c for event in events if event['request_type'] in ('app-started', 'app-client-configuration-change')
               for c in event['payload'].get('configuration', [])]
    enabled = env['DD_APPSEC_SCA_ENABLED'] == 'true'
    assert any(c['name'] in ('DD_APPSEC_SCA_ENABLED', 'appsec_sca_enabled') and c['value'] in
               (enabled, str(enabled).lower()) for c in configs), configs
    dependencies = [d for event in events if event['request_type'] == 'app-dependencies-loaded'
                    for d in event['payload']['dependencies'] if d['name'] == 'requests']
    assert dependencies and all(d['version'] == '2.31.0' for d in dependencies)
    reached = [json.loads(m['value']) for d in dependencies for m in (d.get('metadata') or [])
               if m['type'] == 'reachability' and json.loads(m['value'])['id'] == 'GHSA-652x-xj99-gmcc']
    if not enabled:
        assert not reached
        return
    initial = [i for i, r in enumerate(reached) if r['reached'] == []]
    observed = [i for i, r in enumerate(reached) if r['reached']]
    assert initial and observed and min(initial) < min(observed), reached
    hits = [r['reached'] for r in reached if r['reached']]
    assert hits and all(len(hit) == 1 for hit in hits), reached
    # The pinned SDK's native frame walker falls back to the instrumented
    # symbol under this runpy launcher. Validate that exact native contract;
    # application caller attribution is deliberately outside this scope.
    assert all(hit[0] == {'path': 'requests.sessions', 'symbol': 'Session.send', 'line': 0}
               for hit in hits), hits
    if env.get('DD_APM_TRACING_ENABLED') == 'false':
        assert all(s['metrics']['_dd.apm.enabled'] == 0 for s in spans)


def check_sampling(records, identity, env):
    spans = assert_export(records, identity)
    assert len(spans) == 2000
    rate = json.loads(env['DD_TRACE_SAMPLING_RULES'])[0]['sample_rate']
    threshold = rate * ((1 << 64) - 1)
    kept = 0
    for span in spans:
        expected = ((span['trace_id'] * 1111111111111111111) % (1 << 64)) <= threshold
        priority = span['metrics']['_sampling_priority_v1']
        assert priority == (2 if expected else -1), (rate, span)
        assert span['metrics']['_dd.rule_psr'] == rate
        kept += int(priority > 0)
    assert abs(kept - 2000 * rate) < 10, (kept, rate)


def check_stats(records, identity, env):
    spans = assert_export(records, identity)
    stats = [decode(r) for r in records if r['path'] == '/v0.6/stats']
    if env['DD_TRACE_STATS_COMPUTATION_ENABLED'] == 'false':
        assert not stats, stats
        return
    assert stats, records
    entries = [row for payload in stats for bucket in payload['Stats'] for row in bucket['Stats']]
    own = [row for row in entries if row['Name'] == 'stats.request' and row['Resource'] == '/users']
    assert own and sum(r['Hits'] for r in own) == 3 and sum(r['Errors'] for r in own) == 1, own
    assert sum(r['TopLevelHits'] for r in own) == 3
    assert all(r['Service'] == 'sdk-extra' and r['Duration'] > 0 for r in own)
    assert all('OkSummary' in r and 'ErrorSummary' in r for r in own)
    assert all(p['Env'] == 'sdk-env' and p['Version'] == 'sdk-version' for p in stats)
    assert sum(s['duration'] for s in spans) == sum(r['Duration'] for r in own)


def check_scrubbing(records, identity, env):
    spans = assert_export(records, identity)
    clients = [s for s in spans if s['name'] == 'requests.request']
    assert len(clients) == 1, spans
    url = clients[0]['meta']['http.url']
    if env.get('DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP') == 'controlled-secret':
        assert 'controlled-secret' not in url and '<redacted>' in url and 'visible=public' in url, url
    else:
        assert 'controlled-secret' not in url and '<redacted>' in url, url
    assert clients[0]['parent_id'] == identity['identities'][0]['span_id']


def check_ai_guard(records, identity, env):
    spans = assert_export(records, identity)
    index = {s['span_id']: s for s in spans}
    api_calls = [r for r in records if r['path'] == '/guard/evaluate']
    assert len(api_calls) == len(identity['evaluations']) == 4
    assert [(e['action'], e['blocked']) for e in identity['evaluations']] == [
        ('ALLOW', False), ('DENY', False), ('DENY', True), ('ABORT', True)]
    assert len(spans) == 8
    for evaluation in identity['evaluations']:
        root = index[evaluation['root_id']]
        children = [s for s in spans if s.get('parent_id') == root['span_id'] and s['resource'] == 'ai_guard']
        assert len(children) == 1, (root, children)
        child = children[0]
        meta = child['meta']
        assert meta['ai_guard.action'] == evaluation['action']
        assert meta['ai_guard.reason'] == 'controlled-' + evaluation['action'] and meta['ai_guard.target'] == 'prompt'
        structured = msgpack(base64.b64decode(child['meta_struct']['ai_guard']['base64'], validate=True))
        assert structured['messages'] == evaluation['messages'] and structured['attack_categories'] == ['controlled-category']
        assert bool(child.get('error', 0)) == evaluation['blocked']
        assert (meta.get('ai_guard.blocked') == 'true') == evaluation['blocked']
        assert root['metrics']['_sampling_priority_v1'] == 2
        if env.get('DD_APM_TRACING_ENABLED') == 'false':
            assert root['meta']['_dd.p.dm'] == '-13', root
            assert root['meta']['_dd.p.ts'] == '20', root
    if env.get('DD_APM_TRACING_ENABLED') == 'false':
        assert all(s['metrics'].get('_dd.apm.enabled') == 0 for s in spans), spans
    for record in api_calls:
        assert headers(record)['dd-api-key'] == 'local-sdk-extra-api-key'
        assert headers(record)['dd-application-key'] == 'local-sdk-extra-app-key'
        request = json.loads(base64.b64decode(record['body']))
        assert request['data']['attributes']['meta'] == {'service': 'sdk-extra', 'env': 'sdk-env'}


def check_ipv6(records, identity, env):
    assert_export(records, identity)
    assert env['DD_AGENT_HOST'] == '::1' and 'DD_TRACE_AGENT_URL' not in env
    assert records and all(r['peer'] == '::1' for r in records)


def check_otlp_traces(records, identity, env):
    assert identity['sdkVersion'] == SDK_VERSION
    own = [r for r in records if r['path'] == '/routed/traces']
    assert own and len(own) == len(records), records
    documents = [json.loads(base64.b64decode(r['body'])) for r in own]
    spans = [s for d in documents for resource in d.get('resourceSpans', d.get('resource_spans', []))
             for scope in resource.get('scopeSpans', resource.get('scope_spans', [])) for s in scope['spans']]
    assert len(spans) == len(identity['identities']), documents
    for actual, expected in zip(spans, identity['identities']):
        trace_id = actual.get('traceId', actual.get('trace_id'))
        span_id = actual.get('spanId', actual.get('span_id'))
        assert trace_id == f"{expected['trace_id']:032x}", actual
        assert span_id == f"{expected['span_id']:016x}", actual
        assert actual['name'] == 'sdk.control'
        assert int(actual.get('endTimeUnixNano', actual.get('end_time_unix_nano'))) > int(actual.get('startTimeUnixNano', actual.get('start_time_unix_nano')))


def check_stable(records, identity, env):
    spans = assert_export(records, identity)
    expected = json.loads(env['SDK_EXTRA_EXPECTED'])
    assert identity['effective']['service'] == expected['service']
    assert identity['effective']['env'] == expected['env']
    assert all(s['service'] == expected['service'] and s['meta']['env'] == expected['env'] for s in spans)
    if 'team' in expected:
        assert all(s['meta']['team'] == expected['team'] for s in spans)


PROFILES = [
    ('ai-guard', {'DD_AI_GUARD_ENABLED': 'true', 'DD_API_KEY': 'local-sdk-extra-api-key', 'DD_APP_KEY': 'local-sdk-extra-app-key'}, ['ai_guard'], check_ai_guard),
    ('ai-guard-standalone', {'DD_AI_GUARD_ENABLED': 'true', 'DD_API_KEY': 'local-sdk-extra-api-key', 'DD_APP_KEY': 'local-sdk-extra-app-key', 'DD_APM_TRACING_ENABLED': 'false'}, ['ai_guard_standalone'], check_ai_guard),
    ('agent-ipv6', {}, ['agent_host_ipv6'], check_ipv6),
    ('otlp-specific-traces', {'OTEL_TRACES_EXPORTER': 'otlp', 'OTEL_EXPORTER_OTLP_TRACES_PROTOCOL': 'http/json'}, ['otel_exporter_otlp_traces_endpoint'], check_otlp_traces),
    ('process-discovery-disabled', {'DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED': 'false'}, ['process_discovery'], check_discovery),
    ('baggage', {'DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED': 'true',
        'DD_TRACE_PROPAGATION_STYLE_EXTRACT': 'datadog,baggage', 'DD_TRACE_PROPAGATION_STYLE_INJECT': 'datadog,baggage'}, ['datadog_baggage_headers'], check_baggage),
    ('otel-propagator', {'DD_TRACE_OTEL_ENABLED': 'true', 'DD_TRACE_PROPAGATION_STYLE_EXTRACT': 'tracecontext,baggage'}, ['otel_propagators_api'], check_otel_propagator),
    ('user-identification', {'DD_TRACE_PROPAGATION_STYLE_EXTRACT': 'datadog'}, ['propagation_of_user_id_rfc'], check_identification),
    ('sampling-quarter', {'DD_TRACE_SAMPLING_RULES': '[{"sample_rate":0.25}]'}, ['twl_customer_controls_ingestion_dd_trace_sampling_rules', 'ensure_that_sampling_is_consistent_across_languages'], check_sampling),
    ('sampling-three-quarters', {'DD_TRACE_SAMPLING_RULES': '[{"sample_rate":0.75}]'}, ['twl_customer_controls_ingestion_dd_trace_sampling_rules', 'ensure_that_sampling_is_consistent_across_languages'], check_sampling),
    ('client-stats-enabled', {'DD_TRACE_STATS_COMPUTATION_ENABLED': 'true'}, ['client_side_stats_supported'], check_stats),
    ('client-stats-disabled', {'DD_TRACE_STATS_COMPUTATION_ENABLED': 'false'}, ['client_side_stats_supported'], check_stats),
    ('scrubbing-default', {}, ['library_scrubbing'], check_scrubbing),
    ('scrubbing-custom', {'DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP': 'controlled-secret'}, ['library_scrubbing'], check_scrubbing),
    ('sca-reachability', {'DD_APPSEC_SCA_ENABLED': 'true', 'DD_INSTRUMENTATION_TELEMETRY_ENABLED': 'true',
        'DD_TELEMETRY_HEARTBEAT_INTERVAL': '1'}, ['runtime_sca_reachability'], check_sca),
    ('sca-standalone', {'DD_APPSEC_SCA_ENABLED': 'true', 'DD_APM_TRACING_ENABLED': 'false',
        'DD_INSTRUMENTATION_TELEMETRY_ENABLED': 'true', 'DD_TELEMETRY_HEARTBEAT_INTERVAL': '1'}, ['sca_standalone'], check_sca),
    ('sca-disabled', {'DD_APPSEC_SCA_ENABLED': 'false', 'DD_INSTRUMENTATION_TELEMETRY_ENABLED': 'true',
        'DD_TELEMETRY_HEARTBEAT_INTERVAL': '1'}, ['runtime_sca_reachability', 'sca_standalone'], check_sca),
]


def execute(args, out, profiles=None, stable=True, receipt_name='datadog-sdk-extra-results.json'):
    results = []
    profiles = list(PROFILES if profiles is None else profiles)
    for name, local, fleet, extra, expected in [
        ('stable-local', {'DD_SERVICE': 'local-service', 'DD_ENV': 'local-env', 'DD_TAGS': 'team:local'}, {}, {}, {'service': 'local-service', 'env': 'local-env', 'team': 'local'}),
        ('stable-env-over-local', {'DD_SERVICE': 'local-service', 'DD_ENV': 'local-env'}, {}, {'DD_SERVICE': 'env-service', 'DD_ENV': 'env-env'}, {'service': 'env-service', 'env': 'env-env'}),
        ('stable-fleet-over-env', {'DD_SERVICE': 'local-service', 'DD_ENV': 'local-env', 'DD_TAGS': 'team:local'},
         {'DD_SERVICE': 'fleet-service', 'DD_ENV': 'fleet-env', 'DD_TAGS': 'team:fleet'},
         {'DD_SERVICE': 'env-service', 'DD_ENV': 'env-env', 'DD_TAGS': 'team:env'},
         {'service': 'fleet-service', 'env': 'fleet-env', 'team': 'fleet'}),
    ]:
        if not stable:
            break
        directory = out / name
        directory.mkdir(parents=True, exist_ok=True)
        paths = {}
        for source, config in [('local', local), ('fleet', fleet)]:
            path = directory / (source + '.yaml')
            # JSON is a strict subset of YAML, accepted by the native reader.
            path.write_text(json.dumps({'apm_configuration_default': config}) + '\n')
            paths[source] = str(path)
        profiles.append((name, dict(extra, _DD_SC_LOCAL_FILE_OVERRIDE=paths['local'], _DD_SC_MANAGED_FILE_OVERRIDE=paths['fleet'],
            SDK_EXTRA_EXPECTED=json.dumps(expected)), ['stable_configuration_support'], check_stable))
    try:
        for name, extra, features, check in profiles:
            directory = out / name
            directory.mkdir(parents=True, exist_ok=True)
            server_class = type('IPv6Intake', (Intake,), {'address_family': socket.AF_INET6}) if name == 'agent-ipv6' else Intake
            with server_class(('::1' if name == 'agent-ipv6' else '127.0.0.1', 0)) as intake, server_thread(intake):
                sink = 'http://127.0.0.1:' + str(intake.server_port)
                env = dict(BASE_ENV, DD_SERVICE='sdk-extra', DD_ENV='sdk-env', DD_VERSION='sdk-version',
                    DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION='v0.4')
                if name.startswith('stable'):
                    for key in ('DD_SERVICE', 'DD_ENV', 'DD_TAGS'):
                        env.pop(key, None)
                env.update(extra)
                if name == 'agent-ipv6':
                    env.pop('DD_TRACE_AGENT_URL')
                    env.update(DD_AGENT_HOST='::1', DD_TRACE_AGENT_PORT=str(intake.server_port))
                if name == 'otlp-specific-traces':
                    env.update(OTEL_EXPORTER_OTLP_ENDPOINT=sink + '/unused', OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=sink + '/routed/traces')
                identity_path = directory / 'identity.json'
                identity_path.unlink(missing_ok=True)
                workload = Path(args.app)
                if name.startswith('sca-'):
                    workload = directory / 'workload.py'
                    shutil.copyfile(args.app, workload)
                result = {'name': name, 'status': 'failed', 'capabilityInventoryRevision': REVISION,
                    'capabilityNames': features, 'configuration': env, 'wire': 'otlp-http-json' if name == 'otlp-specific-traces' else 'v0.4',
                    'captureFile': name + '/capture.json', 'workloadSha256': sha(Path(args.app).read_bytes())}
                results.append(result)
                command = [args.launcher, '--runtime=python', '--rootfs=' + args.rootfs] + args.injection_flags
                command += ['--instance=datadog-sdk-extra-' + name, '--prepend-path=PYTHONPATH=' + args.overlay]
                command += ['--env=' + key + '=' + value for key, value in sorted(env.items())]
                command += ['--', str(workload), '--case', name, '--identity-file', str(identity_path), '--peer', sink]
                clean = {k: v for k, v in os.environ.items() if not k.startswith(('DD_', 'OTEL_', '_DD_', 'SDK_EXTRA_')) and k != 'PYTHONOPTIMIZE'}
                try:
                    with (directory / 'app.log').open('wb') as log:
                        process = subprocess.run(command, stdout=log, stderr=log, env=clean, timeout=45)
                    assert process.returncode == 0, (directory / 'app.log').read_text(errors='replace')[-6000:]
                    identity = json.loads(identity_path.read_text())
                    result['artifacts'] = [{'file': name + '/identity.json', 'sha256': sha(identity_path.read_bytes())}]
                    if name.startswith('sca-'):
                        result['artifacts'].append({'file': name + '/workload.py', 'sha256': sha(workload.read_bytes())})
                    for path in directory.glob('*.yaml'):
                        result['artifacts'].append({'file': name + '/' + path.name, 'sha256': sha(path.read_bytes())})
                    check(intake.records, identity, env)
                    result['status'] = 'passed'
                    print(name, 'passed', flush=True)
                finally:
                    capture = json.dumps(intake.records, sort_keys=True).encode()
                    (directory / 'capture.json').write_bytes(capture)
                    result['captureSha256'] = sha(capture)
    finally:
        (out / receipt_name).write_text(json.dumps({'schemaVersion': 1, 'results': results}, indent=2) + '\n')


def main():
    if not __debug__:
        raise RuntimeError('Native assertions require Python optimization disabled')
    parser = argparse.ArgumentParser()
    for name in ('launcher', 'rootfs', 'app', 'overlay'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--injection-flag', action='append', default=[], dest='injection_flags')
    args = parser.parse_args()
    for name in ('launcher', 'rootfs', 'app', 'overlay'):
        setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ['--instrumentation-rootfs=' + resolve(flag.split('=', 1)[1]) if flag.startswith('--instrumentation-rootfs=') else flag for flag in args.injection_flags]
    out = Path(os.environ['TEST_UNDECLARED_OUTPUTS_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out)


if __name__ == '__main__':
    main()
