"""Narrow, executed SDK differences revalidated against ddtrace 4.15.5."""
from pathlib import Path
import traceback

SDK_VERSION = "4.15.5"
EXPECTED_FAILURES = {
    "test_partial_flushing.Test_Partial_Flushing.test_partial_flushing_propagation_tags[0]": (
        "test_partial_flushing.py", "do_propagation_tags_test", 102, "AssertionError", "sampling",
        "Legacy DD_TRACE_SAMPLE_RATE=1 yields automatic keep priority 1 instead of user keep 2."),
    "test_tracer.Test_TracerServiceNameSource.test_tracer_no_srv_src_when_service_not_manually_set[0]": (
        "test_tracer.py", "test_tracer_no_srv_src_when_service_not_manually_set", 234, "AssertionError", "service-source",
        "The automatically detected default service carries _dd.svc_src=datadog."),
    "test_span_events.Test_Span_Events.test_span_with_event_v05[0]": (
        "adapter.py", "traces", 316, "AssertionError", "event-wire",
        "DD_TRACE_NATIVE_SPAN_EVENTS=1 emits native v0.4 despite the requested v0.5 wire."),
    "test_span_events.Test_Span_Events.test_span_with_invalid_event_attributes[0]": (
        "adapter.py", "wait_for_num_traces", 347, "ValueError", "event-overflow",
        "Event attributes containing integers outside uint64 drop the native payload."),
    "local_cases.Test_Lab_Http.test_query_redaction_empty[0]": (
        "local_cases.py", "test_query_redaction_empty", 76, "AssertionError", "empty-query",
        "An empty obfuscation regex preserves the server query but removes the aiohttp client query."),
    "local_cases.Test_Lab_Http.test_independent_client_status_ranges[0]": (
        "local_cases.py", "test_independent_client_status_ranges", 59, "AssertionError", "client-range",
        "The aiohttp client uses the server error range; status 200 is erroneous on both spans."),
}


SOURCE_SHA256 = {
    'local_cases.Test_Lab_Http.test_independent_client_status_ranges[0]': '5bec38863513511038e84e60647544dd6deda7a960dc7be9d8628f5208e6fb51',
    'local_cases.Test_Lab_Http.test_query_redaction_empty[0]': '5bec38863513511038e84e60647544dd6deda7a960dc7be9d8628f5208e6fb51',
    'test_partial_flushing.Test_Partial_Flushing.test_partial_flushing_propagation_tags[0]': '2c8a445acdc133f41a5323f6ed7ec9f955661f2233c253c30215100658747bd2',
    'test_span_events.Test_Span_Events.test_span_with_invalid_event_attributes[0]': '04c623e842d40c5bd8ac73201515a7e2abcaf0ea957cae829920400cefd91f7d',
    'test_tracer.Test_TracerServiceNameSource.test_tracer_no_srv_src_when_service_not_manually_set[0]': '12dddb4972865a8a3a2e6dc9457dc40c640eeaaa529e5af9a4af98ef62feebb3',
    'test_span_events.Test_Span_Events.test_span_with_event_v05[0]': '04c623e842d40c5bd8ac73201515a7e2abcaf0ea957cae829920400cefd91f7d',
}


def failure_signature(error):
    frame = traceback.extract_tb(error.__traceback__)[-1]
    return {"type": type(error).__name__, "file": Path(frame.filename).name,
            "function": frame.name, "line": frame.lineno, "message": str(error)}


def matches_expected_failure(name, sdk_version, wire, failure, captures, operations, source_sha256):
    spec = EXPECTED_FAILURES.get(name)
    if spec is None or sdk_version != SDK_VERSION or not operations or source_sha256 != SOURCE_SHA256.get(name):
        return False
    filename, function, line, error_type, kind, _ = spec
    if (failure.get("file"), failure.get("function"), failure.get("line"), failure.get("type")) != (
            filename, function, line, error_type):
        return False
    records = [record for snapshot in captures for record in snapshot]
    spans = {int(span["span_id"]): span for record in records
             for chunk in record["payload"]["traces"] for span in chunk}
    if kind == "sampling":
        return bool(records) and all(record["payload"]["wire_version"] == wire for record in records) and any(
            span["name"] == "child1" and span.get("metrics", {}).get("_sampling_priority_v1") == 1
            for span in spans.values())
    if kind == "service-source":
        starts = [op["result"] for op in operations if op["operation"] == "start"]
        return len(starts) == 1 and spans.get(starts[0]["span_id"], {}).get("meta", {}).get("_dd.svc_src") == "datadog"
    if kind == "event-wire":
        starts = [op["result"] for op in operations if op["operation"] == "otel_start"]
        span = spans.get(starts[0]["span_id"], {}) if len(starts) == 1 else {}
        return wire == "v0.5" and bool(records) and all(
            record["payload"]["wire_version"] == "v0.4" for record in records) and [
                event["name"] for event in span.get("span_events", [])] == ["event_name", "other_event"]
    if kind == "event-overflow":
        events = [op["arguments"] for op in operations if op["operation"] == "otel_event"]
        return wire == "v0.4" and failure.get("message") == "1 traces not available from test agent, got 0: []" and (
            len(captures) == 40 and all(snapshot == [] for snapshot in captures)) and [
                op["operation"] for op in operations] == ["otel_start", "otel_event", "otel_end", "flush"] and (
            len(events) == 1 and events[0]["attributes"] == {
                "string": "bar", "int": 1, "invalid_int1": 2**66, "invalid_int2": -(2**66),
                "invalid_arr1": [1, "a"], "invalid_arr2": [[1]]})
    requests = [op for op in operations if op["operation"] == "http_request"]
    if len(requests) != 1 or requests[0]["arguments"].get("status") != 200:
        return False
    receipt = requests[0]["result"]
    server = spans.get(receipt["target"]["span_id"], {})
    clients = [span for span in spans.values() if span.get("meta", {}).get("component") == "aiohttp_client"
               and span.get("parent_id") == receipt["control"]["span_id"]]
    if len(clients) != 1:
        return False
    client = clients[0]
    if kind == "client-range":
        return server.get("error", 0) == client.get("error", 0) == 1
    return requests[0]["arguments"].get("query") == {"token": "secret-value"} and (
        server.get("meta", {}).get("http.url", "").endswith("?token=secret-value")) and (
        client.get("meta", {}).get("http.url") == receipt["url"])
