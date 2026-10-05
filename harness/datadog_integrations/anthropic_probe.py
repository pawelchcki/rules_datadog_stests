"""Observe official Anthropic client instrumentation across both real Agent paths."""
import argparse
import json
import time
from pathlib import Path

from harness.datadog_agent.probe import AGENT_VERSION, server_thread
from harness.datadog_backend.backend import decompress
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_integrations import anthropic_api, anthropic_assertions
from harness.datadog_integrations.lab import (AgentLab, REVISION, attach_artifacts, base_parser,
                                              output_dir, receipt, resolve_args, write_results)
from harness.datadog_telemetry.probe import sha

SDK_PATH = "/evp_proxy/v2/api/v2/llmobs"
SOURCE = ("https://github.com/DataDog/system-tests/blob/" + REVISION
          + "/tests/integration_frameworks/llm/anthropic/test_anthropic_")
SOURCE_HASHES = {
    "apm": "ca6b808a9dcf5046e583c21797203a72043fce9688505b4a05f68fdc3ccaed30",
    "llmobs": "a09d619f044e70688217281739c959c80d1aec302f6745ca173ba54dd1c7ef0f",
}
MISSING = {
    # Upstream test_create/test_create_stream_method assert only name, resource
    # and the model tag; every one of those is covered here plus linkage,
    # delivery and error shape, so nothing upstream asserts is skipped.
    "apm": [],
    # Upstream also asserts tool calls, tool results, multiple/system prompts,
    # image redaction and prompt-caching token breakdowns; the pinned SDK paths
    # for those are not exercised by this workload.
    "llmobs": ["tool calls", "tool results", "system prompts", "image redaction", "prompt caching"],
}


def run_case(lab, args, api, out, mode):
    case_dir = out / mode
    first_api = len(api.snapshot())
    env_extra = {"DD_SERVICE": "anthropic-lab", "DD_ENV": "anthropic-env", "DD_VERSION": "anthropic-version",
                 "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]', "DD_LLMOBS_AGENTLESS_ENABLED": "false",
                 "DD_APM_TRACING_ENABLED": str(mode == "apm").lower(), "_DD_LLMOBS_WRITER_INTERVAL": "3600",
                 "DD_TRACE_API_VERSION": "v0.4"}
    app_args = [args.app, "--identity-file", str(case_dir / "identity.json"), "--sdk-overlay", args.sdk_overlay,
                "--api-url", "http://127.0.0.1:" + str(api.server_port), "--mode", mode]
    tracer_records, env = lab.run_workload(case_dir, app_args, env_extra, "datadog-anthropic-" + mode)
    identity = json.loads((case_dir / "identity.json").read_text())
    api_records = api.snapshot()[first_api:]
    anthropic_assertions.check_client(identity, api_records)
    assert all(record["status"] == 200 for record in tracer_records), tracer_records
    spans, native_spans = [], []
    if mode == "apm":
        for request in tracer_records:
            if request["path"] == "/v0.4/traces":
                raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
                assert sha(raw) == request["raw_sha256"]
                native_spans.extend(span for trace in msgpack(raw) for span in trace)
        assert len([s for s in native_spans if s["name"] == "anthropic.request"]) == 4, native_spans
        (case_dir / "native-spans.json").write_text(json.dumps(native_spans, indent=2) + "\n")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            spans = [span for record in lab.backend.snapshot() if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                     for chunk in trace_chunks(record["payload"]) for span in chunk["spans"]]
            if len([s for s in spans if s["name"] == "anthropic.request"]) == 4:
                break
            time.sleep(0.1)
        assert len([s for s in spans if s["name"] == "anthropic.request"]) == 4, spans
    else:
        for request in [r for r in tracer_records if r["path"] == SDK_PATH]:
            matches = [r for r in lab.backend.snapshot()
                       if r["path"] == "/api/v2/llmobs" and r["raw_sha256"] == request["raw_sha256"]]
            assert len(matches) == 1 and matches[0]["status"] == 200, matches
            forwarded = matches[0]
            assert forwarded["headers"]["via"] == "trace-agent " + AGENT_VERSION
            assert forwarded["headers"]["x-datadog-hostname"] == "anthropic-agent"
            assert forwarded["headers"]["x-datadog-agentdefaultenv"] == "anthropic-agent-env"
            raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
            assert raw == (out / "backend" / forwarded["raw_file"]).read_bytes()
            document = json.loads(decompress(raw, request["headers"].get("content-encoding", "")))
            assert document == forwarded["payload"]
            spans.extend(span for batch in document for span in batch["spans"])
        assert len(spans) == 4, spans
    (case_dir / "events.json").write_text(json.dumps(spans, indent=2) + "\n")
    return identity, spans, native_spans, tracer_records, env


def execute(args, out):
    results = []
    api = anthropic_api.server(out / "api")
    with server_thread(api), AgentLab(args, out, hostname="anthropic-agent") as lab:
        try:
            for mode in ("apm", "llmobs"):
                case_dir = out / mode
                identity, spans, native_spans, tracer_records, env = run_case(lab, args, api, out, mode)
                feature, check = anthropic_assertions.CHECKS[mode]
                result = receipt(feature, [feature], env, mode + "/tracer/requests.json",
                                 sha((case_dir / "tracer" / "requests.json").read_bytes()), out,
                                 clientVersion=identity["client_version"], sourceSha256=SOURCE_HASHES[mode],
                                 workloadSha256=sha(Path(args.app).read_bytes()), missingAssertions=MISSING[mode],
                                 source=SOURCE + ("apm.py" if mode == "apm" else "llmobs.py"))
                files = [mode + "/identity.json", mode + "/events.json", "datadog.yaml", "agent-info.json"]
                if mode == "apm":
                    files.append(mode + "/native-spans.json")
                attach_artifacts(result, out, files)
                result["artifacts"].extend({"file": mode + "/tracer/" + r["raw_file"], "sha256": r["raw_sha256"]}
                                           for r in tracer_records)
                results.append(result)
                try:
                    check(spans, identity)
                    if mode == "apm":
                        check(native_spans, identity)
                    result["status"] = "passed"
                    print(result["name"], "passed", flush=True)
                except Exception as error:
                    result["detail"] = repr(error)
                    raise
        finally:
            for label, records in (("api", api.snapshot()), ("backend", lab.backend.snapshot())):
                path = out / (label + "-capture.json")
                path.write_text(json.dumps(records, indent=2) + "\n")
                for result in results:
                    result["artifacts"].append({"file": path.name, "sha256": sha(path.read_bytes())})
                    result["artifacts"].extend({"file": label + "/" + r["raw_file"], "sha256": r["raw_sha256"]}
                                               for r in records)
            write_results(out, "datadog-anthropic-results.json", results)
    assert len(results) == 2


def main():
    if not __debug__:
        raise RuntimeError("Anthropic assertions require Python optimization disabled")
    parser = base_parser()
    args = resolve_args(parser.parse_args())
    execute(args, output_dir())


if __name__ == "__main__":
    main()
