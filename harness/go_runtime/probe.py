"""Observe automatic instrumentation through native intake, with replaceable activation."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from urllib.request import Request, urlopen

LOCK = Path(__file__).with_name('versions.lock.json')


def resolve(path):
    candidate = Path(path)
    if candidate.exists():
        return str(candidate.resolve())
    roots = [os.environ.get('RUNFILES_DIR'), os.environ.get('TEST_SRCDIR')]
    roots += [str(parent) for parent in Path(__file__).absolute().parents if parent.name.endswith('.runfiles')]
    for root in roots:
        if root and (Path(root)/path).exists():
            return str((Path(root)/path).resolve())
    raise FileNotFoundError(str((path, roots)))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request(url, method='GET', headers=None):
    with urlopen(Request(url, method=method, headers=headers or {}), timeout=10) as response:
        return json.load(response)


def reset(sink):
    for protocol in ('datadog', 'otlp'):
        with urlopen(Request(sink+'/reset?protocol='+protocol, method='POST'), timeout=5):
            pass


def capture(sink):
    return {protocol: request(sink+'/dump?protocol='+protocol) for protocol in ('datadog', 'otlp')}


def attributes(values):
    return {value['key']: next(iter(value['value']['value'].values())) for value in values}


def configuration(args, env):
    # Retain instrumentation settings, without publishing unrelated inherited
    # CI credentials or the rest of the launcher environment.
    return {key: ('<redacted>' if any(word in key.lower() for word in ('token', 'secret', 'password', 'api_key')) else value)
            for key, value in env.items()
            if key.startswith(('DD_', 'OTEL_')) or key in args.environment or key == 'LD_PRELOAD'}


def substitute(value, values):
    for key, replacement in values.items():
        value = value.replace('{'+key+'}', replacement)
    return value


def spans(records):
    """Project native intake IDs/attributes for assertions; retain the raw capture separately."""
    results = {}
    for record in records['datadog']:
        for trace in record['payload']['traces']:
            high = next((span['meta']['_dd.p.tid'] for span in trace if span.get('meta', {}).get('_dd.p.tid')), '0000000000000000')
            for span in trace:
                meta = span.get('meta', {})
                trace_id = meta.get('_dd.p.tid', high)+f"{span['trace_id']:016x}"
                item = dict(protocol='datadog', trace=trace_id, id=f"{span['span_id']:016x}", parent=f"{span.get('parent_id', 0):016x}",
                            kind={'web': 'server', 'http': 'client'}.get(span.get('type')),
                            status=meta.get('http.status_code'), url=meta.get('http.url'),
                            error=bool(span.get('error', 0)), service=span.get('service'),
                            duration=span['duration'])
                results[('datadog', item['trace'], item['id'])] = item
    for record in records['otlp']:
        if record['signal'] != 'traces':
            continue
        for resource in record['payload']['resource_spans']:
            service = attributes(resource['resource']['attributes']).get('service.name')
            for scope in resource['scope_spans']:
                for span in scope['spans']:
                    tags = attributes(span['attributes'])
                    item = dict(protocol='otlp', trace=span['trace_id'], id=span['span_id'], parent=span['parent_span_id'],
                                kind={2: 'server', 3: 'client'}.get(span['kind']),
                                status=str(tags.get('http.response.status_code', tags.get('http.status_code', ''))),
                                url=tags.get('url.full', tags.get('http.url', '')),
                                error=span.get('status', {}).get('code') == 2, service=service,
                                duration=int(span['end_time_unix_nano'])-int(span['start_time_unix_nano']))
                    results[('otlp', item['trace'], item['id'])] = item
    return list(results.values())


def assert_workload(response, status):
    assert response['integer'] == 68 and response['float'] == 3.75 and response['text'] == 'abi', response
    assert response['stackTotal'] == 8*528 and response['callback'] == 9.5, response
    assert response['targetStatus'] == status, response


def assert_trace(records, trace_id, parent_id, response, status):
    native = [span for span in spans(records) if span['trace'] == trace_id]
    roots = [span for span in native if span['parent'] == parent_id and span['kind'] == 'server']
    assert len(roots) == 1, ('Expected one automatically traced inbound request', native)
    root = roots[0]
    clients = [span for span in native if span['parent'] == root['id'] and span['kind'] == 'client']
    assert len(clients) == 1, ('Expected one automatically traced outbound request', native)
    client = clients[0]
    targets = [span for span in native if span['parent'] == client['id'] and span['kind'] == 'server']
    assert len(targets) == 1, ('Expected a propagated server/client/server trace', native)
    target = targets[0]
    assert client['status'] == target['status'] == str(status), native
    assert root['status'] == '200', native
    # Datadog net/http marks client transport errors; OpenTelemetry additionally
    # marks received 5xx responses. Both backends mark the target server error.
    assert client['error'] == (status >= 500 and client['protocol'] == 'otlp'), native
    assert target['error'] == (status >= 500) and not root['error'], native
    assert all(span['duration'] > 0 and span['service'] for span in (root, client, target)), native
    propagated = response['target']
    if propagated['traceparent']:
        _, tid, sid, _ = propagated['traceparent'].split('-')
        assert tid == trace_id and sid == client['id'], propagated
    else:
        assert int(propagated['datadogTraceID']) == int(trace_id[-16:], 16), propagated
        assert int(propagated['datadogParentID']) == int(client['id'], 16), propagated


def assert_instrumentor(records, backend):
    if backend == 'orchestrion':
        pin = json.loads(LOCK.read_text())['instrumentation'][backend]
        assert records['datadog'] and not records['otlp'], 'Orchestrion must export native Datadog traces'
        for record in records['datadog']:
            headers = {item['name'].lower(): item['value'] for item in record['request']['headers']}
            assert headers['datadog-meta-tracer-version'] == 'v'+pin['sdkVersion'], headers
    elif backend == 'alibaba':
        assert records['otlp'] and not records['datadog'], 'Alibaba must export native OTLP traces'


@contextmanager
def application(args, sink, out, phase, disabled=False):
    ready = out/(phase+'.ready.json')
    ready.unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('DD_', 'OTEL_')) and key != 'LD_PRELOAD'}
    env.update(DD_SERVICE='go-runtime', DD_ENV='test', DD_VERSION='1', DD_TRACE_AGENT_URL=sink,
               DD_TRACE_API_VERSION='v0.4', DD_TRACE_ENABLED='false' if disabled else 'true',
               DD_TRACE_SAMPLING_RULES='[{"sample_rate":1}]', DD_TRACE_RATE_LIMIT='1000',
               DD_TRACE_PROPAGATION_STYLE='tracecontext,baggage', DD_TRACE_STARTUP_LOGS='false',
               DD_INSTRUMENTATION_TELEMETRY_ENABLED='false', DD_REMOTE_CONFIGURATION_ENABLED='false',
               DD_APPSEC_ENABLED='false', DD_PROFILING_ENABLED='false',
               OTEL_SERVICE_NAME='go-runtime', OTEL_TRACES_EXPORTER='none' if disabled else 'otlp',
               OTEL_SDK_DISABLED='true' if disabled else 'false', OTEL_EXPORTER_OTLP_ENDPOINT=sink,
               OTEL_EXPORTER_OTLP_PROTOCOL='http/protobuf', OTEL_BSP_SCHEDULE_DELAY='10',
               OTEL_TRACES_SAMPLER='always_on', OTEL_METRICS_EXPORTER='none', OTEL_LOGS_EXPORTER='none')
    if args.backend == 'orchestrion':
        env = {key: value for key, value in env.items() if not key.startswith('OTEL_')}
    if not disabled:
        values = dict(app=args.app, library=args.library or '', sink=sink, output=str(out), phase=phase)
        env.update({key: substitute(value, values) for key, value in args.environment.items()})
        if args.mode == 'startup' and args.library:
            env['LD_PRELOAD'] = args.library
    with (out/(phase+'.app.log')).open('wb') as log:
        executable = args.control_app if disabled and args.control_app else args.app
        proc = subprocess.Popen([executable, '--ready-file', str(ready)], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+30
            while not ready.exists():
                assert proc.poll() is None, 'Application exited before readiness'
                assert time.monotonic() < deadline, 'Readiness timed out'
                time.sleep(.02)
            identity = json.loads(ready.read_text())
            assert identity['pid'] == proc.pid, identity
            while True:
                try:
                    health = request(identity['url']+'/healthz')
                    break
                except OSError:
                    assert proc.poll() is None and time.monotonic() < deadline, 'Health timed out'
                    time.sleep(.02)
            assert health['runtime'] == args.runtime and health['architecture'] == args.arch, health
            assert health['pid'] == proc.pid and health['cgo'], health
            yield proc, identity['url'], health, env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def execute(args):
    out = Path(args.output or os.environ['TEST_UNDECLARED_OUTPUTS_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text())
    assert manifest['runtime'] == args.runtime and manifest['architecture'] == args.arch
    receipt = dict(runtime=args.runtime, architecture=args.arch, backend=args.backend, mode=args.mode,
                   applicationManifest=manifest, repetitions=2, artifacts=[])

    def save(name, value):
        path = out/name
        path.write_text(json.dumps(value, indent=2)+'\n')
        receipt['artifacts'] = [entry for entry in receipt['artifacts'] if entry['file'] != name]
        receipt['artifacts'].append(dict(file=name, sha256=sha(path)))

    try:
        if manifest['status'] == 'unsupported-build':
            assert args.backend == manifest['backend'] and args.backend in ('orchestrion', 'alibaba')
            assert args.mode == 'startup' and not args.library and not args.attacher
            pin = json.loads(LOCK.read_text())['instrumentation'][args.backend]
            assert manifest['instrumentation'] == pin and int(args.runtime.split('.')[1]) < pin['minimumGoMinor']
            receipt.update(status='unsupported-build', reason=manifest['reason'])
            return
        if manifest['status'] == 'unsupported-toolchain':
            row = next(row for row in json.loads(LOCK.read_text())['runtimes'] if row['version'] == args.runtime)
            assert args.arch not in row['toolchains']
            receipt.update(status='unsupported-toolchain', reason=manifest['reason'])
            return
        assert manifest['status'] == 'built', manifest.get('reason', manifest['status'])
        assert manifest['status'] == 'built' and manifest['elf']['sha256'] == sha(args.app)
        if args.control_app:
            control = json.loads(Path(args.control_manifest).read_text())
            assert control['runtime'] == args.runtime and control['architecture'] == args.arch
            assert control['status'] == 'built' and control['backend'] == 'plain'
            assert control['elf']['sha256'] == sha(args.control_app)
            receipt['controlManifest'] = control
        else:
            assert manifest['backend'] == 'plain', 'Automatic instrumentation requires a tracer-free control app'
            receipt['controlManifest'] = manifest
        if args.backend == 'custom':
            assert manifest['backend'] == 'plain', 'Custom instrumentation must replace, not layer over, compiled tracers'
        else:
            assert args.backend == manifest['backend']
            assert args.mode == 'startup' and not args.library and not args.attacher
        if args.mode == 'attach':
            assert args.backend == 'custom' and args.attacher, 'Attach mode requires a custom attacher executable'
        if args.library:
            receipt['librarySha256'] = sha(args.library)
        if args.attacher:
            receipt['attacherSha256'] = sha(args.attacher)
        sink = args.sink
        if not sink:
            ports = json.loads(os.environ['ASSIGNED_PORTS'])
            sink = 'http://127.0.0.1:'+str(next(value for key, value in ports.items() if key.endswith('//harness:otel_sink_service')))
        for phase in ('primary', 'repeat'):
            reset(sink)
            with application(args, sink, out, phase+'.control', disabled=True) as (_, url, health, env):
                response = request(url+'/capability?status=200')
                assert_workload(response, 200)
                time.sleep(2.2)
                records = capture(sink)
                save(phase+'.control.json', dict(health=health, response=response, configuration=configuration(args, env)))
                save(phase+'.control.capture.json', records)
                assert not spans(records), 'Disabled/uninstrumented control emitted traces'
            if args.backend == 'plain':
                continue
            reset(sink)
            with application(args, sink, out, phase) as (proc, url, health, env):
                save(phase+'.health.json', dict(health=health, configuration=configuration(args, env)))
                if args.mode == 'attach':
                    response = request(url+'/capability?status=200')
                    assert_workload(response, 200)
                    time.sleep(2.2)
                    before = capture(sink)
                    save(phase+'.before-attach.capture.json', before)
                    assert not spans(before), 'Attach workload was instrumented before activation'
                    values = dict(pid=str(proc.pid), app=args.app, library=args.library or '', sink=sink,
                                  url=url, output=str(out), phase=phase)
                    command = [args.attacher]+[substitute(arg, values) for arg in args.attach_args]
                    with (out/(phase+'.attach.log')).open('wb') as log:
                        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=30, check=True)
                    assert proc.poll() is None and request(url+'/healthz')['pid'] == proc.pid, 'Attacher replaced or killed the application'
                    save(phase+'.attach.json', dict(command=command, pid=proc.pid))
                    reset(sink)
                for status in (200, 503):
                    # Fresh known external parents tie exported spans to this
                    # workload without importing a tracer into the application.
                    trace_id = hashlib.sha256((phase+str(status)).encode()).hexdigest()[:32]
                    parent_id = '0000000000000123'
                    response = request(url+'/capability?status='+str(status),
                                       headers={'traceparent': '00-'+trace_id+'-'+parent_id+'-01'})
                    assert_workload(response, status)
                    save(phase+'.'+str(status)+'.response.json', response)
                    deadline = time.monotonic()+12
                    while True:
                        records = capture(sink)
                        save(phase+'.'+str(status)+'.capture.json', records)
                        try:
                            assert_trace(records, trace_id, parent_id, response, status)
                            assert_instrumentor(records, args.backend)
                            break
                        except AssertionError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.1)
        receipt['status'] = 'control-only' if args.backend == 'plain' else 'passed'
    except Exception:
        receipt.update(status='failed', detail=traceback.format_exc())
        raise
    finally:
        receipt['artifacts'] = [dict(file=str(path.relative_to(out)), sha256=sha(path))
                                for path in sorted(out.rglob('*'))
                                if path.is_file() and path.name != 'go-runtime-results.json']
        (out/'go-runtime-results.json').write_text(json.dumps(receipt, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--control-app')
    parser.add_argument('--control-manifest')
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--arch', choices=('amd64', 'arm64'), default='amd64')
    parser.add_argument('--backend', choices=('plain', 'orchestrion', 'alibaba', 'custom'), required=True)
    parser.add_argument('--mode', choices=('startup', 'attach'), default='startup')
    parser.add_argument('--library')
    parser.add_argument('--attacher')
    parser.add_argument('--attach-arg', action='append', default=[], dest='attach_args')
    parser.add_argument('--env', action='append', default=[])
    parser.add_argument('--sink')
    parser.add_argument('--output')
    args = parser.parse_args()
    for name in ('app', 'manifest', 'control_app', 'control_manifest', 'library', 'attacher'):
        if getattr(args, name):
            setattr(args, name, resolve(getattr(args, name)))
    args.environment = dict(value.split('=', 1) for value in args.env)
    execute(args)


if __name__ == '__main__':
    main()
