"""Emit actual stdlib logs and SDK runtime metrics under controlled tracing."""
import argparse
import json
import logging
import time

from ddtrace import tracer
from ddtrace.trace import Context

parser = argparse.ArgumentParser()
parser.add_argument("--trace-bits", choices=["64", "128"], required=True)
args = parser.parse_args()
TRACE_ID = int("1234567890abcdef1234567890abcdef" if args.trace_bits == "128" else "1234567890abcdef", 16)
PARENT_ID = 123456789


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        fields = {key: getattr(record, key, None) for key in (
            "dd.trace_id", "dd.span_id", "dd.service", "dd.env", "dd.version")}
        self.records.append({"message": record.getMessage(), "fields": fields})
        if fields["dd.trace_id"] is not None:
            formatter = logging.Formatter("%(message)s dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s")
            print("TEXT " + formatter.format(record), flush=True)
        print("STRUCTURED " + json.dumps({"message": record.getMessage(), **fields}), flush=True)


handler = Records()
logger = logging.getLogger("signals-lab")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False
logger.info("outside-before")
tracer.context_provider.activate(Context(trace_id=TRACE_ID, span_id=PARENT_ID, sampling_priority=2))
with tracer.trace("signals.request", resource="logging-and-runtime") as span:
    logger.info("inside")
    identity = {"trace_id": str(span.trace_id), "span_id": str(span.span_id), "parent_id": str(span.parent_id)}
tracer.context_provider.activate(None)
logger.info("outside-after")
# RuntimeMetricsWorker uses the configured interval, without manually creating
# its metric values or invoking the DogStatsD client.
time.sleep(2.2)
tracer.shutdown()
print("RESULT " + json.dumps({"identity": identity, "logs": handler.records}), flush=True)
