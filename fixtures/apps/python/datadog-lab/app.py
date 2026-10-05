"""Controlled ddtrace SDK workload for native propagation and sampling checks."""
import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ddtrace import tracer, config, __version__ as DDTRACE_VERSION
from ddtrace.propagation.http import HTTPPropagator

# SDK handles are kept across HTTP calls, as in upstream's parametric client.
SPANS = {}
CONTEXTS = {}
OTEL_SPANS = {}
ACTIVE = {"dd": None, "otel": None}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/sdk":
            self.send_error(404)
            return
        try:
            args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            op = args.pop("operation")
            if op == "mixed_context":
                import opentelemetry.trace as otel
                from ddtrace.opentelemetry import TracerProvider
                otel.set_tracer_provider(TracerProvider())
                otel_tracer = otel.get_tracer(__name__)
                def identity(span):
                    context = span.get_span_context() if hasattr(span, "get_span_context") else span
                    return {"span_id": context.span_id, "trace_id": context.trace_id}
                result = {}
                with tracer.trace("mixed.dd.root") as dd_root:
                    result["dd_root"] = identity(dd_root)
                    with otel_tracer.start_as_current_span("mixed.otel.child") as otel_child:
                        result["otel_child"] = identity(otel_child)
                        result["dd_current_while_otel"] = tracer.current_span().span_id
                with otel_tracer.start_as_current_span("mixed.otel.root") as otel_root:
                    result["otel_root"] = identity(otel_root)
                    with tracer.trace("mixed.dd.child") as dd_child:
                        result["dd_child"] = identity(dd_child)
                        result["otel_current_while_dd"] = otel.get_current_span().get_span_context().span_id
                tracer.flush()
            elif op == "config":
                rate = 1.0
                for rule in tracer._sampler.rules:
                    if rule.service is None and rule.name is None and rule.resource is None and not rule.tags and rule.provenance in ("default", None):
                        rate = rule.sample_rate
                        break
                result = {
                    "dd_service": config.service, "dd_tags": ",".join(f"{key}:{value}" for key, value in config.tags.items()),
                    "dd_trace_sample_rate": str(rate), "dd_trace_rate_limit": str(config._trace_rate_limit),
                    "dd_trace_otel_enabled": str(config._otel_enabled).lower(),
                    "dd_trace_enabled": str(config._tracing_enabled).lower(),
                    "dd_env": config.env, "dd_version": config.version,
                }
            elif op == "otel_start":
                import opentelemetry.trace as otel
                from ddtrace.opentelemetry import TracerProvider
                from opentelemetry.trace import set_span_in_context, SpanKind, Link
                if not OTEL_SPANS:
                    otel.set_tracer_provider(TracerProvider())
                parent_id = args["parent_id"]
                parent = OTEL_SPANS.get(parent_id)
                links = [Link(OTEL_SPANS[link["parent_id"]].get_span_context(), link.get("attributes")) for link in args["links"]]
                with otel.get_tracer(__name__).start_as_current_span(
                    args["name"], context=set_span_in_context(parent), kind=SpanKind(args["span_kind"] or 0),
                    attributes=args["attributes"], links=links, end_on_exit=False,
                    start_time=int(args["timestamp"] * 1000) if args["timestamp"] is not None else None,
                ) as span:
                    ACTIVE["otel"] = otel.get_current_span()
                    ACTIVE["dd"] = tracer.current_span()
                    for event in args["events"]:
                        span.add_event(event["name"], event.get("attributes"), event.get("timestamp"))
                context = span.get_span_context()
                OTEL_SPANS[context.span_id] = span
                SPANS[context.span_id] = span._ddspan
                result = {"span_id": context.span_id, "trace_id": context.trace_id}
            elif op in ("dd_current", "otel_current"):
                span = ACTIVE["dd" if op == "dd_current" else "otel"]
                if span is None:
                    result = None
                elif op == "dd_current":
                    result = {"span_id": span.span_id, "trace_id": span.trace_id}
                else:
                    context = span.get_span_context()
                    result = {"span_id": context.span_id, "trace_id": context.trace_id}
            elif op.startswith("otel_"):
                from opentelemetry.trace import StatusCode
                span = OTEL_SPANS[args.pop("span_id")]
                result = None
                if op == "otel_end":
                    ACTIVE["dd"] = span._ddspan._parent
                    ACTIVE["otel"] = OTEL_SPANS.get(ACTIVE["dd"].span_id) if ACTIVE["dd"] else None
                    span.end(int(args["timestamp"] * 1000) if args["timestamp"] is not None else None)
                elif op == "otel_attributes":
                    span.set_attributes(args["attributes"])
                elif op == "otel_status":
                    span.set_status(StatusCode(args["status"]), args["description"])
                elif op == "otel_name":
                    span.update_name(args["name"])
                elif op == "otel_recording":
                    result = span.is_recording()
                elif op == "otel_context":
                    context = span.get_span_context()
                    result = {"span_id": context.span_id, "trace_id": f"{context.trace_id:032x}",
                        "trace_flags": f"{context.trace_flags:02x}", "trace_state": context.trace_state.to_header(), "remote": context.is_remote}
                elif op == "otel_event":
                    timestamp = args["timestamp"]
                    span.add_event(args["name"], args["attributes"], timestamp)
                elif op == "otel_exception":
                    span.record_exception(Exception(args["message"]), args["attributes"])
                else:
                    raise ValueError("Unsupported OpenTelemetry SDK operation: " + op)
            elif op == "extract":
                context = HTTPPropagator.extract(dict(args["headers"]))
                CONTEXTS[context.span_id] = context
                result = context.span_id
            elif op == "start":
                parent_id = args.pop("parent_id", None)
                parent = SPANS.get(parent_id, CONTEXTS.get(parent_id))
                tags = args.pop("tags", []) or []
                span = tracer.start_span(child_of=parent, activate=True, **args)
                for key, value in tags:
                    span.set_tag(key, value)
                SPANS[span.span_id] = span
                ACTIVE["dd"] = tracer.current_span()
                result = {"span_id": span.span_id, "trace_id": span.trace_id}
            elif op == "reset":
                tracer.flush()
                SPANS.clear()
                OTEL_SPANS.clear()
                CONTEXTS.clear()
                ACTIVE.update(dd=None, otel=None)
                result = None
            elif op == "flush":
                tracer.flush()
                result = True
            else:
                span = SPANS[args.pop("span_id")]
                if op == "finish":
                    span.finish()
                    ACTIVE["dd"] = span._parent
                    result = None
                elif op == "inject":
                    result = {}
                    # Pass the actual span so ddtrace 4.x samples before injection.
                    HTTPPropagator.inject(span, result)
                elif op == "set_resource":
                    span.resource = args["resource"]
                    result = None
                elif op == "set_error":
                    span.error = 1
                    for key, value in (("error.type", args["error_type"]), ("error.message", args["message"]), ("error.stack", args["stack"])):
                        span.set_tag(key, value)
                    result = None
                elif op == "set_meta":
                    span.set_tag(args["key"], args.get("value"))
                    result = None
                elif op == "set_metric":
                    span.set_metric(args["key"], args["value"])
                    result = None
                elif op == "set_baggage":
                    span.context.set_baggage_item(args["key"], args["value"])
                    result = None
                elif op == "get_baggage":
                    result = span.context.get_baggage_item(args["key"])
                elif op == "get_all_baggage":
                    result = span.context.get_all_baggage_items()
                elif op == "remove_baggage":
                    span.context.remove_baggage_item(args["key"])
                    result = None
                elif op == "remove_all_baggage":
                    span.context.remove_all_baggage_items()
                    result = None
                elif op == "add_link":
                    parent_id = args["parent_id"]
                    linked = SPANS.get(parent_id, CONTEXTS.get(parent_id))
                    span.link_span(linked.context if hasattr(linked, "context") else linked,
                                   attributes=args.get("attributes"))
                    result = None
                else:
                    raise ValueError("Unsupported SDK operation: " + op)
            self.send_json({"result": result})
        except Exception as error:
            self.send_json({"error": repr(error)}, status=500)

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/healthz":
            self.send_json({"ready": True, "ddtraceVersion": DDTRACE_VERSION})
            return
        if route.path == "/shutdown":
            self.send_json({"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if route.path != "/run":
            self.send_error(404)
            return
        query = parse_qs(route.query)
        service = query.get("service", ["lab-service"])[0]
        name = query.get("name", ["lab.request"])[0]
        with tracer.trace(name, service=service) as root:
            with tracer.trace("lab.child", service="lab-child") as child:
                child_id = child.span_id
                child_parent_id = child.parent_id
            carrier = {}
            HTTPPropagator.inject(root.context, carrier)
            result = {
                "trace_id": str(root.trace_id),
                "root_id": str(root.span_id),
                "root_parent_id": str(root.parent_id or 0),
                "child_id": str(child_id),
                "child_parent_id": str(child_parent_id),
                "service": service,
                "name": name,
                "carrier": carrier,
                "priority": root.context.sampling_priority,
            }
        tracer.flush()
        self.send_json(result)

    def send_json(self, value, status=200):
        body = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def http_application(ready_file):
    """Reuse the existing aiohttp rootfs for actual server/client instrumentation."""
    import socket
    from aiohttp import web, ClientSession
    from ddtrace.contrib.aiohttp import trace_app
    # Register the middleware's integration defaults even when automatic client
    # patching is disabled. Importing the module does not call patch().
    import ddtrace.contrib.internal.aiohttp.patch
    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    def identity(span):
        return {"span_id": span.span_id, "trace_id": span.trace_id} if span else None
    async def health(request):
        return web.json_response({"ready": True, "ddtraceVersion": DDTRACE_VERSION})
    async def target(request):
        return web.json_response({"active": identity(tracer.current_span())},
            status=int(request.match_info["status"]), headers={"X-Lab-Response": "response-value"})
    async def sdk(request):
        args = await request.json()
        operation = args["operation"]
        if operation in ("reset", "flush"):
            tracer.flush()
            return web.json_response({"result": True})
        assert operation == "http_request", operation
        with tracer.trace("http.lab.export-control") as control:
            url = f"http://127.0.0.1:{port}/target/{args['status']}"
            async with ClientSession() as session:
                async with session.get(url, params=args.get("query", {}), headers=args.get("headers", {})) as response:
                    payload = await response.json()
                    result = {"control": identity(control), "target": payload["active"], "status": response.status, "url": url}
        tracer.flush()
        return web.json_response({"result": result})
    async def ready(application):
        Path(ready_file).write_text(str(port))
    app = web.Application()
    trace_app(app, tracer)
    app.router.add_get("/healthz", health)
    app.router.add_get("/target/{status}", target)
    app.router.add_post("/sdk", sdk)
    app.on_startup.append(ready)
    web.run_app(app, host="127.0.0.1", port=port, print=None, access_log=None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    if os.environ.get("DD_LAB_HTTP_MODE") == "true":
        http_application(args.ready_file)
        raise SystemExit
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ready = Path(args.ready_file)
    temporary = ready.with_name(ready.name + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    temporary.replace(ready)
    server.serve_forever()
    tracer.shutdown()
