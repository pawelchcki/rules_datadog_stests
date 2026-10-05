"""Serve a real graphql-core schema over stdlib HTTP for a fixed request budget.

Mirrors the upstream system-tests weblog /graphql endpoint just enough to drive
tests/test_graphql.py: the withError resolver raises GraphQLError carrying the
typed extensions the test asserts, and a healthy query covers the success path.
The process exits cleanly after --requests POST /graphql calls so the probe can
run it one-shot through the app launcher.
"""
import argparse
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

parser = argparse.ArgumentParser()
for name in ("ready-file", "identity-file", "sdk-overlay"):
    parser.add_argument("--" + name, required=True)
parser.add_argument("--requests", type=int, default=4)
args = parser.parse_args()
sys.path.insert(0, args.sdk_overlay)

import ddtrace
import graphql
from graphql import GraphQLError
from graphql import GraphQLField
from graphql import GraphQLObjectType
from graphql import GraphQLSchema
from graphql import GraphQLString
from graphql import graphql_sync

ddtrace.patch(graphql=True)

# Same extension values as utils/build/docker/python/flask/integrations/graphql.py;
# DD_TRACE_GRAPHQL_ERROR_EXTENSIONS selects all keys except not_captured.
ERROR_EXTENSIONS = {"int": 1, "float": 1.1, "str": "1", "bool": True, "other": [1, "foo"], "not_captured": "foo"}


def resolve_with_error(_root, _info):
    raise GraphQLError(message="test error", extensions=dict(ERROR_EXTENSIONS))


def resolve_hello(_root, _info):
    return "Hello world"


schema = GraphQLSchema(query=GraphQLObjectType("Query", {
    "withError": GraphQLField(GraphQLString, resolve=resolve_with_error),
    "hello": GraphQLField(GraphQLString, resolve=resolve_hello),
}))

records = []
remaining = {"count": args.requests}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/graphql":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        document = json.loads(self.rfile.read(length))
        result = graphql_sync(schema, document["query"], operation_name=document.get("operationName"))
        # Real GraphQL-over-HTTP semantics: resolver errors still answer 200 with
        # the errors list, matching what upstream's weblog returns.
        self._send(200, result.formatted)
        records.append({"query": document["query"], "operation_name": document.get("operationName"),
                        "data": result.data, "errors": [str(error) for error in (result.errors or [])]})
        remaining["count"] -= 1
        if remaining["count"] <= 0:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
Path(args.ready_file).write_text(str(server.server_address[1]))
server.serve_forever()
server.server_close()
ddtrace.tracer.flush()
identity = {"client_version": graphql.version, "graphql_version": graphql.version, "tracer_version": ddtrace.__version__,
            "requests": records}
Path(args.identity_file).write_text(json.dumps(identity, indent=2) + "\n")
