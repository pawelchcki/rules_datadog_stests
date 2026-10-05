"""Loopback-only CONNECT relay for the Agent's synthetic EVP destination."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import select
import socket
import threading


class ConnectRelay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, backend_port):
        super().__init__(("127.0.0.1", 0), Handler)
        self.backend_port = backend_port
        self.records = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        with self.server.lock:
            self.server.records.append(self.path)
        if self.path not in {"llmobs-intake.backend.test:443", "127.0.0.1:" + str(self.server.backend_port)}:
            self.send_error(403, "only the synthetic LLMObs intake is allowed")
            return
        with socket.create_connection(("127.0.0.1", self.server.backend_port), timeout=10) as upstream:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            peers = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(peers, [], [], 20)
                if not ready:
                    return
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(chunk)

    def log_message(self, *_args):
        pass
