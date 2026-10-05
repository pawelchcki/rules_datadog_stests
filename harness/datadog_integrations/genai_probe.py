"""Runtime-verify google-genai APM and LLMObs spans with a real trace-agent."""
import json
from pathlib import Path
import time

from harness.datadog_agent.probe import AGENT_VERSION, server_thread
from harness.datadog_backend.backend import decompress
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_integrations import genai_assertions as assertions
from harness.datadog_integrations import genai_api
from harness.datadog_integrations.lab import (AgentLab, REVISION, base_parser, output_dir, receipt, resolve_args,
                                              attach_artifacts, write_results)
from harness.datadog_telemetry.probe import sha

SDK_PATH = "/evp_proxy/v2/api/v2/llmobs"
SOURCE_APM = "333d16b94b557fc4cce374e41da15f4da16162c22fec5681fa4454190b028865"
SOURCE_LLM = "7a77b5bf59501c4fbab27ffa8b22a1a170756bdc2badc61d9fbe64078275a5ed"


def wait_backend_spans(lab, expected, deadline_s=12):
    """Poll the agent backend until `expected` google_genai.request spans arrived."""
    deadline = time.monotonic() + deadline_s
    spans = []
    while time.monotonic() < deadline:
        spans = [span for record in lab.backend.snapshot() if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                 for chunk in trace_chunks(record["payload"]) for span in chunk["spans"]]
        if len([s for s in spans if s["name"] == "google_genai.request"]) == expected:
            return spans
        time.sleep(0.1)
    raise AssertionError("backend spans incomplete: " + repr(spans))


def run_case(args, lab, api, out, mode):
    case_dir = out / mode
    first_api = len(api.snapshot())
    api_url = "http://127.0.0.1:" + str(api.server_port)
    env_extra = {"DD_SERVICE": "genai-lab", "DD_ENV": "genai-env", "DD_VERSION": "genai-version",
                 "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]', "DD_LLMOBS_AGENTLESS_ENABLED": "false",
                 "DD_APM_TRACING_ENABLED": str(mode == "apm").lower(), "_DD_LLMOBS_WRITER_INTERVAL": "3600",
                 "DD_TRACE_API_VERSION": "v0.4"}
    tracer_records, env = lab.run_workload(case_dir, [args.app, "--identity-file", str(case_dir / "identity.json"),
                                                      "--sdk-overlay", args.sdk_overlay, "--api-url", api_url,
                                                      "--mode", mode], env_extra, "datadog-genai-" + mode)
    identity = json.loads((case_dir / "identity.json").read_text())
    api_records = api.snapshot()[first_api:]
    assertions.check_client(identity, api_records)
    assert all(record["status"] == 200 for record in tracer_records), tracer_records
    spans, native_spans = [], []
    if mode == "apm":
        for request in tracer_records:
            if request["path"] == "/v0.4/traces":
                raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
                assert sha(raw) == request["raw_sha256"]
                native_spans.extend(span for trace in msgpack(raw) for span in trace)
        expected = len([call for call in identity["calls"]])
        assert len([s for s in native_spans if s["name"] == "google_genai.request"]) == expected, native_spans
        (case_dir / "native-spans.json").write_text(json.dumps(native_spans, indent=2) + "\n")
        spans = wait_backend_spans(lab, expected)
    else:
        for request in [r for r in tracer_records if r["path"] == SDK_PATH]:
            matches = [r for r in lab.backend.snapshot() if r["path"] == "/api/v2/llmobs"
                       and r["raw_sha256"] == request["raw_sha256"]]
            assert len(matches) == 1 and matches[0]["status"] == 200, matches
            forwarded = matches[0]
            assert forwarded["headers"]["via"] == "trace-agent " + AGENT_VERSION
            assert forwarded["headers"]["x-datadog-hostname"] == "integrations-lab"
            assert forwarded["headers"]["x-datadog-agentdefaultenv"] == "integrations-lab-env"
            raw = (case_dir / "tracer" / request["raw_file"]).read_bytes()
            assert raw == (out / "backend" / forwarded["raw_file"]).read_bytes()
            document = json.loads(decompress(raw, request["headers"].get("content-encoding", "")))
            assert document == forwarded["payload"]
            spans.extend(span for batch in document for span in batch["spans"])
        assert len(spans) == len(identity["calls"]), spans
    (case_dir / "events.json").write_text(json.dumps(spans, indent=2) + "\n")
    return identity, spans, native_spans, tracer_records, env


def execute(args, out):
    results = []
    with AgentLab(args, out) as lab:
        api = genai_api.server(out / "api")
        with server_thread(api):
            try:
                for mode in ("apm", "llmobs"):
                    identity, spans, native_spans, tracer_records, env = run_case(args, lab, api, out, mode)
                    for feature, check, cases in assertions.CHECKS[mode]:
                        result = receipt(mode + ":" + feature, [feature], env, mode + "/tracer/requests.json",
                                         sha((out / mode / "tracer" / "requests.json").read_bytes()), out,
                                         agentVersion=AGENT_VERSION, clientVersion=identity["client_version"],
                                         sourceSha256=SOURCE_APM if mode == "apm" else SOURCE_LLM,
                                         missingAssertions=assertions.MISSING[feature],
                                         workloadSha256=sha(Path(args.app).read_bytes()),
                                         source="https://github.com/DataDog/system-tests/blob/" + REVISION
                                                + "/tests/integration_frameworks/llm/google_genai/test_google_genai_"
                                                + ("apm.py" if mode == "apm" else "llmobs.py"))
                        attach_artifacts(result, out, [mode + "/identity.json", mode + "/events.json",
                                                       "datadog.yaml", "agent-info.json"])
                        result["artifacts"].extend({"file": mode + "/tracer/" + r["raw_file"], "sha256": r["raw_sha256"]}
                                                   for r in tracer_records)
                        results.append(result)
                        try:
                            check(spans, identity, cases) if cases else check(spans, identity)
                            if mode == "apm":
                                check(native_spans, identity, cases) if cases else check(native_spans, identity)
                                result["artifacts"].append({"file": mode + "/native-spans.json",
                                                            "sha256": sha((out / mode / "native-spans.json").read_bytes())})
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
                write_results(out, "datadog-google-genai-results.json", results)
    assert len(results) == 6


def main():
    if not __debug__:
        raise RuntimeError("google-genai assertions require Python optimization disabled")
    args = resolve_args(base_parser().parse_args())
    execute(args, output_dir())


if __name__ == "__main__":
    main()
