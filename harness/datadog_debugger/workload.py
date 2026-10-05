"""Exercise ordinary Python functions after the real Agent installs probes."""
import argparse
import json
from pathlib import Path
import sys
import time
import ddtrace
from ddtrace import tracer
from ddtrace.internal.runtime import get_runtime_id
sys.path.insert(0, str(Path(__file__).parent))
import probe_target

parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
args = parser.parse_args()
root = Path(args.directory)
(root / "ready.json").write_text(json.dumps({"runtime_id": get_runtime_id(), "tracer_version": ddtrace.__version__}))
for stage in (1, 2, 3):
    deadline = time.monotonic() + 90
    while not (root / (str(stage) + ".command")).exists():
        assert time.monotonic() < deadline, "controller did not request stage"
        time.sleep(0.05)
    with tracer.trace("debugger.lab.stage", resource="stage-" + str(stage)) as span:
        result = []
        for value in (1, 3):
            result.append(probe_target.calculate(value, "local-password-do-not-capture"))
            time.sleep(0.03)
        budget_calls = 0
        budget_started = time.monotonic()
        if stage == 2:
            for value in range(150):
                assert probe_target.budget(value) == value + 1
                budget_calls += 1
        budget_elapsed_seconds = time.monotonic() - budget_started
        assert result == ["result:2", "result:6"]
        identity = {"stage": stage, "trace_id": str(span.trace_id), "span_id": str(span.span_id), "result": result, "budget_calls": budget_calls, "budget_elapsed_seconds": budget_elapsed_seconds}
    tracer.flush()
    time.sleep(2)  # Let the documented periodic debugger uploader flush.
    (root / (str(stage) + ".identity.json")).write_text(json.dumps(identity, indent=2) + "\n")
