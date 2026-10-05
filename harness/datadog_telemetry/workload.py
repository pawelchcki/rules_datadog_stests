"""Controlled public SDK operations; telemetry is emitted by the real SDK."""
import argparse
import json
from pathlib import Path
import platform
import time

import aiohttp
import ddtrace
from ddtrace import tracer, patch
from ddtrace.internal.runtime import get_runtime_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity-file", required=True)
    parser.add_argument("--stop-file", required=True)
    args = parser.parse_args()
    # Enable an installed integration without creating uncontrolled HTTP spans.
    patch(aiohttp=True)
    identities = []
    for index in range(3):
        with tracer.trace("telemetry.controlled", service="telemetry-lab") as span:
            span.set_tag("telemetry.case", str(index))
            identities.append({"trace_id": str(span.trace_id), "span_id": str(span.span_id)})
    tracer.flush()
    identity = {"runtime_id": get_runtime_id(), "tracer_version": ddtrace.__version__,
                "language_version": platform.python_version(), "spans": identities,
                "dependency": {"name": "aiohttp", "version": aiohttp.__version__}}
    path = Path(args.identity_file)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(identity, indent=2) + "\n")
    temporary.replace(path)
    deadline = time.monotonic() + 60
    while not Path(args.stop_file).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("telemetry probe did not finish within 60 seconds")
        time.sleep(0.1)
    tracer.shutdown()


if __name__ == "__main__":
    main()
