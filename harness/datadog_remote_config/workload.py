"""One SDK process emits traces on controller requests while native RC polls."""
import argparse
import asyncio
import aiohttp
import json
import logging
from pathlib import Path
import time
import ddtrace
from ddtrace import tracer
from ddtrace.internal.runtime import get_runtime_id

parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
parser.add_argument("--url", required=True)
args = parser.parse_args()
root = Path(args.directory)
ddtrace.patch(logging=True, aiohttp=True)
class Handler(logging.Handler):
    record = None
    def emit(self, record):
        self.record = dict(record.__dict__)
handler = Handler()
logger = logging.getLogger("rc-lab")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
(root / "ready.json").write_text(json.dumps({"runtime_id": get_runtime_id(), "tracer_version": ddtrace.__version__}))
async def request():
    async with aiohttp.ClientSession() as session:
        async with session.get(args.url, headers={"x-rc-header": "header-value"}) as response:
            assert response.status == 200
            assert json.loads(await response.text())["version"] == "7.83.1"

for stage in (1, 2, 3):
    deadline = time.monotonic() + 90
    while not (root / (str(stage) + ".command")).exists():
        assert time.monotonic() < deadline, "controller did not request stage"
        time.sleep(0.05)
    with tracer.trace("rc.lab.stage", resource="stage-" + str(stage)) as span:
        logger.info("remote-config-stage-%d", stage)
        asyncio.run(request())
        identity = {"trace_id": str(span.trace_id), "span_id": str(span.span_id), "stage": stage,
                    "log": {key: value for key, value in handler.record.items() if key.startswith("dd.")}}
    tracer.flush()
    (root / (str(stage) + ".identity.json")).write_text(json.dumps(identity, indent=2) + "\n")
