"""Real Kombu AMQP transport, native ddtrace producer/consumer and DSM hooks."""
import argparse
import json
from pathlib import Path
import threading
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ddtrace import __version__, patch, tracer
patch(kombu=True)
from kombu import Connection, Consumer, Exchange, Producer, Queue
from peer import Peer


def identity(span):
    return dict(trace_id=span.trace_id, span_id=span.span_id, parent_id=span.parent_id or 0)


def run(args):
    result = {'sdkVersion': __version__, 'identities': [], 'received': []}
    typ = args.case.split('-')[0]
    exchange = Exchange('controlled-' + typ, type=typ)
    patterns = {'direct': ['events.created'], 'fanout': ['', '', ''],
                'topic': ['events.*', 'events.#', '#.created', 'ignored.*']}[typ]
    with Peer() as peer:
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        with Connection('amqp://guest:guest@127.0.0.1:' + str(peer.server_address[1]) + '//', heartbeat=0) as connection:
            channel = connection.channel()
            queues = [Queue(typ + '-queue-' + str(i), exchange=exchange, routing_key=p) for i, p in enumerate(patterns)]
            for queue in queues:
                queue(channel).declare()
            with tracer.trace('messaging.request') as root:
                result['identities'].append(identity(root))
                Producer(channel, exchange=exchange).publish({'message': 'controlled-body'}, routing_key='' if typ == 'fanout' else 'events.created', serializer='json', retry=False)
            tracer.context_provider.activate(None)
            for queue in queues[:3] if typ != 'direct' else queues:
                def received(body, message, queue=queue):
                    span = tracer.current_span()
                    result['identities'].append(identity(span))
                    result['received'].append({'queue': queue.name, 'body': body, 'headers': message.headers})
                    message.ack()
                with Consumer(channel, queues=[queue], callbacks=[received], accept=['json']):
                    connection.drain_events(timeout=5)
            channel.close()
        peer.shutdown()
        thread.join(timeout=5)
        assert not peer.errors, peer.errors
        result.update(amqpFrames=peer.frames, publications=peer.publications, deliveries=peer.deliveries,
                      exchanges=peer.exchanges, bindings=peer.bindings, remaining={q: len(m) for q,m in peer.queues.items()})
    tracer.shutdown()
    Path(args.identity_file).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    parser.add_argument('--identity-file', required=True)
    parser.add_argument('--peer')
    run(parser.parse_args())
