"""Public OpenFeature API against the actual Datadog provider and live HTTP config."""
import argparse
import json
from importlib.metadata import version
from pathlib import Path
import time
from urllib.request import Request, urlopen

from ddtrace import tracer, __version__ as DDTRACE_VERSION
from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from ddtrace.openfeature import DataDogProvider
from opentelemetry import metrics


def plain(value):
    return getattr(value, "value", value)


def main(args):
    status = json.load(urlopen(args.backend + "/_lab/status"))
    # The HTTP trace writer can handshake independently; configuration delivery
    # must remain lazy until the application's provider is accessed.
    time.sleep(0.3)
    before = json.load(urlopen(args.backend + "/_lab/status"))
    provider = DataDogProvider()
    api.set_provider(provider)
    client = api.get_client()
    functions = {"BOOLEAN": client.get_boolean_details, "STRING": client.get_string_details,
        "INTEGER": client.get_integer_details, "NUMERIC": client.get_float_details, "JSON": client.get_object_details}
    evaluations = []
    identities = []
    def evaluate(case):
        context = EvaluationContext(targeting_key=case["targetingKey"], attributes=case.get("attributes", {}))
        result = functions[case["variationType"]](case["flag"], case["defaultValue"], context)
        return {"value": result.value, "reason": plain(result.reason), "errorCode": plain(result.error_code), "variant": result.variant}
    with tracer.trace("ffe.fixture-evaluations", service="ffe-lab") as root:
        identities.append({"span_id": root.span_id, "trace_id": root.trace_id})
        for path in sorted(Path(args.vendor).glob("*.json")):
            if path.name in ("manifest.json", "flags-v1.json", "span-enrichment-flags.json"):
                continue
            for index, case in enumerate(json.loads(path.read_text())):
                evaluations.append({"file": path.name, "index": index, "input": case, "actual": evaluate(case)})
    with tracer.trace("ffe.enrichment.root", service="ffe-lab") as root:
        with tracer.trace("ffe.enrichment.child", service="ffe-lab") as child:
            identities += [{"span_id": span.span_id, "trace_id": span.trace_id} for span in (root, child)]
            enriched = []
            for flag, kind, default in (("basic-flag", "BOOLEAN", False), ("experiment-flag", "STRING", "default")):
                enriched.append(evaluate({"flag": flag, "variationType": kind, "defaultValue": default, "targetingKey": "enrichment-user"}))
            repeated = [evaluate({"flag": "basic-flag", "variationType": "BOOLEAN", "defaultValue": False,
                "targetingKey": "deduplicated-user", "attributes": {"user_email": "alice@example.com", "org_id": 1234}}) for _ in range(5)]
    tracer.flush()
    # Observe a real conditional poll and a changed configuration while the
    # public provider remains alive.
    time.sleep(1.2)
    urlopen(Request(args.backend + "/_lab/change", data=b"{}", headers={"Content-Type": "application/json"})).close()
    changed = None
    changed_evaluations = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        changed = evaluate({"flag": "basic-flag", "variationType": "BOOLEAN", "defaultValue": True, "targetingKey": "changed-user"})
        changed_evaluations.append(changed)
        if changed["value"] is False:
            break
        time.sleep(0.1)
    api.shutdown()
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "force_flush"):
        meter_provider.force_flush()
    result = {"ddtraceVersion": DDTRACE_VERSION, "openfeatureVersion": version("openfeature-sdk"), "otelVersion": version("opentelemetry-sdk"), "beforeProviderAccess": before, "initialStatus": status,
        "evaluations": evaluations, "identities": identities, "enriched": enriched, "repeated": repeated, "changed": changed, "changedEvaluations": changed_evaluations}
    Path(args.identity_file).write_text(json.dumps(result, indent=2) + "\n")
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("identity-file", "backend", "vendor"):
        parser.add_argument("--" + name, required=True)
    main(parser.parse_args())
