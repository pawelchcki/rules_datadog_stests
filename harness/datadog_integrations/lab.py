"""Shared orchestration for integration labs: real Agent, launcher, captured SDK traffic.

Every cluster probe in this package reuses the same boundaries as
harness/datadog_openai: the pinned ddtrace 4.15.4 SDK runs inside the rootfs via
the app launcher, talks to the real trace-agent through a loopback capture
proxy, and the agent forwards to a local backend whose records are decoded
independently.
"""
import json
import os
from pathlib import Path
import socket
import ssl
import time

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer
from harness.datadog_backend.wire import trace_chunks
from harness.datadog_llmobs.connect import ConnectRelay
from harness.datadog_telemetry.probe import TelemetryBackendHandler, sha
from harness.datadog_telemetry.proxy import CaptureProxy

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
CERTIFICATE = Path(__file__).parent.parent / "datadog_llmobs/localhost-test.crt"


def base_parser():
    import argparse
    parser = argparse.ArgumentParser()
    for option in ("agent-rootfs", "launcher", "rootfs", "app", "sdk-overlay"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    parser.add_argument("--sdk-lock")
    return parser


def resolve_args(args):
    for key in ("agent_rootfs", "launcher", "rootfs", "app", "sdk_overlay"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    if args.sdk_lock:
        args.sdk_lock = resolve(args.sdk_lock)
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    return args


def output_dir(environ=None):
    out = Path((environ or os.environ)["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    return out


class AgentLab:
    """Runs the pinned trace-agent against a local backend, ready for workloads."""

    def __init__(self, args, out, hostname="integrations-lab"):
        self.args = args
        self.out = out
        self.hostname = hostname
        lock = Path(args.sdk_lock) if args.sdk_lock else Path.cwd() / "bazel/integrations_wheels.lock.json"
        if lock.exists():
            (out / "sdk-wheels.lock.json").write_bytes(lock.read_bytes())
        self.backend = BackendServer(("127.0.0.1", 0), out / "backend")
        self.backend.RequestHandlerClass = TelemetryBackendHandler
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(CERTIFICATE, CERTIFICATE.with_suffix(".key"))
        self.backend.socket = tls.wrap_socket(self.backend.socket, server_side=True)
        self.relay = ConnectRelay(self.backend.server_port)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.backend_url = "https://127.0.0.1:" + str(self.backend.server_port)
        self.config = {"api_key": API_KEY, "hostname": hostname, "env": hostname + "-env", "dd_url": self.backend_url,
            "log_level": "info", "log_to_console": True, "log_file": "", "cmd_port": 0,
            "proxy": {"https": "http://127.0.0.1:" + str(self.relay.server_port)},
            "evp_proxy_config": {"enabled": True, "dd_url": "backend.test"},
            "remote_configuration": {"enabled": False}, "agent_telemetry": {"enabled": False},
            "apm_config": {"enabled": True, "receiver_port": self.port, "receiver_socket": "", "log_file": "", "debug": {"port": 0},
                "apm_non_local_traffic": False, "apm_dd_url": self.backend_url, "telemetry": {"enabled": False},
                "trace_writer": {"flush_period_seconds": 0.2}, "max_memory": 0, "max_cpu_percent": 0}}
        (out / "datadog.yaml").write_text(json.dumps(self.config, indent=2) + "\n")

    def __enter__(self):
        self._servers = [self.backend, self.relay]
        self._threads = []
        for server in self._servers:
            self._threads.append(server_thread(server))
            self._threads[-1].__enter__()
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_"))}
        clean_env["SSL_CERT_FILE"] = str(CERTIFICATE)
        self._proc_cm = process(agent_command(self.args.agent_rootfs) + ["run", "--config=" + str(self.out / "datadog.yaml")], self.out / "agent.log", clean_env)
        self.proc = self._proc_cm.__enter__()
        self.agent_url = "http://127.0.0.1:" + str(self.port)
        info = wait_ready(self.proc, self.agent_url + "/info", self.out / "agent.log")
        assert info["version"] == AGENT_VERSION, info
        (self.out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
        return self

    def __exit__(self, *exc):
        self._proc_cm.__exit__(*exc)
        for thread in reversed(self._threads):
            thread.__exit__(*exc)

    def run_workload(self, case_dir, app_args, env_extra, instance, timeout=90):
        """Launch the app rootfs once and return (records, env) from the capture proxy."""
        case_dir = Path(case_dir)
        case_dir.mkdir(parents=True, exist_ok=True)
        proxy = CaptureProxy(self.agent_url, case_dir / "tracer")
        with server_thread(proxy):
            env = dict(BASE_ENV)
            env.update(env_extra)
            env["DD_TRACE_AGENT_URL"] = "http://127.0.0.1:" + str(proxy.server_port)
            launch = [self.args.launcher, "--runtime=python", "--rootfs=" + self.args.rootfs] + self.args.injection_flags
            launch += ["--instance=" + instance]
            launch += ["--env=" + key + "=" + value for key, value in sorted(env.items())]
            launch += ["--"] + app_args
            clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_")) and key != "PYTHONOPTIMIZE"}
            with process(launch, case_dir / "app.log", clean_env) as proc:
                assert proc.wait(timeout=timeout) == 0, (case_dir / "app.log").read_text(errors="replace")
        return proxy.snapshot(), env


def wait_backend_spans(lab, predicate, deadline_s=12):
    """Poll the agent backend until predicate(spans) is true; return spans."""
    deadline = time.monotonic() + deadline_s
    spans = []
    while time.monotonic() < deadline:
        spans = [span for record in lab.backend.snapshot() if record["path"] == "/api/v0.2/traces" and record["status"] == 200
                 for chunk in trace_chunks(record["payload"]) for span in chunk["spans"]]
        if predicate(spans):
            return spans
        time.sleep(0.1)
    raise AssertionError("backend spans incomplete: " + repr(spans))


def receipt(name, capabilities, env, capture_file, capture_sha, out, extra_artifacts=(), **fields):
    result = {"name": name, "status": "failed", "capabilityNames": capabilities, "capabilityInventoryRevision": REVISION,
        "configuration": env, "captureFile": capture_file, "captureSha256": capture_sha, "agentVersion": AGENT_VERSION,
        "artifacts": []}
    result.update(fields)
    lock = Path(out) / "sdk-wheels.lock.json"
    if lock.exists():
        result["sdkWheelLockSha256"] = sha(lock.read_bytes())
        result["artifacts"].append({"file": lock.name, "sha256": result["sdkWheelLockSha256"]})
    return result


def attach_artifacts(result, out, files):
    for file in files:
        result["artifacts"].append({"file": file, "sha256": sha((out / file).read_bytes())})


def write_results(out, filename, results):
    (out / filename).write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")
