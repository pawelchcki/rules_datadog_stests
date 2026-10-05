"""Emit a control span, then crash only this disposable SDK subprocess."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import resource
import time
import ddtrace
from ddtrace import tracer
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.core import crashtracking
parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
args = parser.parse_args()
root = Path(args.directory)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
assert crashtracking.is_available, "Native crashtracker unavailable"
if os.environ["DD_CRASHTRACKING_ENABLED"] == "true":
    # The workload may run while bootstrap products finish enrollment. The
    # fatal signal must wait for the native handler's initialized status.
    deadline = time.monotonic() + 60
    while not crashtracking.is_started():
        assert time.monotonic() < deadline, "Native crashtracker did not initialize"
        time.sleep(0.05)
with tracer.trace("extra.crash.control") as span:
    identity = {"runtime_id": get_runtime_id(), "tracer_version": ddtrace.__version__, "span_id": str(span.span_id), "trace_id": str(span.trace_id),
                "crashtracker_available": crashtracking.is_available, "crashtracker_started": crashtracking.is_started()}
tracer.flush()
(root / "ready.json").write_text(json.dumps(identity))
deadline = time.monotonic() + 60
while not (root / "crash.command").exists():
    assert time.monotonic() < deadline
    time.sleep(0.05)
ctypes.string_at(0)
