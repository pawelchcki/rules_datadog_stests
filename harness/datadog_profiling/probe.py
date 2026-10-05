"""Profile enablement and process tags through SDK → real Agent → backend."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit

from harness.datadog_agent.probe import BASE_ENV, agent_command, process, resolve, server_thread, wait_ready
from harness.datadog_backend.backend import API_KEY, BackendServer, Handler, MAX_BODY
from harness.datadog_telemetry.proxy import CaptureProxy
from harness.datadog_profiling.wire import multipart

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"


class ProfileHandler(Handler):
    def do_POST(self):
        if urlsplit(self.path).path != "/api/v2/profile":
            return super().do_POST()
        headers = {key.lower(): value for key, value in self.headers.items()}
        data, payload = b"", None
        status, error = 200, None
        try:
            length = int(headers.get("content-length", "-1"))
            if not 0 <= length <= MAX_BODY or headers.get("transfer-encoding"):
                raise ValueError("unsupported profile request framing")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("truncated profile upload")
            if headers.get("dd-api-key") != self.server.api_key:
                raise ValueError("invalid profile API key")
            payload = multipart(headers, data)
        except (ValueError, UnicodeError) as exc:
            status, error = 400, str(exc)
        self.server.capture("/api/v2/profile", headers, data, status, payload, error)
        self.respond(status, {"error": error} if error else {})


def check_profiles(records):
    assert records and all(record["status"] == 200 for record in records), records
    for record in records:
        payload = record["payload"]
        event = payload["event"]
        start, end = (datetime.fromisoformat(event[key].replace("Z", "+00:00")) for key in ("start", "end"))
        assert start.tzinfo is not None and end > start, event
        tags = event.get("tags_profiler", "")
        assert "service:profiling-lab" in tags and "env:profiling-env" in tags and "version:profiling-version" in tags, event
        process_tags = event["process_tags"]
        assert all(tag in process_tags for tag in ("entrypoint.name:", "entrypoint.workdir:", "svc.user:true")), event
    strings = [value for record in records for part in record["payload"]["parts"] if "profile" in part for value in part["profile"]["sampledFunctions"]]
    assert any("profile_work" in value for value in strings), "profile did not sample the actual application workload"


def execute(args, out):
    backend = BackendServer(("127.0.0.1", 0), out / "backend")
    backend.RequestHandlerClass = ProfileHandler
    results = []
    with server_thread(backend):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        backend_url = "http://127.0.0.1:" + str(backend.server_port)
        config = {"api_key": API_KEY, "hostname": "profiling-agent", "dd_url": backend_url,
            "log_level": "info", "log_to_console": True, "log_file": "", "remote_configuration": {"enabled": False},
            "agent_telemetry": {"enabled": False}, "apm_config": {"enabled": True, "receiver_port": port,
                "receiver_socket": "", "log_file": "", "debug": {"port": 0}, "telemetry": {"enabled": False},
                "apm_dd_url": backend_url, "profiling_dd_url": backend_url + "/api/v2/profile",
                "max_memory": 0, "max_cpu_percent": 0, "trace_writer": {"flush_period_seconds": 0.2}}}
        config_path = out / "datadog.yaml"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        clean = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
        with process(agent_command(args.agent_rootfs) + ["run", "--config=" + str(config_path)], out / "agent.log", clean) as proc:
            agent_url = "http://127.0.0.1:" + str(port)
            info = wait_ready(proc, agent_url + "/info", out / "agent.log")
            (out / "agent-info.json").write_text(json.dumps(info, indent=2) + "\n")
            for enabled in (True, False):
                name = "profiling-enabled" if enabled else "profiling-disabled"
                case_out = out / name
                proxy = CaptureProxy(agent_url, case_out / "tracer")
                before = len(backend.snapshot())
                with server_thread(proxy):
                    env = dict(BASE_ENV, DD_TRACE_AGENT_URL="http://127.0.0.1:" + str(proxy.server_port),
                        DD_SERVICE="profiling-lab", DD_ENV="profiling-env", DD_VERSION="profiling-version",
                        DD_PROFILING_ENABLED=str(enabled).lower(), DD_PROFILING_UPLOAD_INTERVAL="1",
                        DD_PROFILING_TIMELINE_ENABLED="true", DD_TRACE_API_VERSION="v0.5")
                    launch = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
                    launch += ["--instance=" + name] + ["--env=" + key + "=" + value for key, value in sorted(env.items())] + ["--", args.app]
                    child = subprocess.run(launch, env=clean, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
                    case_out.mkdir(parents=True, exist_ok=True)
                    (case_out / "app.log").write_bytes(child.stdout)
                    assert child.returncode == 0, child.stdout.decode("utf-8", errors="replace")
                    time.sleep(0.3)
                    records = [record for record in backend.snapshot()[before:] if record["path"] == "/api/v2/profile"]
                    intake = [record for record in proxy.snapshot() if record["path"] == "/profiling/v1/input"]
                if enabled:
                    check_profiles(records)
                    assert intake and all(record["status"] == 200 for record in intake), intake
                    for request in intake:
                        assert any(record["raw_sha256"] == request["raw_sha256"] for record in records), (request, records)
                else:
                    assert not records and not intake, (records, intake)
                capture_path = name + "/profiles.json"
                capture = json.dumps(records, indent=2).encode() + b"\n"
                (out / capture_path).write_bytes(capture)
                files = [capture_path, name + "/app.log", "datadog.yaml", "agent-info.json"]
                files += ["backend/" + record["raw_file"] for record in records]
                files += [name + "/tracer/" + record["raw_file"] for record in intake]
                results.append({"name": name, "status": "passed", "configuration": env,
                    "capabilityInventoryRevision": REVISION, "capabilityNames": ["profiling", "dd_profiling_enabled", "process_tags"],
                    "captureFile": capture_path, "captureSha256": hashlib.sha256(capture).hexdigest(),
                    "artifacts": [{"file": file, "sha256": hashlib.sha256((out / file).read_bytes()).hexdigest()} for file in files]})
                print(name, "passed", flush=True)
    (out / "datadog-profiling-results.json").write_text(json.dumps({"results": results}, indent=2) + "\n")


def main():
    if not __debug__:
        raise RuntimeError("Profile assertions require optimization disabled")
    parser = argparse.ArgumentParser()
    for option in ("agent-rootfs", "launcher", "rootfs", "app"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("agent_rootfs", "launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    os.environ["DATADOG_BACKEND_ZSTD_LIBRARY"] = str(Path(args.agent_rootfs) / "usr/lib/x86_64-linux-gnu/libzstd.so.1.5.5")
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, out)


if __name__ == "__main__":
    main()
