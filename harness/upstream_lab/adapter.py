"""Fixture routing for unchanged upstream tests; all observations come from SDK/intake."""
import ast
from contextlib import contextmanager
import inspect
from enum import IntEnum
from urllib.parse import urlparse
import re
import itertools
import json
import logging
from pathlib import Path
import random
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen


class Markers:
    """Retain feature and parametrization metadata without importing upstream infrastructure."""
    def __init__(self, kind):
        self.kind = kind

    def __getattr__(self, name):
        if name == "parametrize":
            return self.parametrize
        def decorate(target):
            values = list(target.__dict__.get("_upstream_" + self.kind, []))
            values.append(name)
            setattr(target, "_upstream_" + self.kind, values)
            return target
        return decorate

    def parametrize(self, names, values, **kwargs):
        def decorate(target):
            parameters = list(target.__dict__.get("_upstream_parameters", []))
            parameters.append(([item.strip() for item in names.split(",")] if isinstance(names, str) else list(names), list(values)))
            target._upstream_parameters = parameters
            return target
        return decorate


class ExceptionInfo:
    def __init__(self):
        self.value = None

    def match(self, pattern):
        assert self.value is not None
        assert re.search(pattern, str(self.value)), (pattern, self.value)
        return True


@contextmanager
def raises(exception):
    info = ExceptionInfo()
    try:
        yield info
    except exception as error:
        info.value = error
        return
    raise AssertionError("Expected " + exception.__name__)


class SpanKind(IntEnum):
    INTERNAL = 0
    SERVER = 1
    CLIENT = 2
    PRODUCER = 3
    CONSUMER = 4


class StatusCode(IntEnum):
    UNSET = 0
    OK = 1
    ERROR = 2


def fixture_namespace(vendor):
    namespace = {
        "pytest": SimpleNamespace(mark=Markers("marks"), MarkDecorator=object, raises=raises,
            param=lambda *args, **kwargs: args if len(args) > 1 else args[0],
            fixture=lambda **kwargs: lambda target: target),
        "features": Markers("features"), "scenarios": Markers("scenarios"),
        "rfc": lambda *args: lambda target: target,
        "logger": logging.getLogger("upstream"), "json": json, "time": time, "random": random,
        "TestAgentAPI": object, "APMLibrary": object, "Span": dict, "Trace": list,
        "context": SimpleNamespace(library="python"), "SpanKind": SpanKind, "StatusCode": StatusCode,
        "Link": dict, "urlparse": urlparse, "re": re,
        "is_same_boolean": lambda actual, expected: str(actual).lower() == str(expected).lower(),
        "scenario_crash": lambda target: target,
    }
    # Use original upstream assertion/parser helpers. Heavy unrelated stats decoder
    # dependencies are deliberately excluded; no SDK or intake data is synthesized.
    tree = ast.parse((vendor / "trace.py").read_text())
    selected = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef))
                and not (isinstance(node, ast.FunctionDef) and node.name.startswith(("_v06", "decode_v06")))]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(vendor / "trace.py"), "exec"), namespace)
    exec(compile((vendor / "tracecontext.py").read_text(), str(vendor / "tracecontext.py"), "exec"), namespace)
    exec(compile((vendor / "dd_constants.py").read_text(), str(vendor / "dd_constants.py"), "exec"), namespace)
    namespace.update(SpanKind=SpanKind, StatusCode=StatusCode)
    return namespace


