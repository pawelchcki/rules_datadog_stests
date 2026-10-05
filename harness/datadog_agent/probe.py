"""Prove SDK → real APM Agent → local backend behavior with both boundaries retained."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from harness.datadog_backend.backend import API_KEY, BackendServer
from harness.datadog_backend.wire import trace_chunks
from harness.datadog_agent.proxy import IntakeProxy

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
AGENT_VERSION = "7.83.1"
AGENT_DIGEST = "sha256:ed0bd588e955d82f661d1b8dd1cdf179c1023e74a2817e7a812c99d52f05c319"
CASES = [
    {"name": "kept", "env": {}, "priority": 2, "exported": "all"},
    {"name": "dropped", "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0}]'}, "priority": -1, "exported": "none"},
    {"name": "single-span", "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0}]', "DD_SPAN_SAMPLING_RULES": '[{"service":"lab-service","sample_rate":1,"max_per_second":50}]'}, "priority": -1, "exported": "root", "normalized_name": "agent.lab.single_span"},
    {"name": "single-child", "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0}]', "DD_SPAN_SAMPLING_RULES": '[{"service":"lab-child","sample_rate":1,"max_per_second":50}]'}, "priority": -1, "exported": "child", "normalized_name": "agent.lab.single_child"},
    {"name": "tags", "env": {"DD_TAGS": "probe.agent:visible,probe.colon:alpha:beta"}, "priority": 2, "exported": "all", "tags": {"probe.agent": "visible", "probe.colon": "alpha:beta"}},
    {"name": "identity", "env": {"DD_SERVICE": "lab-service", "DD_ENV": "agent-lab-env", "DD_VERSION": "agent-lab-version"}, "priority": 2, "exported": "all", "tags": {"env": "agent-lab-env"}, "root_tags": {"version": "agent-lab-version"}},
]
BASE_ENV = {
    "DD_SERVICE": "datadog-agent-lab", "DD_ENV": "test", "DD_VERSION": "1",
    "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1}]', "DD_TRACE_RATE_LIMIT": "-1",
    "DD_TRACE_ENABLED": "true", "DD_TRACE_PARTIAL_FLUSH_ENABLED": "false",
    "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.05", "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": "true",
    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false", "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_RUNTIME_METRICS_ENABLED": "false", "DD_PROFILING_ENABLED": "false",
    "DD_APPSEC_ENABLED": "false", "DD_IAST_ENABLED": "false", "DD_SCA_ENABLED": "false",
    "DD_TRACE_COMPUTE_STATS": "false", "DD_TRACE_STATS_COMPUTATION_ENABLED": "false",
    "DD_TRACE_PROPAGATION_STYLE_EXTRACT": "none", "DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog",
    "DD_TRACE_STARTUP_LOGS": "false", "DD_LOGS_INJECTION": "false",
}


def get(url, method="GET"):
    with urlopen(Request(url, method=method), timeout=10) as response:
        return response.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def resolve(path):
    for candidate in [Path(path)] + [Path(root) / path for root in
            (os.environ.get("RUNFILES_DIR"), os.environ.get("TEST_SRCDIR")) if root]:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError(path)


@contextmanager
def server_thread(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@contextmanager
def process(command, log_path, env=None):
    with log_path.open("wb") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=log, env=env, start_new_session=True)
        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def wait_ready(proc, url, log_path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert proc.poll() is None, log_path.read_text(errors="replace")
        try:
            return json.loads(get(url))
        except URLError:
            time.sleep(0.1)
    raise AssertionError("readiness timeout: " + log_path.read_text(errors="replace"))


def agent_command(root):
    root = Path(root)
    return [str(root / "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"),
            "--library-path", str(root / "opt/datadog-agent/embedded/lib") + ":" + str(root / "usr/lib/x86_64-linux-gnu"),
            str(root / "opt/datadog-agent/embedded/bin/trace-agent")]


@contextmanager
def core_agent(args, config, out, env, health_port):
    if not getattr(args, "core_agent", False):
        yield None
        return
    command = agent_command(args.agent_rootfs)
    command[-1] = str(Path(args.agent_rootfs) / "opt/datadog-agent/bin/agent/agent")
    core_env = dict(env, LD_LIBRARY_PATH=str(Path(args.agent_rootfs) / "opt/datadog-agent/embedded/lib"),
                    PYTHONHOME=str(Path(args.agent_rootfs) / "opt/datadog-agent/embedded"),
                    HTTP_PROXY=args.backend_url, HTTPS_PROXY=args.backend_url,
                    NO_PROXY="127.0.0.1,localhost")
    log = out / "core-agent.log"
    with process(command + ["run", "--cfgpath=" + str(config)], log, core_env) as proc:
        wait_ready(proc, "http://127.0.0.1:" + str(health_port) + "/live", log)
        yield proc


def native_spans(capture, wire):
    records = json.loads(capture)
    assert records and all(record["payload"]["wire_version"] == wire for record in records), records
    return [span for record in records for chunk in record["payload"]["traces"] for span in chunk]


def assert_delivery(spans, chunks, identity, case):
    """Assert every retained native span against the separately decoded backend."""
    root_id, child_id = int(identity["root_id"]), int(identity["child_id"])
    full = int(identity["trace_id"])
    low, high = full & ((1 << 64) - 1), f"{full >> 64:016x}"
    intake = {int(span["span_id"]): span for span in spans}
    assert len(spans) == len(intake) == 2 and set(intake) == {root_id, child_id}, spans
    assert all(int(span["trace_id"]) == low for span in spans), spans
    assert intake[root_id].get("parent_id", 0) == 0 and intake[child_id]["parent_id"] == root_id, spans
    assert intake[root_id].get("meta", {}).get("_dd.p.tid") == high, spans
    assert intake[root_id]["metrics"]["_sampling_priority_v1"] == case["priority"], spans
    assert identity["priority"] == case["priority"], identity
    assert intake[root_id]["meta"].get("language") == "python", intake[root_id]
    runtime = intake[root_id]["meta"]["runtime-id"]
    assert uuid.UUID(runtime).hex == runtime.replace("-", ""), runtime
    assert int(intake[root_id]["metrics"]["process_id"]) > 0, intake[root_id]
    wanted = {root_id, child_id} if case["exported"] == "all" else {root_id} if case["exported"] == "root" else {child_id} if case["exported"] == "child" else set()
    relevant = [span for chunk in chunks for span in chunk["spans"] if span.get("trace_id") == low]
    assert len(relevant) == len(wanted) and {span["span_id"] for span in relevant} == wanted, (case, relevant)
    for span in relevant:
        original = intake[span["span_id"]]
        for key in ("trace_id", "span_id", "service", "resource", "start", "duration"):
            assert span[key] == original[key], (key, span, original)
        expected_name = case.get("normalized_name", original["name"]) if span["span_id"] == root_id else original["name"]
        assert span["name"] == expected_name, (span, expected_name)
        for key in ("parent_id", "error"):
            assert span.get(key, 0) == original.get(key, 0), (key, span, original)
        for key, value in case.get("tags", {}).items():
            assert span.get("meta", {}).get(key) == value, (key, span)
        if span["span_id"] == root_id:
            assert span.get("meta", {}).get("_dd.p.tid") == high, span
            for key in ("runtime-id", "language"):
                assert span.get("meta", {}).get(key) == original["meta"][key], (key, span)
            assert span.get("metrics", {}).get("process_id") == original["metrics"]["process_id"], span
            for key, value in case.get("root_tags", {}).items():
                assert span.get("meta", {}).get(key) == value, (key, span)
    if case["exported"] in ("root", "child"):
        selected = root_id if case["exported"] == "root" else child_id
        for observed in (intake[selected], relevant[0]):
            assert observed["metrics"].get("_dd.span_sampling.mechanism") == 8, observed
            assert observed["metrics"].get("_dd.span_sampling.rule_rate") == 1, observed
            assert observed["metrics"].get("_dd.span_sampling.max_per_second") == 50, observed


def stats_rows(records):
    return [row for record in records if record["path"] == "/api/v0.2/stats" and record["status"] == 200
            for client in record["payload"].get("Stats", [])
            for bucket in client.get("Stats", []) for row in bucket.get("Stats", [])]


def assert_stats(records, observations):
    rows = stats_rows(records)
    assert rows, "Agent must export independently decoded trace statistics"
    for case, env, identity, spans, _ in observations:
        root = next(span for span in spans if span["span_id"] == int(identity["root_id"]))
        matching = [row for row in rows if row["Resource"] == root["resource"] and row["Service"] == root["service"]]
        assert sum(row["Hits"] for row in matching) == 1, (case, matching)
        assert sum(row["Duration"] for row in matching) == root["duration"], (case, matching, root)
        assert sum(row["Errors"] for row in matching) == 0, (case, matching)
        assert all(row["Name"] == case.get("normalized_name", root["name"]) for row in matching), (case, matching)


def run_workload(args, proxy_url, sink, out, case, index):
    get(sink + "/reset?protocol=datadog", "POST")
    env = dict(BASE_ENV, DD_TRACE_AGENT_URL=proxy_url, DD_TRACE_API_VERSION=args.wire, **case["env"])
    if getattr(args, "core_agent", False):
        env.update(DD_DOGSTATSD_URL=args.dogstatsd_url, DD_RUNTIME_METRICS_ENABLED="true", DD_RUNTIME_METRICS_INTERVAL="0.25")
    ready = out / (case["name"] + ".port")
    ready.unlink(missing_ok=True)
    command = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
    command += ["--instance=datadog-agent-" + case["name"] + "-" + str(index)]
    command += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
    command += ["--", args.app, "--ready-file", str(ready)]
    proc_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
    log_path = out / (case["name"] + ".app.log")
    with process(command, log_path, proc_env) as proc:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert proc.poll() is None and time.monotonic() < deadline, log_path.read_text(errors="replace")
            time.sleep(0.1)
        app_url = "http://127.0.0.1:" + ready.read_text().strip()
        wait_ready(proc, app_url + "/healthz", log_path)
        if getattr(args, "core_agent", False):
            # Allow real periodic GC/CPU metrics to reach the UDP listener.
            time.sleep(0.6)
        identity_bytes = get(app_url + "/run?" + urlencode({"name": "agent.lab." + case["name"], "service": "lab-service"}))
        (out / (case["name"] + ".identity.json")).write_bytes(identity_bytes)
        identity = json.loads(identity_bytes)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            capture = get(sink + "/dump?protocol=datadog")
            if json.loads(capture):
                spans = native_spans(capture, args.wire)
                if len(spans) == 2:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("missing native tracer intake")
        (out / (case["name"] + ".capture.json")).write_bytes(capture)
        get(app_url + "/shutdown")
        assert proc.wait(timeout=10) == 0, log_path.read_text(errors="replace")
    return env, identity, spans, capture


def execute(args, sink, out):
    results = []
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    with server_thread(backend):
        # Bind a socket to discover a free port; startup failures never retry into
        # a passing result, and /info must identify the pinned Agent version.
        import socket
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        backend_url = "http://127.0.0.1:" + str(backend.server_port)
        args.backend_url = backend_url
        health_port = 0
        config_path = out / "datadog.yaml"
        config = (
            "api_key: '" + API_KEY + "'\nhostname: datadog-agent-lab\n"
            "dd_url: " + backend_url + "\n"
            "log_level: info\nlog_to_console: true\nlog_file: ''\n"
            "remote_configuration:\n  enabled: false\n"
            "agent_telemetry:\n  enabled: false\n"
            "apm_config:\n  enabled: true\n  receiver_port: " + str(port) + "\n"
            "  log_file: ''\n  telemetry:\n    enabled: false\n"
            "  debug:\n    port: 0\n"
            "  receiver_socket: ''\n  apm_non_local_traffic: false\n"
            "  apm_dd_url: " + backend_url + "\n"
            "  target_traces_per_second: 1000\n  errors_per_second: 0\n"
            "  max_memory: 0\n  max_cpu_percent: 0\n"
            "  trace_writer:\n    flush_period_seconds: 0.2\n"
            "  stats_writer:\n    flush_period_seconds: 1\n"
        )
        if getattr(args, "core_agent", False):
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                health_port = reservation.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                dogstatsd_port = reservation.getsockname()[1]
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                command_port = reservation.getsockname()[1]
            args.dogstatsd_url = "udp://127.0.0.1:" + str(dogstatsd_port)
            extra = {"health_port": health_port, "dogstatsd_port": dogstatsd_port, "cmd_port": command_port, "expvar_port": 0,
                "dogstatsd_socket": "", "health_platform": {"enabled": False},
                "proxy": {"http": backend_url, "https": backend_url, "no_proxy": ["127.0.0.1", "localhost"]},
                "enable_metadata_collection": False, "inventories_enabled": False, "cloud_provider_metadata": [],
                "run_path": str(out / "core-run"), "conf_path": str(out / "conf.d"),
                "auth_token_file_path": str(out / "auth_token"), "logs_enabled": False,
                "process_config": {"process_collection": {"enabled": False}, "container_collection": {"enabled": False},
                    "process_discovery": {"enabled": False}, "run_in_core_agent": False}}
            # Append top-level YAML-compatible JSON values without repeating
            # the APM block shared by the real core and trace components.
            config += "".join(key + ": " + json.dumps(value) + "\n" for key, value in extra.items())
            (out / "conf.d").mkdir(exist_ok=True)
        config_path.write_text(config)
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_"))}
        clean_env["DD_TRACE_AGENT_DD_URL"] = backend_url
        command = agent_command(args.agent_rootfs)
        version = subprocess.check_output(command + ["version"], env=clean_env, text=True)
        assert AGENT_VERSION in version, version
        (out / "agent-version.txt").write_text(version)
        log_path = out / "agent.log"
        with process(command + ["run", "--config=" + str(config_path)], log_path, clean_env) as proc, core_agent(args, config_path, out, clean_env, health_port) as core:
            info = wait_ready(proc, "http://127.0.0.1:" + str(port) + "/info", log_path)
            assert info["version"] == AGENT_VERSION, info
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            proxy = IntakeProxy("http://127.0.0.1:" + str(port), sink, out / "intake")
            with server_thread(proxy):
                observations = []
                try:
                    for index, case in enumerate(CASES):
                        env, identity, spans, capture = run_workload(args, "http://127.0.0.1:" + str(proxy.server_port), sink, out, case, index)
                        observations.append((case, env, identity, spans, capture))
                    # Wait for both trace and stats writers: a kept control proves
                    # this Agent/backend path exports while drop decisions are checked.
                    # Agent stats retain completed ten-second buckets before
                    # flushing. Separate graceful workloads can cross bucket
                    # boundaries, so allow both buckets to reach the backend.
                    deadline = time.monotonic() + 55
                    chunks = []
                    while time.monotonic() < deadline:
                        assert proc.poll() is None, log_path.read_text(errors="replace")
                        assert core is None or core.poll() is None, "core Agent exited"
                        records = backend.snapshot()
                        chunks = [chunk for record in records if record["path"] == "/api/v0.2/traces" and record["status"] == 200 for chunk in trace_chunks(record["payload"])]
                        delivered = {span["span_id"] for chunk in chunks for span in chunk["spans"]}
                        wanted = {int(identity[key]) for case, _, identity, _, _ in observations for key in (["root_id", "child_id"] if case["exported"] == "all" else ["root_id"] if case["exported"] == "root" else [])}
                        resources = {row["Resource"] for row in stats_rows(records)}
                        expected_resources = {identity["name"] for _, _, identity, _, _ in observations}
                        runtime_sketches = [sketch for record in records if record["path"] == "/api/beta/sketches" and record["status"] == 200 for sketch in record["payload"].get("sketches", [])]
                        runtime_ready = core is None or {"runtime.python.gc.count.gen0", "runtime.python.gc.count.gen1", "runtime.python.gc.count.gen2"} <= {
                            sketch["metric"] for sketch in runtime_sketches
                            if "service:datadog-agent-lab" in sketch.get("tags", [])
                        }
                        if wanted <= delivered and expected_resources <= resources and runtime_ready:
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Agent delivery incomplete: " + repr({"wanted": sorted(wanted), "delivered": sorted(delivered), "expectedResources": sorted(expected_resources), "resources": sorted(resources), "runtimeMetrics": [item["metric"] for item in runtime_sketches], "requests": [(item["path"], item["status"], item.get("error")) for item in records]}))
                    assert all(record["status"] == (403 if record["path"] == "/forbidden-connect" else 200) for record in records), [(record["path"], record["status"], record.get("error")) for record in records]
                    assert_stats(records, observations)
                    if core is not None:
                        for metric in ("runtime.python.gc.count.gen0", "runtime.python.gc.count.gen1", "runtime.python.gc.count.gen2"):
                            samples = [sketch for sketch in runtime_sketches if sketch["metric"] == metric and "service:datadog-agent-lab" in sketch.get("tags", [])]
                            assert samples and all(sketch["host"] == "datadog-agent-lab" for sketch in samples), (metric, runtime_sketches)
                            assert sum(value["cnt"] for sketch in samples for value in sketch.get("dogsketches", [])) > 0, samples
                    ledger = proxy.snapshot()
                    assert not ledger["errors"], ledger
                    requests = [record for record in ledger["records"] if record["path"] == "/" + args.wire + "/traces"]
                    assert len(requests) >= len(CASES) and all(record["status"] == 200 for record in requests), ledger
                    assert all("rate_by_service" in json.loads(record["responseBody"]) for record in requests), requests
                    required_headers = ("datadog-meta-tracer-version", "datadog-meta-lang", "datadog-meta-lang-interpreter", "datadog-meta-lang-version", "x-datadog-trace-count")
                    for request in requests:
                        headers = {key.lower(): value for key, value in request["requestHeaders"].items()}
                        assert all(headers.get(key) for key in required_headers), headers
                        assert headers["datadog-meta-lang"] == "python" and int(headers["x-datadog-trace-count"]) == 1, headers
                    assert len({identity["trace_id"] for _, _, identity, _, _ in observations}) == len(CASES), observations
                    for case, env, identity, spans, capture in observations:
                        assert_delivery(spans, chunks, identity, case)
                        results.append({"name": ("core-agent-" if core is not None else "agent-") + case["name"], "status": "passed", "wire": args.wire,
                            "configuration": env, "captureSha256": sha(capture), "captureFile": case["name"] + ".capture.json",
                            "agentVersion": AGENT_VERSION, "agentImageDigest": AGENT_DIGEST,
                            "backendCapture": "backend-capture.json", "traceId": identity["trace_id"],
                            "capabilityInventoryRevision": REVISION,
                            "capabilityNames": {"kept": ["trace_agent_connection", "trace_sampling", "trace_data_integrity", "agent_data_integrity", "runtime_id_in_span_metadata_for_service_entry_spans"], "dropped": ["trace_sampling"], "single-span": ["single_span_sampling", "single_span_ingestion_control"], "single-child": ["single_span_ingestion_control"], "tags": ["trace_global_tags"], "identity": ["unified_service_tagging"]}[case["name"]]})
                        if core is not None:
                            results[-1]["capabilityNames"] += ["runtime_metrics", "dogstatsd_agent_connection"]
                        print(args.wire, "agent-" + case["name"], "passed", flush=True)
                finally:
                    (out / "intake-proxy.json").write_text(json.dumps(proxy.snapshot(), indent=2) + "\n")
                    backend_bytes = json.dumps(backend.snapshot(), indent=2).encode() + b"\n"
                    (out / "backend-capture.json").write_bytes(backend_bytes)
                    for result in results:
                        result["backendCaptureSha256"] = sha(backend_bytes)
                        paths = ["backend-capture.json", "intake-proxy.json", "agent-info.json", "datadog.yaml", "agent-version.txt"]
                        paths += [case["name"] + ".identity.json" for case in CASES]
                        paths += [record["rawFile"] for record in proxy.snapshot()["records"]]
                        paths += ["backend/" + record["raw_file"] for record in backend.snapshot()]
                        result["artifacts"] = [{"file": path, "sha256": sha((out / path).read_bytes())} for path in paths]
                    (out / "datadog-agent-results.json").write_text(json.dumps({"schemaVersion": 1, "wire": args.wire, "results": results}, indent=2) + "\n")


def main():
    if not __debug__:
        raise RuntimeError("Agent assertions require optimization disabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--wire", choices=["v0.4", "v0.5"], required=True)
    parser.add_argument("--core-agent", action="store_true")
    for option in ("agent-rootfs", "launcher", "rootfs", "app"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("agent_rootfs", "launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    matches = [int(value) for key, value in ports.items() if key.endswith("//harness:otel_sink_service")]
    assert len(matches) == 1, ports
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, "http://127.0.0.1:" + str(matches[0]), out)


if __name__ == "__main__":
    main()
