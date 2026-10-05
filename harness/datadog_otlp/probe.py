"""Assert real Datadog-configured OTLP metrics/logs and retain native evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from harness.datadog_agent.probe import BASE_ENV, resolve

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def attributes(values):
    return {item["key"]: next(iter(item["value"]["value"].values())) for item in values}


def logs(records):
    return [log for record in records if record["signal"] == "logs"
        for resource in record["payload"]["resource_logs"] for scope in resource["scope_logs"]
        for log in scope["log_records"]]


def metrics(records):
    # Inspect the first flush of the controlled measurements. Shutdown can
    # export a subsequent delta of zero for an unchanged observable counter.
    entries = {}
    for record in records:
        if record["signal"] == "metrics":
            for resource in record["payload"]["resource_metrics"]:
                for scope in resource["scope_metrics"]:
                    for metric in scope["metrics"]:
                        entries.setdefault(metric["name"], metric)
    return entries


def check_pipeline(records, traces, identity):
    assert identity["ddtraceVersion"] == "4.14.0"
    assert identity["apiVersion"] == identity["exporterVersion"] == "1.44.0"
    assert identity["meterProvider"] == "MeterProvider" and identity["loggerProvider"] == "LoggerProvider"
    assert identity["metricFlush"] and identity["logFlush"]
    native = [span for record in traces for trace in record["payload"]["traces"] for span in trace]
    control = identity["identities"][0]
    span = next(span for span in native if span["span_id"] == control["span_id"])
    assert span["trace_id"] == control["trace_id"] & ((1 << 64) - 1)
    assert span["resource"] == "otlp.control" and span["service"] == "otlp-lab" and span["duration"] > 0
    assert all(record["payload"]["wire_version"] == "v0.5" for record in traces)
    metric_entries = metrics(records)
    expected = {"lab.counter", "lab.inflight", "lab.latency", "lab.gauge", "lab.observable_counter", "lab.observable_gauge", "lab.observable_updown"}
    assert expected <= metric_entries.keys(), metric_entries.keys()
    values = {"lab.counter": 5, "lab.inflight": 5, "lab.gauge": 17,
              "lab.observable_counter": 11, "lab.observable_gauge": 13, "lab.observable_updown": 7}
    for name, value in values.items():
        metric = metric_entries[name]
        data = next(iter(metric["data"].values()))
        point = data["data_points"][0]
        assert next(iter(point["value"].values())) == value, metric
        assert point["time_unix_nano"] > 0
    histogram = metric_entries["lab.latency"]["data"]["histogram"]["data_points"][0]
    assert histogram["count"] == 2 and histogram["sum"] == 10
    assert sum(histogram["bucket_counts"]) == 2
    assert metric_entries["lab.counter"]["description"] == "controlled counter"
    assert metric_entries["lab.counter"]["unit"] == "requests"
    assert attributes(metric_entries["lab.counter"]["data"]["sum"]["data_points"][0]["attributes"]) == {"kind": "sync", "boolean": True, "integer": 3}
    exemplar = metric_entries["lab.counter"]["data"]["sum"]["data_points"][0]["exemplars"][0]
    assert exemplar["trace_id"] == f"{control['trace_id']:032x}" and exemplar["span_id"] == f"{control['span_id']:016x}"
    observed_logs = logs(records)
    assert len(observed_logs) == 5
    assert {log["body"]["value"]["string_value"] for log in observed_logs} == {"lab.log." + str(i) for i in range(5)}
    for log in observed_logs:
        assert log["severity_number"] == 9 and log["severity_text"] == "INFO"
        assert log["trace_id"] == f"{control['trace_id']:032x}" and log["span_id"] == f"{control['span_id']:016x}"
        assert attributes(log["attributes"])["boolean"] is True
    for record in records:
        assert record["encoding"] == "protobuf" and record["request"]["content_type"] == "application/x-protobuf"
        for resource in record["payload"].get("resource_metrics", record["payload"].get("resource_logs", [])):
            tags = attributes(resource["resource"]["attributes"])
            assert tags["service.name"] == "otlp-lab" and tags["service.version"] == "otlp-version"
            assert tags["deployment.environment"] == "otlp-env" and tags["team"] == "lab"
            assert tags["telemetry.sdk.version"] == "1.44.0"


def execute(args):
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    wheel_lock = Path(args.wheel_lock).read_bytes()
    (out / "wheel-lock.json").write_bytes(wheel_lock)
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    port = next(int(value) for key, value in ports.items() if key.endswith("//harness:otel_sink_service"))
    sink = "http://127.0.0.1:" + str(port)
    profiles = [
        ("generic-endpoint", {}, ["otel_metrics_api", "otel_logs_enabled", "otel_exporter_otlp_endpoint"], 0),
        ("specific-endpoints", {"OTEL_EXPORTER_OTLP_ENDPOINT": sink + "/unused-base",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": sink + "/v1/metrics", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": sink + "/v1/logs"},
            ["otel_exporter_otlp_metrics_endpoint", "otel_exporter_otlp_logs_endpoint"], 0),
        ("log-queue-small", {"OTEL_BLRP_MAX_QUEUE_SIZE": "16", "OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "2"}, ["otel_blrp_max_queue_size"], 0),
        ("log-queue-large", {"OTEL_BLRP_MAX_QUEUE_SIZE": "128"}, ["otel_blrp_max_queue_size"], 0),
        ("metric-timeout", {"OTEL_METRIC_EXPORT_TIMEOUT": "12000"}, ["otel_metric_export_timeout"], 0),
        ("log-batch-limit", {"OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "2"}, ["otel_blrp_max_export_batch_size"], 0),
        ("log-schedule-fast", {"OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "64", "OTEL_BLRP_SCHEDULE_DELAY": "200"}, ["otel_blrp_schedule_delay"], 0.8),
        ("log-schedule-delayed", {"OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "64", "OTEL_BLRP_SCHEDULE_DELAY": "60000"}, ["otel_blrp_schedule_delay"], 0.3),
    ]
    results = []
    try:
        for name, extra, features, pause in profiles:
            directory = out / name
            directory.mkdir(exist_ok=True)
            for protocol in ("otlp", "datadog"):
                urlopen(Request(sink + "/reset?protocol=" + protocol, method="POST")).close()
            env = dict(BASE_ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION="v0.5", DD_SERVICE="otlp-lab",
                DD_ENV="otlp-env", DD_VERSION="otlp-version", DD_TAGS="team:lab", DD_TRACE_OTEL_ENABLED="true",
                DD_METRICS_OTEL_ENABLED="true", DD_LOGS_OTEL_ENABLED="true", OTEL_EXPORTER_OTLP_ENDPOINT=sink,
                OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf", OTEL_METRIC_EXPORT_INTERVAL="60000",
                OTEL_METRIC_EXPORT_TIMEOUT="7500", OTEL_BLRP_MAX_QUEUE_SIZE="64", OTEL_BLRP_MAX_EXPORT_BATCH_SIZE="64",
                OTEL_BLRP_SCHEDULE_DELAY="60000")
            env.update(extra)
            command = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
            command += ["--prepend-path=PYTHONPATH=" + args.overlay, "--instance=datadog-otlp-" + name]
            command += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
            identity_path = directory / "identity.json"
            identity_path.unlink(missing_ok=True)
            command += ["--", args.app, "--identity-file", str(identity_path), "--pause", str(pause)]
            clean = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
            with (directory / "app.log").open("wb") as log:
                process = subprocess.run(command, stdout=log, stderr=log, env=clean, timeout=30)
            assert process.returncode == 0, (directory / "app.log").read_text(errors="replace")[-8000:]
            identity = json.loads(identity_path.read_text())
            records = json.load(urlopen(sink + "/dump?protocol=otlp"))
            traces = json.load(urlopen(sink + "/dump?protocol=datadog"))
            capture_bytes = json.dumps({"otlp": records, "datadog": traces}, sort_keys=True).encode()
            capture_path = directory / "capture.json"
            capture_path.write_bytes(capture_bytes)
            result = {"name": name, "status": "failed", "capabilityInventoryRevision": REVISION, "capabilityNames": features,
                "origin": "local", "configuration": env, "sdkVersion": identity["ddtraceVersion"],
                "captureFile": name + "/capture.json", "captureSha256": sha(capture_bytes),
                "workloadSha256": sha(Path(args.app).read_bytes()),
                "artifacts": [{"file": name + "/identity.json", "sha256": sha(identity_path.read_bytes())},
                    {"file": "wheel-lock.json", "sha256": sha(wheel_lock)}]}
            results.append(result)
            check_pipeline(records, traces, identity)
            assert all(record["request"]["path"] == "/v1/" + record["signal"] for record in records)
            if name.startswith("log-queue"):
                assert identity["processorConfiguration"][0]["max_queue_size"] == int(env["OTEL_BLRP_MAX_QUEUE_SIZE"]), identity
            if name == "metric-timeout":
                assert identity["readerConfiguration"][0]["export_timeout_millis"] == 12000, identity
            if name == "log-batch-limit":
                counts = [len(logs([record])) for record in records if record["signal"] == "logs"]
                assert counts and max(counts) == 2 and sum(counts) == 5, counts
                assert identity["processorConfiguration"][0]["max_export_batch_size"] == 2
            if name.startswith("log-schedule"):
                received = [record["received_unix_nano"] for record in records if record["signal"] == "logs"]
                if name.endswith("fast"):
                    assert min(received) < identity["flushStartedUnixNano"], received
                else:
                    assert min(received) >= identity["flushStartedUnixNano"], received
                assert min(received) >= identity["logEmissionStartedUnixNano"]
            result["status"] = "passed"
            print(name, "passed", flush=True)
    finally:
        (out / "datadog-otlp-results.json").write_text(json.dumps({"schemaVersion": 1, "results": results,
            "unsupported": [{"name": "otel_logs_exporter", "reason": "Pinned Datadog4.14 still exports all5records with OTEL_LOGS_EXPORTER=none"},
                {"name": "otel_blrp_export_timeout", "reason": "Pinned OTelSDK1.44 batch processor stores export timeout but explicitly does not use it"}]}, indent=2) + "\n")
    assert len(results) == len(profiles)


def main():
    if not __debug__:
        raise RuntimeError("Native capability assertions require Python optimization disabled")
    parser = argparse.ArgumentParser()
    for name in ("launcher", "rootfs", "app", "overlay", "wheel-lock"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for name in ("launcher", "rootfs", "app", "overlay", "wheel_lock"):
        setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1])
        if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    execute(args)


if __name__ == "__main__":
    main()
