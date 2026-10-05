"""A named CPU workload so the real profiler can prove sampled application work."""
import json
import time

from ddtrace import tracer


def profile_work():
    value = 0
    for index in range(10000):
        value = (value + index * index) % 1000000007
    return value


with tracer.trace("profiling.request") as span:
    identity = {"trace_id": str(span.trace_id), "span_id": str(span.span_id)}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        profile_work()
tracer.shutdown()
print("RESULT " + json.dumps(identity), flush=True)