def load_cases(vendor):
    namespace = fixture_namespace(vendor)
    selection = json.loads((vendor / "manifest.json").read_text()).get("sourceSelection", {})
    cases = []
    for path in sorted(vendor.rglob("test_*.py")):
        module = dict(namespace)
        # Upstream uses Python 3.12 typing aliases; the harness also runs on
        # Python 3.11. Lower only those top-level declarations, leaving every
        # test method body unchanged and the vendored source hash intact.
        source = re.sub(r"^type (\w+) =", r"\1 =", path.read_text(), flags=re.MULTILINE)
        tree = ast.parse(source)
        # Imports only route to fixture infrastructure. Execute every other source
        # node, including the complete unchanged upstream methods and their assertions.
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        policy = selection.get(str(path.relative_to(vendor)), {})
        if "topLevelNodesBeforeLine" in policy:
            tree.body = [node for node in tree.body if node.lineno < policy["topLevelNodesBeforeLine"]]
        exec(compile(tree, str(path), "exec"), module)
        for clsname, cls in module.items():
            if not clsname.startswith("Test_") or not inspect.isclass(cls):
                continue
            for method_name, method in cls.__dict__.items():
                if not method_name.startswith("test_") or not inspect.isfunction(method):
                    continue
                module_marks = module.get("pytestmark")
                if module_marks and not isinstance(module_marks, (tuple, list)):
                    metadata = module_marks(SimpleNamespace())
                    global_parameters = metadata._upstream_parameters
                else:
                    global_parameters = []
                parameters = global_parameters + cls.__dict__.get("_upstream_parameters", []) + getattr(method, "_upstream_parameters", [])
                combinations = itertools.product(*(values for _, values in parameters)) if parameters else [()]
                for index, combination in enumerate(combinations):
                    values = {}
                    for (names, _), row in zip(parameters, combination):
                        values.update(zip(names, [row] if len(names) == 1 else row))
                    cases.append({
                        "name": path.stem + "." + clsname + "." + method_name + "[" + str(index) + "]",
                        "file": str(path.relative_to(vendor)), "method": method_name, "class": clsname,
                        "features": cls.__dict__.get("_upstream_features", []) + getattr(method, "_upstream_features", []),
                        "parameters": values, "instance": cls(), "function": method,
                    })
    return cases


