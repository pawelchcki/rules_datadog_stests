"""Exercise configuration, discovery and propagation in the pinned native tracer."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import hashlib

from ddtrace import __version__, config, tracer
from ddtrace.trace import Context
from ddtrace.propagation.http import HTTPPropagator


def identity(span):
    return {"trace_id": span.trace_id, "span_id": span.span_id, "parent_id": span.parent_id or 0}


def sca_requests_vulnerable_call(peer):
    import requests
    with requests.Session() as session:
        request = requests.Request('GET', peer + '/sca/control').prepare()
        return session.send(request, timeout=5, verify=False)


def sca_requests_alternate_call(peer):
    import requests
    with requests.Session() as session:
        request = requests.Request('GET', peer + '/sca/alternate').prepare()
        return session.send(request, timeout=5)


def main(args):
    result = {"sdkVersion": __version__, "identities": []}
    if args.case.startswith('ai-guard'):
        from ddtrace.aiguard import new_ai_guard_client, AIGuardAbortError
        client = new_ai_guard_client(endpoint=args.peer + '/guard')
        result['evaluations'] = []
        for action, block in [('ALLOW', True), ('DENY', False), ('DENY', True), ('ABORT', True)]:
            with tracer.trace('guard.control', resource=action + '.' + str(block)) as root:
                result['identities'].append(identity(root))
                messages = [{'role': 'user', 'content': action + ':controlled-message'}]
                try:
                    evaluation = client.evaluate(messages, {'block': block})
                    result['evaluations'].append({'action': evaluation['action'], 'blocked': False, 'messages': messages, 'root_id': root.span_id})
                except AIGuardAbortError as error:
                    result['evaluations'].append({'action': error.action, 'blocked': True, 'messages': messages, 'root_id': root.span_id})
    elif args.case.startswith('sca-'):
        import requests
        import ddtrace.appsec.sca._cve_loader as cve_loader
        result['requestsVersion'] = requests.__version__
        result['nativeCveDataSha256'] = hashlib.sha256(Path(cve_loader._CVE_DATA_PATH).read_bytes()).hexdigest()
        # Allow startup dependency telemetry to emit before the first hit.
        time.sleep(2.2)
        for function in (sca_requests_vulnerable_call, sca_requests_alternate_call):
            with tracer.trace('sca.request', span_type='web') as span:
                result['identities'].append(identity(span))
                assert function(args.peer).status_code == 200
            time.sleep(2.2)
    elif args.case.startswith("process-discovery"):
        descriptors = []
        for path in Path('/proc/self/fd').iterdir():
            try:
                target = os.readlink(path)
                if target.startswith('/memfd:datadog-tracer-info-'):
                    descriptors.append({"target": target, "bytes": base64.b64encode(path.read_bytes()).decode()})
            except FileNotFoundError:
                continue
        result['metadata'] = descriptors
    elif args.case == 'baggage':
        incoming = HTTPPropagator.extract({'x-datadog-trace-id': '123', 'x-datadog-parent-id': '456',
            'x-datadog-sampling-priority': '2', 'baggage': 'foo=bar,hello=a%20b'})
        result['extracted'] = dict(incoming.get_all_baggage_items())
        incoming.set_baggage_item('api', 'hello world/你好')
        with tracer.start_span('baggage.child', child_of=incoming, activate=True) as span:
            result['identities'].append(identity(span))
            carrier = {}
            HTTPPropagator.inject(span.context, carrier)
            result['carrier'] = carrier
            result['roundtrip'] = HTTPPropagator.extract(carrier).get_all_baggage_items()
            span.context.remove_baggage_item('foo')
            without = {}
            HTTPPropagator.inject(span.context, without)
            result['removed'] = HTTPPropagator.extract(without).get_all_baggage_items()
    elif args.case == 'otel-propagator':
        from opentelemetry import propagate, trace
        incoming = propagate.extract({'traceparent': '00-11111111111111110000000000000002-000000000000000a-01',
            'tracestate': 'dd=s:2;p:000000000000000a,foo=1', 'baggage': 'foo=bar'})
        context = trace.get_current_span(incoming).get_span_context()
        result['extracted'] = {'trace_id': context.trace_id, 'span_id': context.span_id, 'tracestate': context.trace_state.to_header()}
        with trace.get_tracer(__name__).start_as_current_span('otel.propagated', context=incoming) as span:
            c = span.get_span_context()
            result['identities'].append({'trace_id': c.trace_id, 'span_id': c.span_id, 'parent_id': 10})
            carrier = {}
            propagate.inject(carrier)
            result['carrier'] = carrier
    elif args.case == 'user-identification':
        from ddtrace.contrib.trace_utils import set_user
        with tracer.trace('identify.outgoing') as span:
            set_user(tracer, user_id='usr.id', name='usr.name', email='usr.email',
                     session_id='usr.session_id', role='usr.role', scope='usr.scope', propagate=True)
            result['identities'].append(identity(span))
            carrier = {}
            HTTPPropagator.inject(span.context, carrier)
            result['carrier'] = carrier
        tracer.context_provider.activate(None)
        extracted = HTTPPropagator.extract(carrier)
        with tracer.start_span('identify.incoming', child_of=extracted, activate=True) as span:
            result['identities'].append(identity(span))
    elif args.case.startswith('sampling-'):
        # Fixed IDs cover the complete Knuth hash cycle several times. Contexts
        # omit a sampling decision so the SDK applies the configured root rule.
        for low in range(1, 2001):
            context = Context(trace_id=(0x1111111111111111 << 64) | low, span_id=123)
            with tracer.start_span('sampling.control', child_of=context, activate=True) as span:
                result['identities'].append(identity(span))
    elif args.case.startswith('client-stats'):
        for index in range(3):
            with tracer.trace('stats.request', resource='/users', span_type='web') as span:
                span.set_metric('_dd.measured', 1)
                span.set_tag('http.status_code', '500' if index == 2 else '200')
                span.error = int(index == 2)
                result['identities'].append(identity(span))
    elif args.case.startswith('scrubbing-'):
        import requests
        with tracer.trace('scrubbing.control') as span:
            result['identities'].append(identity(span))
            response = requests.get(args.peer + '/echo?visible=public&token=controlled-secret', timeout=5)
            assert response.status_code == 200
    else:
        result['effective'] = {'service': config.service, 'env': config.env, 'version': config.version, 'tags': config.tags}
    if not result['identities']:
        with tracer.trace('sdk.control') as span:
            result['identities'].append(identity(span))
    tracer.shutdown()
    Path(args.identity_file).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    if not __debug__:
        raise RuntimeError('SDK assertions require optimization disabled')
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    parser.add_argument('--identity-file', required=True)
    parser.add_argument('--peer', required=True)
    main(parser.parse_args())
