"""Span-event assertions for tests/test_graphql.py error reporting and tracking.

Each upstream class posts "query myQuery { withError }" and asserts exactly one
graphql.execute span on the request trace carrying one span event with the
class-specific key semantics. This module replays those assertions over both
boundaries: the tracer's native /v0.4/traces msgpack spans and the spans the
real trace-agent delivers to the backend /api/v0.2/traces intake.
"""
import copy
import json

ERROR_QUERY = "query myQuery { withError }"
SUCCESS_QUERY = "query myQuery { hello }"

REPORTING = {"event_name": "dd.graphql.query.error", "message_key": "message", "type_key": "type",
             "stacktrace_key": "stacktrace", "path_key": "path", "locations_key": "locations",
             "extensions_prefix": "extensions"}
TRACKING = {"event_name": "exception", "message_key": "exception.message", "type_key": "exception.type",
            "stacktrace_key": "exception.stacktrace", "path_key": "graphql.error.path",
            "locations_key": "graphql.error.locations", "extensions_prefix": "graphql.error.extensions"}


def _typed_value(value):
    """Decode a native span_events attribute value (BaseGraphQLOperationError._parse_event_value)."""
    kind = value["type"]
    if kind == 0:
        return value["string_value"]
    if kind == 1:
        return value["bool_value"]
    if kind == 2:
        return value["int_value"]
    if kind == 3:
        return value["double_value"]
    if kind == 4:
        return [_typed_value(entry) for entry in value["array_value"]["values"]]
    raise ValueError("unsupported span event attribute type %s for: %s" % (kind, value))


def get_events(span):
    """Mirror BaseGraphQLOperationError._get_events across wire formats."""
    meta = span.get("meta", {})
    if "events" in meta:
        return json.loads(meta["events"])
    events = copy.deepcopy(span.get("span_events", []))
    for event in events:
        event["attributes"] = {key: _typed_value(value) for key, value in event.get("attributes", {}).items()}
    return events


def is_graphql_execute_span(span):
    """Mirror BaseGraphQLOperationError._is_graphql_execute_span for python/graphql.

    COMPONENT_EXCEPTIONS is a defaultdict whose default entry is
    {"operation_name": "graphql.execute", "has_location": True}, so upstream
    matches on the operation name alone; only the root span carries language.
    """
    return span.get("name") == "graphql.execute" and span.get("meta", {}).get("component") == "graphql"


def _split_spans(spans, label):
    execute = [span for span in spans if is_graphql_execute_span(span)]
    error_spans = [span for span in execute if span.get("resource") == ERROR_QUERY]
    success_spans = [span for span in execute if span.get("resource") == SUCCESS_QUERY]
    # Two error requests and two success requests per workload run; upstream
    # asserts exactly one graphql.execute span on the single request trace.
    assert len(error_spans) == 2 and len(success_spans) == 2 and len(execute) == 4, (label, execute)
    return error_spans, success_spans


def _check_span_error_flags(error_spans, success_spans, label):
    for span in error_spans:
        assert span.get("error", 0) == 1, (label, span)
        assert "GraphQLError" in span.get("meta", {}).get("error.type", ""), (label, span)
        assert "test error" in span.get("meta", {}).get("error.message", ""), (label, span)
    for span in success_spans:
        assert span.get("error", 0) == 0, (label, span)
        assert get_events(span) == [], (label, span)


def check_event(span, spec, label):
    """Replay BaseGraphQLOperationError.test_execute_error_span_event on one span."""
    events = get_events(span)
    targets = [event for event in events if event["name"] == spec["event_name"]]
    assert len(targets) == 1, (label, spec["event_name"], events)
    attributes = targets[0]["attributes"]

    assert isinstance(attributes[spec["message_key"]], str), attributes
    assert isinstance(attributes[spec["type_key"]], str), attributes
    assert isinstance(attributes[spec["stacktrace_key"]], str), attributes

    for path in attributes[spec["path_key"]]:
        assert isinstance(path, str), attributes
    location = attributes[spec["locations_key"]]
    assert len(location) == 1, "%s has more than one item: %s" % (spec["locations_key"], attributes)
    for loc in location:
        parts = loc.split(":")
        assert len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit(), attributes

    prefix = spec["extensions_prefix"]
    assert attributes[prefix + ".int"] == 1, attributes
    assert attributes[prefix + ".float"] == 1.1, attributes
    assert attributes[prefix + ".str"] == "1", attributes
    assert attributes[prefix + ".bool"] is True, attributes
    # The heterogeneous [1, "foo"] list is stringified on this wire format.
    assert "1" in attributes[prefix + ".other"] and "foo" in attributes[prefix + ".other"], attributes
    assert prefix + ".not_captured" not in attributes, attributes


def _check_parity(native_spans, backend_spans):
    backend_by_id = {span["span_id"]: span for span in backend_spans}
    native_execute = [span for span in native_spans if is_graphql_execute_span(span)]
    assert len(native_execute) == 4, native_execute
    for native in native_execute:
        backend = backend_by_id.get(native["span_id"])
        assert backend is not None, (native, backend_by_id.keys())
        for key in ("name", "resource"):
            assert backend[key] == native[key], (key, native, backend)
        assert backend.get("error", 0) == native.get("error", 0), (native, backend)
        # Span events must survive the real Agent's intake and re-export unchanged.
        assert get_events(backend) == get_events(native), (native, backend)


def check_case(native_spans, backend_spans, spec, expect_event, label):
    """Assert one workload run; expect_event selects reporting vs tracking semantics."""
    native_error, native_success = _split_spans(native_spans, label + " native")
    backend_error, backend_success = _split_spans(backend_spans, label + " backend")
    _check_span_error_flags(native_error, native_success, label + " native")
    _check_span_error_flags(backend_error, backend_success, label + " backend")
    _check_parity(native_spans, backend_spans)
    if expect_event:
        for span in native_error + backend_error:
            check_event(span, spec, label)
    else:
        # DD_TRACE_GRAPHQL_ERROR_TRACKING=true is ignored by the pinned SDK: it
        # still emits the Datadog-semantics event and never the OTel "exception"
        # event. Assert what the SDK genuinely produces; the absent OTel event is
        # recorded in missingAssertions by the probe instead of being fabricated.
        for span in native_error + backend_error:
            events = get_events(span)
            assert not [event for event in events if event["name"] == TRACKING["event_name"]], events
            targets = [event for event in events if event["name"] == REPORTING["event_name"]]
            assert len(targets) == 1, events
