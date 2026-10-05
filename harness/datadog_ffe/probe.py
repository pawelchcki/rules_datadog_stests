"""Validate native SDK flags, agentless delivery, EVP events and OTLP metrics."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
from urllib.request import Request, urlopen

from harness.datadog_agent.probe import BASE_ENV, resolve
from harness.datadog_ffe.backend import Backend, CONFIG_PATH

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def attrs(values):
    return {entry["key"]: next(iter(entry["value"]["value"].values())) for entry in values}


def verify(capture, identity, vendor):
    assert identity["ddtraceVersion"] == "4.14.0"
    assert identity["openfeatureVersion"] == "0.8.3" and identity["otelVersion"] == "1.44.0"
    native = [span for record in capture["datadog"] for trace in record["payload"]["traces"] for span in trace]
    assert len(native) == len(identity["identities"]) == 3
    assert all(record["payload"]["wire_version"] == "v0.5" for record in capture["datadog"])
    for expected in identity["identities"]:
        span = next(span for span in native if span["span_id"] == expected["span_id"])
        assert span["trace_id"] == expected["trace_id"] & ((1 << 64) - 1)
        assert span["duration"] > 0 and span["service"] == "ffe-lab"

    configuration = [record for record in capture["backend"] if record["path"].startswith(CONFIG_PATH)]
    assert identity["initialStatus"]["configurationRequests"] == identity["beforeProviderAccess"]["configurationRequests"] == 0
    assert configuration[0]["status"] == 200
    assert 304 in [record["status"] for record in configuration]
    assert len([record for record in configuration if record["status"] == 200]) >= 2
    assert any(record["headers"].get("if-none-match") == '"ufc-1"' for record in configuration[1:])
    assert all("dd-api-key" not in record["headers"] for record in configuration)
    assert all(record["headers"]["dd-client-library-language"] == "python" and
        record["headers"]["dd-client-library-version"] == "4.14.0" for record in configuration)
    assert configuration[0]["payload"]["data"]["attributes"]["flags"]["basic-flag"]["variations"]["on"]["value"] is True
    assert [record for record in configuration if record["status"] == 200][-1]["payload"]["data"]["attributes"]["flags"]["basic-flag"]["variations"]["on"]["value"] is False

    expected_cases = [(path.name, index, case) for path in sorted(vendor.glob("test*.json"))
        for index, case in enumerate(json.loads(path.read_text()))]
    assert len(expected_cases) == len(identity["evaluations"]) == 217
    for (file, index, case), actual in zip(expected_cases, identity["evaluations"]):
        assert (actual["file"], actual["index"], actual["input"]) == (file, index, case)
        assert actual["actual"]["value"] == case["result"]["value"], (file, index, case, actual)
    assert identity["enriched"][0]["value"] is True and identity["enriched"][1]["value"] == "treatment"
    assert all(item["value"] is True for item in identity["repeated"])
    assert identity["changed"]["value"] is False and identity["changed"]["errorCode"] is None

    spec = importlib.util.spec_from_file_location("pinned_ffe_utils", vendor / "utils.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root = next(span for span in native if span["name"] == "ffe.enrichment.root")
    child = next(span for span in native if span["name"] == "ffe.enrichment.child")
    assert child["parent_id"] == root["span_id"]
    assert helper.decode_delta_varint(root["meta"]["ffe_flags_enc"]) == [100, 108]
    subjects = json.loads(root["meta"]["ffe_subjects_enc"])
    assert {key: helper.decode_delta_varint(value) for key, value in subjects.items()} == {
        helper.hash_targeting_key("enrichment-user"): [100, 108], helper.hash_targeting_key("deduplicated-user"): [100]}
    assert not any(key.startswith("ffe_") for key in child["meta"])
    default_root = next(span for span in native if span["name"] == "ffe.fixture-evaluations")
    assert "empty_flag" in json.loads(default_root["meta"]["ffe_runtime_defaults"])

    evp = [record for record in capture["backend"] if record["path"].startswith("/evp_proxy/v2/")]
    assert evp and all(record["method"] == "POST" and record["status"] == 202 and
        record["headers"]["x-datadog-evp-subdomain"] == "event-platform-intake" for record in evp)
    context = {"service": "ffe-lab", "env": "ffe-env", "version": "ffe-version"}
    assert all(record["payload"]["context"] == context for record in evp)
    exposures = [event for record in evp for event in record["payload"].get("exposures", [])]
    repeated = [event for event in exposures if event["subject"]["id"] == "deduplicated-user"]
    assert len(repeated) == 1
    event = repeated[0]
    assert event["flag"]["key"] == "basic-flag" and event["variant"]["key"] == "on"
    assert event["allocation"]["key"] == "on-allocation" and event["timestamp"] > 0
    assert event["subject"]["attributes"] == {"user_email": "alice@example.com", "org_id": 1234}
    counts = [event for record in evp for event in record["payload"].get("flagEvaluations", [])]
    repeated_counts = [event for event in counts if event.get("targeting_key") == "deduplicated-user"]
    assert len(repeated_counts) == 1 and repeated_counts[0]["evaluation_count"] == 5
    assert repeated_counts[0]["flag"]["key"] == "basic-flag" and repeated_counts[0]["variant"]["key"] == "on"
    assert repeated_counts[0]["first_evaluation"] <= repeated_counts[0]["last_evaluation"] <= repeated_counts[0]["timestamp"]
    assert any(event.get("runtime_default_used") is True and event.get("error") for event in counts)
    expected_count = 217 + 7 + len(identity["changedEvaluations"])
    assert sum(event["evaluation_count"] for event in counts) == expected_count

    points = []
    for record in capture["otlp"]:
        assert record["signal"] == "metrics" and record["encoding"] == "protobuf"
        for resource in record["payload"]["resource_metrics"]:
            tags = attrs(resource["resource"]["attributes"])
            assert tags["service.name"] == "ffe-lab"
            for scope in resource["scope_metrics"]:
                for metric in scope["metrics"]:
                    if metric["name"] == "feature_flag.evaluations":
                        assert scope["scope"]["name"] == "ddtrace.openfeature"
                        points += metric["data"]["sum"]["data_points"]
    assert points and sum(next(iter(point["value"].values())) for point in points) == expected_count
    experiment = [point for point in points if attrs(point["attributes"]).get("feature_flag.key") == "experiment-flag"]
    assert sum(next(iter(point["value"].values())) for point in experiment) == 1
    assert any(attrs(point["attributes"])["feature_flag.result.variant"] == "treatment" for point in experiment)
    assert any(exemplar["span_id"] == f"{child['span_id']:016x}" and
        exemplar["trace_id"] == f"{identity['identities'][2]['trace_id']:032x}"
        for point in experiment for exemplar in point["exemplars"])


def execute(args):
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    vendor = Path(args.manifest).parent
    manifest = json.loads(Path(args.manifest).read_text())
    assert manifest["revision"] == REVISION
    for name, metadata in manifest["files"].items():
        assert sha((vendor / name).read_bytes()) == metadata["sha256"], name
    (out / "source-manifest.json").write_bytes(Path(args.manifest).read_bytes())
    (out / "wheel-lock.json").write_bytes(Path(args.wheel_lock).read_bytes())
    (out / "otel-wheel-lock.json").write_bytes(Path(args.otel_wheel_lock).read_bytes())
    configuration = json.loads((vendor / "flags-v1.json").read_text())
    configuration["flags"].update(json.loads((vendor / "span-enrichment-flags.json").read_text())["flags"])
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    port = next(int(value) for key, value in ports.items() if key.endswith("//harness:otel_sink_service"))
    sink = "http://127.0.0.1:" + str(port)
    for protocol in ("datadog", "otlp"):
        urlopen(Request(sink + "/reset?protocol=" + protocol, method="POST")).close()
    backend = Backend(sink, configuration)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    origin = "http://127.0.0.1:" + str(backend.server_port)
    env = dict(BASE_ENV, DD_TRACE_AGENT_URL=origin, DD_TRACE_API_VERSION="v0.5", DD_SERVICE="ffe-lab",
        DD_ENV="ffe-env", DD_VERSION="ffe-version", DD_METRICS_OTEL_ENABLED="true",
        OTEL_EXPORTER_OTLP_ENDPOINT=origin, OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
        OTEL_METRIC_EXPORT_INTERVAL="60000", DD_FEATURE_FLAGS_ENABLED="true",
        DD_FEATURE_FLAGS_CONFIGURATION_SOURCE="agentless", DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL=origin,
        DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_POLL_INTERVAL_SECONDS="1",
        DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_REQUEST_TIMEOUT_SECONDS="1",
        DD_API_KEY="system-tests-mock-api-key", DD_EXPERIMENTAL_FLAGGING_PROVIDER_SPAN_ENRICHMENT_ENABLED="true",
        DD_FFE_INTAKE_HEARTBEAT_INTERVAL="0.2")
    command = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
    command += ["--prepend-path=PYTHONPATH=" + args.overlay, "--instance=datadog-ffe"]
    command += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
    identity_path = out / "identity.json"
    command += ["--", args.app, "--identity-file", str(identity_path), "--backend", origin, "--vendor", str(vendor)]
    clean = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
    results = []
    try:
        with (out / "app.log").open("wb") as log:
            process = subprocess.run(command, stdout=log, stderr=log, env=clean, timeout=40)
        assert process.returncode == 0, (out / "app.log").read_text(errors="replace")[-8000:]
        identity = json.loads(identity_path.read_text())
        capture = {protocol: json.load(urlopen(sink + "/dump?protocol=" + protocol)) for protocol in ("datadog", "otlp")}
        capture["backend"] = backend.snapshot()
        raw = json.dumps(capture, sort_keys=True).encode()
        (out / "capture.json").write_bytes(raw)
        for capability in ("feature_flags_agentless", "feature_flags_dynamic_evaluation", "feature_flags_event_enrichment",
                "feature_flags_exposures", "feature_flags_evp_flagevaluation", "feature_flags_eval_metrics"):
            results.append({"name": capability, "status": "failed", "capabilityNames": [capability],
                "capabilityInventoryRevision": REVISION, "sourceRevision": REVISION, "origin": "local",
                "sdkVersion": identity["ddtraceVersion"], "configuration": env, "captureFile": "capture.json",
                "captureSha256": sha(raw), "workloadSha256": sha(Path(args.app).read_bytes()),
                "artifacts": [{"file": name, "sha256": sha((out / name).read_bytes())}
                    for name in ("identity.json", "source-manifest.json", "wheel-lock.json", "otel-wheel-lock.json")]})
        verify(capture, identity, vendor)
        for result in results:
            result["status"] = "passed"
            print(result["name"], "passed", flush=True)
    finally:
        backend.shutdown()
        backend.server_close()
        (out / "datadog-ffe-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")


def main():
    if not __debug__:
        raise RuntimeError("Native capability assertions require Python optimization disabled")
    parser = argparse.ArgumentParser()
    for name in ("launcher", "rootfs", "app", "overlay", "wheel-lock", "otel-wheel-lock", "manifest"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for name in ("launcher", "rootfs", "app", "overlay", "wheel_lock", "otel_wheel_lock", "manifest"):
        setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1])
        if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    execute(args)


if __name__ == "__main__":
    main()
