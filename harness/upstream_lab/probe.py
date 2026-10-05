"""Execute pinned upstream parametric bodies against the real Python SDK and intake."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import AgentIntake, Library, load_cases
from local_cases import cases as local_cases

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
BASE_ENV = {
    "DD_TRACE_ENABLED": "true",
    "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.05",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false", "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_RUNTIME_METRICS_ENABLED": "false", "DD_PROFILING_ENABLED": "false",
    "DD_APPSEC_ENABLED": "false", "DD_IAST_ENABLED": "false", "DD_SCA_ENABLED": "false",
    "DD_DYNAMIC_INSTRUMENTATION_ENABLED": "false", "DD_EXCEPTION_REPLAY_ENABLED": "false",
    "DD_DATA_STREAMS_ENABLED": "false", "DD_LLMOBS_ENABLED": "false",
    "DD_LOGS_INJECTION": "false", "DD_TRACE_COMPUTE_STATS": "false",
    "DD_TRACE_STATS_COMPUTATION_ENABLED": "false", "DD_TRACE_STARTUP_LOGS": "false",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def resolve(path):
    for candidate in [Path(path)] + [Path(root) / path for root in
            (os.environ.get("RUNFILES_DIR"), os.environ.get("TEST_SRCDIR")) if root]:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError(path)


def case_environment(case, sink, wire):
    env = dict(BASE_ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION=wire)
    for key, value in case["parameters"].get("library_env", {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def exclusion(case, wire, exclusions):
    reason = exclusions.get(case["name"], exclusions.get(case["file"] + "::" + case["class"] + "::" + case["method"]))
    if reason:
        return reason
    parameters = case["parameters"]
    if any(key.startswith("agent_") for key in parameters):
        return "Requires configurable upstream test-agent stats responses; direct native intake lab does not provide them"
    env = parameters.get("library_env", {})
    if env.get("DD_TRACE_API_VERSION", wire) != wire:
        return "Case requires wire " + env["DD_TRACE_API_VERSION"] + "; this target uses " + wire
    if env.get("DD_TRACE_STATS_COMPUTATION_ENABLED", "false") == "true":
        return "Requires client-side stats negotiation and stats intake assertions"
    if "trace_agent_connection" in case["features"]:
        return "These methods deliberately configure an unreachable Agent URL; native intake lab requires a reachable Agent"
    if "trace_log_directory" in case["features"]:
        return "Requires tracer filesystem/log-directory fixture and container_exec_run API"
    return None


@contextmanager
def application(args, env, out, group):
    ready = out / ("configuration-" + str(group) + ".port")
    ready.unlink(missing_ok=True)
    log_path = out / ("configuration-" + str(group) + ".app.log")
    launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs]
    launch += args.injection_flags
    launch += ["--instance=datadog-upstream-" + str(group)]
    launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
    launch += ["--", args.app, "--ready-file", str(ready)]
    proc_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
    with log_path.open("wb") as log:
        proc = subprocess.Popen(launch, stdout=log, stderr=log, env=proc_env, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert proc.poll() is None, log_path.read_text(errors="replace")
                assert time.monotonic() < deadline, "readiness timeout: " + log_path.read_text(errors="replace")
                time.sleep(0.05)
            url = "http://127.0.0.1:" + ready.read_text().strip()
            while True:
                try:
                    with urlopen(url + "/healthz", timeout=5) as response:
                        health = json.load(response)
                    break
                except (OSError, ConnectionError):
                    assert proc.poll() is None, log_path.read_text(errors="replace")
                    assert time.monotonic() < deadline, "health readiness timeout"
                    time.sleep(0.05)
            assert health["ready"] and health["ddtraceVersion"] == "4.14.0", health
            library = Library(url)
            library.sdk_version = health["ddtraceVersion"]
            library.log_path = log_path
            yield library
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def assert_sdk_evidence(library, agent):
    identities = [operation["result"] for operation in library.operations if operation["operation"] in ("start", "otel_start")]
    identities += [operation["result"][name] for operation in library.operations
                   if operation["operation"] == "mixed_context"
                   for name in ("dd_root", "otel_child", "otel_root", "dd_child")]
    identities += [operation["result"]["control"] for operation in library.operations
                   if operation["operation"] == "http_request"]
    assert identities, "Upstream case did not create any SDK spans"
    def observed():
        return {int(span["span_id"]): span for snapshot in agent.captures for record in snapshot
                for chunk in record["payload"]["traces"] for span in chunk}
    for _ in range(40):
        spans = observed()
        if all(identity["span_id"] in spans for identity in identities):
            break
        time.sleep(0.05)
        agent.traces()
    for identity in identities:
        assert identity["span_id"] in spans, ("SDK span missing from native intake", identity)
        span = spans[identity["span_id"]]
        assert int(span["trace_id"]) == identity["trace_id"] & ((1 << 64) - 1), (identity, span)
        assert int(span["start"]) > 0 and int(span["duration"]) > 0, span


def execute(args):
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    vendor = Path(args.vendor)
    if vendor.is_file():
        vendor = vendor.parent
    manifest = json.loads((vendor / "manifest.json").read_text())
    assert manifest["revision"] == REVISION, manifest
    for filename, spec in manifest["files"].items():
        assert digest((vendor / filename).read_bytes()) == spec["sha256"], filename
    exclusions = json.loads((Path(__file__).parent / "exclusions.json").read_text())
    cases = load_cases(vendor) + local_cases()
    if args.select:
        cases = [case for case in cases if args.select in case["name"]]
    assert cases, "No upstream cases selected"
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    matches = [int(value) for key, value in ports.items() if key.endswith("//harness:otel_sink_service")]
    assert len(matches) == 1, ports
    sink = "http://127.0.0.1:" + str(matches[0])
    results = []
    groups = defaultdict(list)
    for case in cases:
        env = case_environment(case, sink, args.wire)
        result = {
            "name": case["name"], "sourceRevision": REVISION, "wire": args.wire,
            "source": "https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/parametric/" + case["file"],
            "sourceMethod": case["method"], "sourceClass": case["class"], "traceView": "native-chunks-grouped-by-trace-id",
            "sourceSha256": digest((Path(__file__).parent / case["file"]).read_bytes()) if case.get("local") else manifest["files"][case["file"]]["sha256"],
            "workloadSha256": digest(Path(args.app).read_bytes()),
            "capabilityInventoryRevision": REVISION, "upstreamFeatures": [] if case.get("local") else case["features"],
            "capabilityNames": [feature for feature in case["features"] if not (feature == "dd_service_mapping" and case["class"] == "Test_TracerUniversalServiceTagging")
                and not (feature == "f_otel_interoperability" and case["method"] == "test_span_creation_using_otel")],
            "configuration": env,
        }
        if case.get("local"):
            result.update(origin="local", source="harness/upstream_lab/local_cases.py", sourceRevision=None)
        reason = exclusion(case, args.wire, exclusions)
        if reason:
            result.update(status="unsupported", detail=reason)
            results.append(result)
        else:
            groups[json.dumps(env, sort_keys=True)].append((case, result))
    try:
        for group, (serialized, pending) in enumerate(groups.items()):
            env = json.loads(serialized)
            with application(args, env, out, group) as library:
                for case, result in pending:
                    result["sdkVersion"] = library.sdk_version
                    agent = AgentIntake(sink, args.wire)
                    agent.clear()
                    library.rpc("reset")
                    library.operations.clear()
                    try:
                        values = dict(case["parameters"], test_agent=agent, test_library=library)
                        signature = inspect.signature(case["function"])
                        case["function"](case["instance"], **{key: values[key] for key in signature.parameters if key != "self"})
                        library.dd_flush()
                        agent.traces()
                        disabled = env.get("DD_TRACE_ENABLED") == "false" or any(
                            operation["operation"] == "config" and operation["result"].get("dd_trace_enabled") == "false"
                            for operation in library.operations)
                        if disabled:
                            with library.dd_start_span("upstream.configuration.suppression-control"):
                                pass
                            library.dd_flush()
                            agent.traces()
                            assert not any(snapshot for snapshot in agent.captures), "Disabled SDK emitted native traces"
                            result["evidenceKind"] = "trace-suppression"
                        else:
                            if not any(operation["operation"] in ("start", "otel_start", "mixed_context", "http_request") for operation in library.operations):
                                with library.dd_start_span("upstream.configuration.export-control"):
                                    pass
                                library.dd_flush()
                                agent.traces()
                                result["sdkExportControl"] = True
                            assert_sdk_evidence(library, agent)
                        result["status"] = "passed"
                        if any(operation["operation"] == "otel_start" for operation in library.operations):
                            result["capabilityNames"] = sorted(set(result["capabilityNames"] + ["otel_api"]))
                        extracted_w3c = any(operation["operation"] == "extract" and any(
                            key.lower() == "traceparent" for key, _ in operation["arguments"]["headers"])
                            for operation in library.operations)
                        injected_w3c = any(operation["operation"] == "inject" and "traceparent" in operation["result"]
                            for operation in library.operations)
                        if extracted_w3c and injected_w3c:
                            result["capabilityNames"] = sorted(set(result["capabilityNames"] + ["w3c_headers_injection_and_extraction"]))
                    except Exception:
                        result.update(status="failed", detail=traceback.format_exc())
                        print(result["detail"], file=sys.stderr, flush=True)
                    finally:
                        capture = json.dumps(agent.captures, sort_keys=True).encode()
                        stem = digest(case["name"].encode())[:16]
                        capture_name = stem + ".capture.json"
                        (out / capture_name).write_bytes(capture)
                        operation_bytes = json.dumps(library.operations, sort_keys=True).encode()
                        operation_name = stem + ".operations.json"
                        (out / operation_name).write_bytes(operation_bytes)
                        result.update(captureFile=capture_name, captureSha256=digest(capture),
                                      operationsFile=operation_name, operationsSha256=digest(operation_bytes))
                        result["artifacts"] = [{"file": operation_name, "sha256": digest(operation_bytes)}]
                        results.append(result)
                        print(args.wire, result["status"], case["name"], flush=True)
    finally:
        results.sort(key=lambda result: result["name"])
        (out / "datadog-upstream-results.json").write_text(json.dumps({"schemaVersion": 1, "sourceRevision": REVISION,
            "wire": args.wire, "results": results}, indent=2) + "\n")
    assert len(results) == len(cases), (len(results), len(cases))
    failed = [result["name"] for result in results if result["status"] == "failed"]
    assert not failed, f"{len(failed)} upstream cases failed: {failed}"
    print(f"{sum(result['status'] == 'passed' for result in results)} passed; "
          f"{sum(result['status'] == 'unsupported' for result in results)} explicitly unsupported", flush=True)


def main():
    if not __debug__:
        raise RuntimeError("Upstream assertions require Python optimization disabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--wire", choices=["v0.4", "v0.5"], required=True)
    for name in ("launcher", "rootfs", "app", "vendor"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    parser.add_argument("--select")
    args = parser.parse_args()
    for name in ("launcher", "rootfs", "app", "vendor"):
        setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1])
        if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    execute(args)


if __name__ == "__main__":
    main()
