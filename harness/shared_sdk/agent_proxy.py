"""Negotiate span-event support while forwarding native intake bytes unchanged."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@contextmanager
def native_events_agent(sink, info_file):
    # The native sink decodes span_events in v0.4. Go discovers that support
    # through /info; Python's pinned cases also set their native-events option.
    info = json.load(urlopen(sink + '/info', timeout=5))
    info['span_events'] = True
    info_file.write_text(json.dumps(info, sort_keys=True) + '\n')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            if self.path == '/info':
                body = json.dumps(info).encode()
                status, content_type = 200, 'application/json'
            else:
                size = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(size) if self.command == 'POST' else None
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in ('host', 'connection', 'content-length')}
                request = Request(sink + self.path, data=body, headers=headers, method=self.command)
                try:
                    response = urlopen(request, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    status = response.status
                    content_type = response.headers.get('Content-Type', 'application/json')
                    body = response.read()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
