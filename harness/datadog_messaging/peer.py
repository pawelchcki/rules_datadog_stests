"""Loopback AMQP 0-9-1 peer with independently controlled exchange routing.

Only the methods exercised by this lab are supported. This is a protocol fixture,
not a claim of RabbitMQ server conformance.
"""
import base64
import hashlib
import socketserver
import struct
import threading

from amqp.basic_message import Message
from amqp.serialization import dumps, loads


def topic_match(pattern, key):
    pattern, key = pattern.split('.'), key.split('.')
    def match(p, k):
        if not p:
            return not k
        if p[0] == '#':
            return any(match(p[1:], k[i:]) for i in range(len(k) + 1))
        return bool(k) and (p[0] == '*' or p[0] == k[0]) and match(p[1:], k[1:])
    return match(pattern, key)


class Peer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.frames, self.publications, self.deliveries = [], [], []
        self.exchanges, self.queues, self.bindings = {}, {}, []
        self.lock = threading.Lock()
        self.errors = []

    def record(self, direction, raw):
        self.frames.append({'direction': direction, 'body': base64.b64encode(raw).decode(),
                            'sha256': hashlib.sha256(raw).hexdigest()})


class Handler(socketserver.BaseRequestHandler):
    def read(self, size):
        result = b''
        while len(result) < size:
            chunk = self.request.recv(size - len(result))
            if not chunk:
                raise EOFError
            result += chunk
        return result

    def frame(self, kind, channel, body):
        raw = struct.pack('>BHI', kind, channel, len(body)) + body + b'\xce'
        self.server.record('out', raw)
        self.request.sendall(raw)

    def method(self, channel, signature, fmt='', values=()):
        self.frame(1, channel, struct.pack('>HH', *signature) + dumps(fmt, values))

    def handle(self):
        try:
            self.run()
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as error:
            self.server.errors.append(repr(error))

    def run(self):
        protocol = self.read(8)
        assert protocol == b'AMQP\x00\x00\x09\x01', protocol
        self.server.record('in', protocol)
        self.method(0, (10, 10), 'ooFSS', [0, 9, {'product': 'controlled-amqp-peer'}, 'PLAIN', 'en_US'])
        pending = {}
        counter = 0
        while True:
            header = self.read(7)
            kind, channel, size = struct.unpack('>BHI', header)
            body, end = self.read(size), self.read(1)
            assert end == b'\xce'
            self.server.record('in', header + body + end)
            if kind == 8:
                continue
            if kind == 2:
                pending[channel]['message'].inbound_header(body)
                continue
            if kind == 3:
                p = pending[channel]
                p['message'].inbound_body(body)
                if not p['message'].ready:
                    continue
                publication = {'exchange': p['exchange'], 'routing_key': p['key'],
                    'headers': p['message'].properties.get('application_headers', {}),
                    'body': p['message'].body.decode(), 'queues': []}
                with self.server.lock:
                    for queue, exchange, pattern in self.server.bindings:
                        typ = self.server.exchanges[exchange]
                        if exchange == p['exchange'] and (typ == 'fanout' or
                            typ == 'direct' and pattern == p['key'] or
                            typ == 'topic' and topic_match(pattern, p['key'])):
                            self.server.queues[queue].append(p)
                            publication['queues'].append(queue)
                    self.server.publications.append(publication)
                del pending[channel]
                continue
            assert kind == 1, kind
            sig = struct.unpack('>HH', body[:4])
            def args(fmt):
                return loads(fmt, body, 4)[0]
            if sig == (10, 11):
                self.method(0, (10, 30), 'BlB', [65535, 131072, 0])
            elif sig == (10, 31):
                pass
            elif sig == (10, 40):
                self.method(0, (10, 41), 's', [''])
            elif sig == (20, 10):
                self.method(channel, (20, 11), 'S', [''])
            elif sig == (40, 10):
                _, exchange, typ, *_ = args('BssbbbbbF')
                self.server.exchanges[exchange] = typ
                self.method(channel, (40, 11))
            elif sig == (50, 10):
                _, queue, *_ = args('BsbbbbbF')
                self.server.queues.setdefault(queue, [])
                self.method(channel, (50, 11), 'sll', [queue, len(self.server.queues[queue]), 0])
            elif sig == (50, 20):
                _, queue, exchange, pattern, *_ = args('BsssbF')
                binding = (queue, exchange, pattern)
                if binding not in self.server.bindings:
                    self.server.bindings.append(binding)
                self.method(channel, (50, 21))
            elif sig == (60, 10):
                self.method(channel, (60, 11))
            elif sig == (60, 40):
                _, exchange, key, *_ = args('Bssbb')
                pending[channel] = {'exchange': exchange, 'key': key, 'message': Message()}
            elif sig == (60, 20):
                _, queue, tag, *_ = args('BssbbbbF')
                self.method(channel, (60, 21), 's', [tag])
                with self.server.lock:
                    messages = list(self.server.queues[queue])
                    self.server.queues[queue].clear()
                for p in messages:
                    counter += 1
                    self.method(channel, (60, 60), 'sLbss', [tag, counter, False, p['exchange'], p['key']])
                    message = p['message']
                    self.frame(2, channel, struct.pack('>HHQ', 60, 0, len(message.body)) + message._serialize_properties())
                    self.frame(3, channel, message.body)
                    self.server.deliveries.append({'queue': queue, 'headers': message.properties.get('application_headers', {})})
            elif sig == (60, 30):
                self.method(channel, (60, 31), 's', [args('sb')[0]])
            elif sig == (60, 80):
                pass
            elif sig == (20, 40):
                self.method(channel, (20, 41))
            elif sig == (10, 50):
                self.method(0, (10, 51))
                return
            else:
                raise AssertionError(('Unsupported AMQP method', sig))
