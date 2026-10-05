"""Shared runtime plumbing for the datadog_debugger_extra harness family.

Every scenario reuses the same production-shaped stack: a TLS-authenticated
fake backend serving signed remote-config TUF stages, the real pinned core and
trace Agents (7.83.1) forwarding to that backend, and a capture proxy between
the pinned Python SDK (ddtrace 4.14) and the trace Agent retaining every
native request.
"""
import json
import os
import select
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from harness.datadog_agent.probe import AGENT_VERSION, BASE_ENV, agent_command, core_agent, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer
from harness.datadog_backend.wire import msgpack, trace_chunks
from harness.datadog_telemetry.proxy import CaptureProxy
from harness.datadog_telemetry.probe import sha

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
CERTIFICATE = Path(__file__).with_name("localhost-test.crt")
UPSTREAM = "https://github.com/DataDog/system-tests/blob/" + REVISION + "/"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, message, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(message)


def rc_requests(proxy, directory):
    return [json.loads((directory / r["raw_file"]).read_bytes()) for r in proxy.snapshot() if r["path"] == "/v0.7/config"]


def tracer_spans(proxy, directory, wire_path):
    return [span for record in proxy.snapshot() if record["path"] == wire_path
            for trace in msgpack((directory / record["raw_file"]).read_bytes()) for span in trace]


