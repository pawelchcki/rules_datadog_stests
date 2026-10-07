"""Run the same pinned upstream methods in fresh Python and Go SDK processes."""
import argparse
from contextlib import contextmanager, ExitStack
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "upstream_lab"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from adapter import AgentIntake, Library, load_cases
from probe import assert_sdk_evidence, BASE_ENV, resolve
from expected_failures import failure_signature, matches_expected_failure, EXPECTED_FAILURES
from portable_cases import cases as portable_cases
from agent_proxy import native_events_agent
from sdk_expected_failures import local_failure_reason
from baggage_cases import adapt_case

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
SDK_VERSIONS = {"python": "4.15.5", "go": "2.10.1"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(out, name, value):
    data = json.dumps(value, sort_keys=True).encode()
    (out / name).write_bytes(data)
    return {"file": name, "sha256": sha(data)}


@contextmanager
def application(args, env, out, phase):
    ready = out / (phase + ".port")
    ready.unlink(missing_ok=True)
    log_path = out / (phase + ".app.log")
    env = dict(env)
    if args.language == "go":
        env["DD_TRACE_STARTUP_LOGS"] = "true"
        # This spelling is the Go SDK's documented B3 single-header style.
        for key in ("DD_TRACE_PROPAGATION_STYLE", "DD_TRACE_PROPAGATION_STYLE_EXTRACT", "DD_TRACE_PROPAGATION_STYLE_INJECT"):
            if key in env:
                env[key] = ",".join("b3 single header" if value.lower() == "b3" else value
                                    for value in env[key].split(","))
        command = [args.app, "--ready-file", str(ready)]
    else:
        command = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs,
                   "--instance=shared-sdk-" + sha(phase.encode())[:16]]
        command += args.injection_flags
        command += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
        command += ["--", args.app, "--ready-file", str(ready)]
    process_env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
    if args.language == "go":
        process_env.update(env)
    with ExitStack() as stack:
        if env.get("DD_TRACE_NATIVE_SPAN_EVENTS") == "1" and args.language == "go":
            env["DD_TRACE_AGENT_URL"] = stack.enter_context(native_events_agent(
                env["DD_TRACE_AGENT_URL"], out / (phase + ".agent-info.json")))
            process_env.update(env)
        log = stack.enter_context(log_path.open("wb"))
        proc = subprocess.Popen(command, env=process_env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert proc.poll() is None, log_path.read_text(errors="replace")
                assert time.monotonic() < deadline, "SDK readiness timeout"
                time.sleep(0.02)
            url = "http://127.0.0.1:" + ready.read_text().strip()
            while True:
                try:
                    with urlopen(url + "/healthz", timeout=5) as response:
                        health = json.load(response)
                    break
                except OSError:
                    assert proc.poll() is None, log_path.read_text(errors="replace")
                    assert time.monotonic() < deadline, "SDK health timeout"
                    time.sleep(0.02)
            assert health["ready"] and health["ddtraceVersion"] == SDK_VERSIONS[args.language], health
            library = Library(url)
            library.lang = args.language
            library.sdk_version = health["ddtraceVersion"]
            library.log_path = log_path
            yield library, env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def execute_method(case, library, agent):
    values = dict(case["parameters"], test_library=library, test_agent=agent)
    signature = inspect.signature(case["function"])
    case["function"](case["instance"], **{key: values[key] for key in signature.parameters if key != "self"})
    library.dd_flush()
    agent.traces()
    if case["name"] in ("test_config_consistency.Test_Config_TraceEnabled.test_tracing_disabled[0]",
                         "test_otel_sdk_disabled.Test_OTEL_SDK_DISABLED.test_stable_true[0]",
                         "test_otel_traces_exporter.Test_OTEL_TRACES_EXPORTER.test_none_exporter[0]"):
        # The original method proves suppression. Its empty intake is paired
        # with the separately retained healthy baseline process.
        assert not any(record["payload"]["traces"] for snapshot in agent.captures for record in snapshot)
        return
    if not any(operation["operation"] in ("start", "otel_start", "http_request") for operation in library.operations):
        with library.dd_start_span("shared.configuration.export-control"):
            pass
        library.dd_flush()
        agent.traces()
    assert_sdk_evidence(library, agent)


def manifest_reason(case, manifest):
    """Use applicable pinned skip rules, as upstream does; prefer a specific reason."""
    node = case.get("upstreamSelector", "tests/parametric/" + case["file"] + "::" + case["class"] + "::" + case["method"])
    matches = [entry for entry in manifest.get("declarations", []) if entry["reason"]
               and (node == entry["selector"] or node.startswith(entry["selector"] + "::"))]
    declaration = max(matches, key=lambda entry: len(entry["selector"])) if matches else None
    return declaration


def expected_failure(case, error, library, agent, reason, source_hash, wire):
    signature = failure_signature(error)
    if library.lang == "python" and matches_expected_failure(case["name"], library.sdk_version, wire,
                                                            signature, agent.captures, library.operations, source_hash):
        return EXPECTED_FAILURES[case["name"]][-1]
    if library.lang == "python":
        local_reason = local_failure_reason(case["name"], library.sdk_version, wire, signature,
                                            agent.captures, library.operations, source_hash)
        if local_reason:
            return local_reason
    # A manifest is not permission to swallow startup, RPC, transport, source
    # validation or adapter errors. The unchanged upstream assertion must fail.
    upstream_assertion = (signature["file"] in (Path(case["file"]).name, "trace.py")
                          and signature["type"] in ("AssertionError", "KeyError", "ValueError"))
    missing_trace = (signature["file"] == "adapter.py" and signature["type"] == "ValueError"
                     and signature["function"] == "wait_for_num_traces"
                     and "traces not available from test agent" in signature["message"]
                     and any(Path(frame.filename).name == Path(case["file"]).name
                             for frame in traceback.extract_tb(error.__traceback__)))
    return reason["reason"] if reason and (upstream_assertion or missing_trace) else None


def execute(args):
    assert args.wire == "v0.4", "The shared SDK lab currently proves v0.4 only"
    out = Path(args.output or os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    vendor = Path(args.vendor)
    if vendor.is_file():
        vendor = vendor.parent
    manifest = json.loads((vendor / "manifest.json").read_text())
    assert manifest["revision"] == REVISION
    for name, entry in manifest["files"].items():
        assert sha((vendor / name).read_bytes()) == entry["sha256"], name
    registry = json.loads(Path(args.registry).read_text())
    entries = {entry["name"]: entry for entry in registry}
    assert len(entries) == len(registry), "Duplicate shared SDK case names"
    selected = list(entries.values()) if args.case is None else [entries[args.case]]
    upstream = {case["name"]: case for case in [adapt_case(case) for case in load_cases(vendor)] + portable_cases()}
    manifest_path = Path(args.registry).with_name("go-manifest.json")
    sdk_manifest_path = Path(args.registry).with_name(args.language + "-manifest.json")
    sdk_manifest = json.loads(sdk_manifest_path.read_text())
    assert sdk_manifest["revision"] == REVISION and sdk_manifest["sdkVersion"] == SDK_VERSIONS[args.language]
    manifest_source = manifest_path.with_name(sdk_manifest["sourceFile"]) if args.language == "go" else vendor / sdk_manifest["sourceFile"]
    assert sha(manifest_source.read_bytes()) == sdk_manifest["sourceSha256"]
    if args.sink:
        sink = args.sink
    else:
        ports = json.loads(os.environ["ASSIGNED_PORTS"])
        matches = [value for key, value in ports.items() if key.endswith("//harness:otel_sink_service")]
        assert len(matches) == 1, ports
        sink = "http://127.0.0.1:" + str(matches[0])
    results = []
    for entry in selected:
        case = upstream[entry["method"]]
        env = dict(BASE_ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION=args.wire,
                   DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="true")
        for key, value in case["parameters"].get("library_env", {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        env.update(entry.get("environment", {}))
        result = dict(name=entry["name"], language=args.language, application="shared-sdk",
                      wire=args.wire, sdkVersion=SDK_VERSIONS[args.language], configuration=env,
                      capabilityNames=entry["features"], capabilityInventoryRevision=REVISION,
                      sourceRevision=REVISION, sourceMethod=case["name"],
                      sourceSha256=sha(Path(inspect.getsourcefile(case["function"])).read_bytes()) if case.get("local") else manifest["files"][case["file"]]["sha256"],
                      source="https://github.com/DataDog/system-tests/blob/" + REVISION + "/tests/parametric/" + case["file"],
                      repetitions=2, artifacts=[])
        if case.get("local"):
            result.update(origin="local", source="harness/" + ("upstream_lab/" if case["file"] == "local_cases.py" else "shared_sdk/") + case["file"], sourceRevision=None)
            adapter_file = case["file"] if case.get("adaptedFrom") else "portable_cases.py"
            result["adapterSha256"] = sha(Path(__file__).with_name(adapter_file).read_bytes())
        if case.get("adaptedFrom"):
            result["adaptedFrom"] = case["adaptedFrom"]
        stem = sha(entry["name"].encode())[:16]
        agent = AgentIntake(sink, args.wire)
        reason = manifest_reason(case, sdk_manifest)
        if reason:
            result["upstreamManifest"] = dict(reason, sourceSha256=sdk_manifest["sourceSha256"], revision=REVISION)
        try:
            # The baseline is a separate, healthy process with unconfigured SDK
            # defaults. Disabled or broken test configurations cannot erase it.
            baseline_env = dict(BASE_ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION=args.wire)
            agent.clear()
            with application(args, baseline_env, out, stem + ".baseline") as (library, _):
                if args.language == "go":
                    assert library.config()["sdk_version"] == SDK_VERSIONS["go"], "Effective Go SDK version differs from pin"
                with library.dd_start_span("shared.baseline.control"):
                    pass
                library.dd_flush()
                agent.traces()
                assert_sdk_evidence(library, agent)
            artifact = save(out, stem + ".baseline.capture.json", agent.captures)
            result["baselineSha256"] = artifact["sha256"]
            result["artifacts"].append(artifact)
            result["artifacts"].append(save(out, stem + ".baseline.operations.json", library.operations))
            outcomes = []
            for phase in ("primary", "repeat"):
                agent = AgentIntake(sink, args.wire)
                agent.clear()
                library = None
                try:
                    with application(args, env, out, stem + "." + phase) as (library, effective):
                        result["configuration"] = effective
                        try:
                            execute_method(case, library, agent)
                            outcomes.append("passed")
                        except Exception as error:
                            library.dd_flush()
                            agent.traces()
                            matched_reason = expected_failure(case, error, library, agent, reason,
                                                              result["sourceSha256"], args.wire)
                            if not matched_reason:
                                raise
                            # Prove export health after a manifest-backed failure.
                            control_offset = len(library.operations)
                            if effective.get("DD_LAB_HTTP_MODE") == "true":
                                library.rpc("http_request", status=200, headers={}, query={})
                            else:
                                with library.dd_start_span("shared.expected-failure.export-control"):
                                    pass
                            library.dd_flush()
                            agent.traces()
                            original_operations = library.operations
                            try:
                                library.operations = original_operations[control_offset:]
                                assert_sdk_evidence(library, agent)
                            finally:
                                library.operations = original_operations
                            outcomes.append("unsupported")
                            result.setdefault("expectedFailures", []).append(dict(
                                phase=phase, signature=failure_signature(error),
                                reason=matched_reason))
                finally:
                    artifact = save(out, stem + "." + phase + ".capture.json", agent.captures)
                    operation_artifact = save(out, stem + "." + phase + ".operations.json", library.operations if library else [])
                    result["artifacts"].append(operation_artifact)
                    info_file = out / (stem + "." + phase + ".agent-info.json")
                    if info_file.exists():
                        result["artifacts"].append({"file": info_file.name, "sha256": sha(info_file.read_bytes())})
                    if phase == "primary":
                        result.update(captureFile=artifact["file"], captureSha256=artifact["sha256"])
                    else:
                        result.update(repeatCaptureFile=artifact["file"], repeatCaptureSha256=artifact["sha256"])
                        result["artifacts"].append(artifact)
            result["runOutcomes"] = outcomes
            # Some manifested defects are nondeterministic (for example a map
            # iteration affects the baggage byte limit). Neither mixed outcome
            # nor two expected failures supplies verified capability evidence.
            result["status"] = "unsupported" if "unsupported" in outcomes else "passed"
            if reason and result["status"] == "passed":
                result["manifestXpass"] = True
        except Exception:
            result.update(status="failed", detail=traceback.format_exc())
            print(result["detail"], file=sys.stderr, flush=True)
        results.append(result)
        print(args.language, result["status"], entry["name"], flush=True)
        (out / "datadog-shared-sdk-results.json").write_text(json.dumps({"results": results}, indent=2) + "\n")
    assert all(result["status"] in ("passed", "unsupported") for result in results), "Shared SDK assertions failed"


def main():
    if not __debug__:
        raise RuntimeError("SDK assertions require optimization disabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", choices=SDK_VERSIONS, required=True)
    parser.add_argument("--wire", default="v0.4")
    for name in ("app", "vendor", "registry"):
        parser.add_argument("--" + name, required=True)
    for name in ("case", "launcher", "rootfs", "sink", "output"):
        parser.add_argument("--" + name)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for name in ("app", "vendor", "registry", "launcher", "rootfs"):
        if getattr(args, name):
            setattr(args, name, resolve(getattr(args, name)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1])
                           if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    execute(args)


if __name__ == "__main__":
    main()