class Library:
    def __init__(self, url):
        self.url = url
        self.operations = []
        self.lang = "python"

    def rpc(self, operation, **values):
        body = json.dumps(dict(operation=operation, **values)).encode()
        with urlopen(Request(self.url + "/sdk", data=body, headers={"Content-Type": "application/json"}), timeout=10) as response:
            result = json.load(response)
        assert "error" not in result, result
        self.operations.append({"operation": operation, "arguments": values, "result": result["result"]})
        return result["result"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.dd_flush()

    @contextmanager
    def dd_start_span(self, name, service=None, resource=None, parent_id=None, typestr=None, tags=None):
        identity = self.rpc("start", name=name, service=service, resource=resource,
                            parent_id=parent_id, span_type=typestr, tags=tags)
        span = SpanClient(self, **identity)
        try:
            yield span
        finally:
            span.finish()

    def config(self):
        return self.rpc("config")

    def get_logs(self):
        return self.log_path.read_text(errors="replace")

    @contextmanager
    def otel_start_span(self, name, parent_id=None, span_kind=None, timestamp=None,
                        attributes=None, links=None, events=None, end_on_exit=True):
        identity = self.rpc("otel_start", name=name, parent_id=parent_id, span_kind=span_kind,
                            timestamp=timestamp, attributes=attributes or {}, links=links or [], events=events or [])
        span = OtelSpanClient(self, **identity)
        try:
            yield span
        finally:
            if end_on_exit:
                span.end_span()

    def dd_current_span(self):
        identity = self.rpc("dd_current")
        return SpanClient(self, **identity) if identity else None

    def otel_current_span(self):
        identity = self.rpc("otel_current")
        return OtelSpanClient(self, **identity) if identity else None

    def otel_flush(self, seconds):
        return self.rpc("flush")

    def dd_extract_headers(self, http_headers):
        return self.rpc("extract", headers=list(http_headers))

    def dd_extract_headers_and_make_child_span(self, name, http_headers):
        return self.dd_start_span(name, parent_id=self.dd_extract_headers(http_headers))

    def dd_make_child_span_and_get_headers(self, headers):
        with self.dd_extract_headers_and_make_child_span("name", headers) as span:
            return dict(self.dd_inject_headers(span.span_id))

    def dd_inject_headers(self, span_id):
        return list(self.rpc("inject", span_id=span_id).items())

    def dd_flush(self):
        return self.rpc("flush")


class SpanClient:
    def __init__(self, library, span_id, trace_id):
        self.library = library
        self.span_id = span_id
        self.trace_id = trace_id
        self.finished = False

    def get_span_id(self):
        return self.span_id

    def get_trace_id(self):
        return self.trace_id

    def finish(self):
        if not self.finished:
            self.library.rpc("finish", span_id=self.span_id)
            self.finished = True

    def set_resource(self, resource):
        return self.library.rpc("set_resource", span_id=self.span_id, resource=resource)

    def manual_keep(self):
        self.set_meta("manual.keep", None)

    def __getattr__(self, operation):
        fields = {
            "set_meta": ["key", "value"], "set_metric": ["key", "value"],
            "set_baggage": ["key", "value"], "get_baggage": ["key"],
            "get_all_baggage": [], "remove_baggage": ["key"], "remove_all_baggage": [],
            "add_link": ["parent_id", "attributes"],
        }
        if operation not in fields:
            raise AttributeError(operation)
        def invoke(*args, **kwargs):
            values = dict(zip(fields[operation], args))
            values.update(kwargs)
            return self.library.rpc(operation, span_id=self.span_id, **values)
        return invoke


class OtelSpanClient(SpanClient):
    def end_span(self, timestamp=None):
        # Repeated end calls deliberately reach the SDK, to exercise no-op behavior.
        self.library.rpc("otel_end", span_id=self.span_id, timestamp=timestamp)

    def set_attributes(self, attributes):
        return self.library.rpc("otel_attributes", span_id=self.span_id, attributes=attributes)

    def set_attribute(self, key, value):
        return self.set_attributes({key: value})

    def set_status(self, status, description=""):
        return self.library.rpc("otel_status", span_id=self.span_id, status=status, description=description)

    def set_name(self, name):
        return self.library.rpc("otel_name", span_id=self.span_id, name=name)

    def span_context(self):
        return self.library.rpc("otel_context", span_id=self.span_id)

    def is_recording(self):
        return self.library.rpc("otel_recording", span_id=self.span_id)

    def add_event(self, name, timestamp=None, attributes=None, time_unix_nano=None):
        return self.library.rpc("otel_event", span_id=self.span_id, name=name,
            timestamp=timestamp if timestamp is not None else time_unix_nano, attributes=attributes or {})

    def record_exception(self, message, attributes=None):
        return self.library.rpc("otel_exception", span_id=self.span_id, message=message, attributes=attributes or {})


class AgentIntake:
    def __init__(self, sink, wire):
        self.sink, self.wire = sink, wire
        self.captures = []

    def request(self, path, method="GET"):
        with urlopen(Request(self.sink + path, method=method), timeout=5) as response:
            return response.read()

    def clear(self):
        self.request("/reset?protocol=datadog", "POST")

    def traces(self, sort_by_start=True):
        raw = self.request("/dump?protocol=datadog")
        records = json.loads(raw)
        self.captures.append(records)
        traces = []
        for record in records:
            payload = record["payload"]
            assert payload["wire_version"] == self.wire, payload
            for trace in payload["traces"]:
                assert trace, trace
                # The test agent defaults missing span maps to empty maps.
                for span in trace:
                    span.setdefault("meta", {})
                    span.setdefault("metrics", {})
                traces.append(sorted(trace, key=lambda span: span["start"]) if sort_by_start else trace)
        # Upstream's trace API exposes complete logical traces. The native sink
        # retains each intake chunk, so group these actual chunks by trace ID for
        # assertions that start a child after its parent already ended.
        logical = {}
        for chunk in traces:
            low = int(chunk[0]["trace_id"])
            high = next((span["meta"]["_dd.p.tid"] for span in chunk if "_dd.p.tid" in span["meta"]), "0000000000000000")
            logical.setdefault((high, low), []).extend(chunk)
        traces = list(logical.values())
        if sort_by_start:
            traces = [sorted(trace, key=lambda span: span["start"]) for trace in traces]
            traces.sort(key=lambda trace: trace[0]["start"])
        return traces

    def wait_for_num_traces(self, count=None, clear=False, sort_by_start=True, wait_loops=40, num=None):
        count = num if count is None else count
        for _ in range(wait_loops):
            traces = self.traces(sort_by_start)
            if len(traces) == count:
                if clear:
                    self.clear()
                return traces
            time.sleep(0.05)
        raise ValueError(f"{count} traces not available from test agent, got {len(traces)}: {traces}")

    def wait_for_num_spans(self, count=None, clear=False, sort_by_start=True, wait_loops=40, num=None):
        count = num if count is None else count
        for _ in range(wait_loops):
            traces = self.traces(sort_by_start)
            if sum(map(len, traces)) >= count:
                if clear:
                    self.clear()
                return traces
            time.sleep(0.05)
        raise ValueError(f"Expected {count} spans; got {traces}")