class Relay(__import__("socketserver").ThreadingTCPServer):
    """Loopback CONNECT relay with a per-scenario allowlist.

    Modeled on harness.datadog_llmobs.connect.ConnectRelay but parameterized
    so scenarios can allow additional synthetic EVP intake hosts (the llmobs
    module stays untouched).
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, backend_port, extra_hosts=()):
        super().__init__(("127.0.0.1", 0), RelayHandler)
        self.server_port = self.server_address[1]
        self.backend_port = backend_port
        self.allowed = {"llmobs-intake.backend.test:443", "127.0.0.1:" + str(backend_port), *extra_hosts}
        self.records = []
        self.lock = threading.Lock()


class RelayHandler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        with self.server.lock:
            self.server.records.append(self.path)
        if self.path not in self.server.allowed:
            self.send_error(403, "host not allowed by relay policy")
            return
        with socket.create_connection(("127.0.0.1", self.server.backend_port), timeout=10) as upstream:
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            peers = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(peers, [], [], 20)
                if not ready:
                    return
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(chunk)

    def log_message(self, *_args):
        pass


@contextmanager
def backend_context(out, handler_class, fixtures, relay_hosts=()):
    """TLS backend with signed RC fixtures plus its CONNECT relay."""
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = handler_class
    backend.rc_stage = 1
    backend.rc_fixtures = fixtures
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(CERTIFICATE, CERTIFICATE.with_suffix(".key"))
    backend.socket = tls.wrap_socket(backend.socket, server_side=True)
    relay = Relay(backend.server_port, relay_hosts)
    with server_thread(backend), server_thread(relay):
        yield backend, relay, "https://127.0.0.1:" + str(backend.server_port)


def agent_config(out, backend_url, relay_port, fixtures, apm_port, health_port, cmd_port, telemetry=False):
    """datadog.yaml for the real core + trace Agents against the fake backend."""
    return {"api_key": API_KEY, "hostname": "rc-agent", "env": "rc-env", "dd_url": backend_url,
        "log_level": "debug", "log_to_console": True, "log_file": "", "cmd_port": cmd_port, "expvar_port": 0,
        "proxy": {"http": "http://127.0.0.1:" + str(relay_port), "https": "http://127.0.0.1:" + str(relay_port)},
        "remote_configuration": {"enabled": True, "no_tls": False, "no_tls_validation": False, "rc_dd_url": backend_url, "refresh_interval": "5s", "org_status_refresh_interval": "5s",
                                 "config_root": json.dumps(fixtures["root"]), "director_root": json.dumps(fixtures["root"])},
        "agent_telemetry": {"enabled": False}, "health_platform": {"enabled": False}, "inventories_diagnostics_enabled": False, "health_port": health_port,
        "dogstatsd_port": port(), "dogstatsd_socket": "",
        "enable_metadata_collection": False, "inventories_enabled": False, "cloud_provider_metadata": [],
        "run_path": str(out / "core-run"), "conf_path": str(out / "conf.d"), "auth_token_file_path": str(out / "auth_token"),
        "logs_enabled": True, "process_config": {"process_collection": {"enabled": False}, "container_collection": {"enabled": False},
            "process_discovery": {"enabled": False}, "run_in_core_agent": False},
        "apm_config": {"enabled": True, "receiver_port": apm_port, "receiver_socket": "", "log_file": "", "debug": {"port": 0},
            "debugger_dd_url": backend_url + "/api/v2/debugger", "debugger_diagnostics_dd_url": backend_url + "/api/v2/debugger",
            "apm_non_local_traffic": False, "apm_dd_url": backend_url, "telemetry": {"enabled": telemetry},
            "trace_writer": {"flush_period_seconds": 0.2}, "max_memory": 0, "max_cpu_percent": 0}}


@contextmanager
def agents_running(args, config, out, health_port, relay_port):
    """Real core Agent plus trace Agent against a written datadog.yaml."""
    (out / "conf.d").mkdir(exist_ok=True)
    config_path = out / "datadog.yaml"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_", "_DD_"))}
    clean_env["SSL_CERT_FILE"] = str(CERTIFICATE)
    args.core_agent = True
    args.backend_url = "http://127.0.0.1:" + str(relay_port)
    with core_agent(args, config_path, out, clean_env, health_port), \
            process(agent_command(args.agent_rootfs) + ["run", "--config=" + str(config_path)], out / "agent.log", clean_env) as apm:
        info = wait_ready(apm, "http://127.0.0.1:" + str(config["apm_config"]["receiver_port"]) + "/info", out / "agent.log")
        assert info["version"] == AGENT_VERSION and "/v0.7/config" in info["endpoints"], info
        (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
        yield info, clean_env


def launch_command(args, env, out, app=None, instance="datadog-debugger-extra", extra=None):
    launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
    launch += ["--instance=" + instance] + ["--env=" + key + "=" + value for key, value in sorted(env.items())]
    launch += ["--", app or args.app, "--directory", str(out)]
    if extra:
        launch += extra
    return launch


def receipt(feature, env, ready, scope, missing, source=None, source_sha256=None):
    record = {"name": feature, "status": "failed", "capabilityNames": [feature],
        "capabilityInventoryRevision": REVISION, "configuration": env,
        "agentVersion": AGENT_VERSION, "clientVersion": ready["tracer_version"],
        "captureFile": "tracer/requests.json", "scope": scope, "missingAssertions": missing, "artifacts": []}
    if source:
        record["source"] = UPSTREAM + source
        record["sourceSha256"] = source_sha256
    return record


def flush_results(out, filename, results, files):
    for record in results:
        capture = out / record["captureFile"]
        if capture.exists():
            record["captureSha256"] = sha(capture.read_bytes())
        record["artifacts"] = [{"file": file, "sha256": sha((out / file).read_bytes())} for file in sorted(set(files)) if (out / file).exists()]
    (out / filename).write_text(json.dumps({"schemaVersion": 1, "results": results}, indent=2) + "\n")


def artifact_files(out, proxy, backend, extras=()):
    files = list(extras)
    files += ["tracer/" + r["raw_file"] for r in proxy.snapshot()]
    files += ["backend/" + r["raw_file"] for r in backend.snapshot()]
    files += ["backend/" + r["payload"]["response_file"] for r in backend.snapshot() if r["path"].startswith("/api/v0.1/") and "response_file" in r.get("payload", {})]
    return files


def run_main(execute):
    """py_binary entry point shared by every scenario probe."""
    if not __debug__:
        raise RuntimeError("RC assertions require Python optimization disabled")
    import argparse
    parser = argparse.ArgumentParser()
    for option in ("agent-rootfs", "launcher", "rootfs", "app"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("agent_rootfs", "launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out)
