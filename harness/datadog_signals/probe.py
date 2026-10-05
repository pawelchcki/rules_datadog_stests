"""Verify log correlation and runtime UDP against retained native spans."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading

from harness.datadog_agent.probe import BASE_ENV, get, native_spans, resolve

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
IDENTITY = {"dd.service": "signals-service", "dd.env": "signals-env", "dd.version": "signals-version"}


class UDP:
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.messages = []
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.receive)

    def receive(self):
        while not self.stopped.is_set():
            try:
                data, _ = self.socket.recvfrom(65535)
                self.messages.append(data.decode("utf-8"))
            except socket.timeout:
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join(timeout=2)
        self.socket.close()


def check_logs(observed, spans, enabled, full):
    identity = observed["identity"]
    assert len(spans) == 1, spans
    span = spans[0]
    assert span["span_id"] == int(identity["span_id"]) and span["parent_id"] == int(identity["parent_id"]), (span, identity)
    trace = int(identity["trace_id"])
    assert span["trace_id"] == trace & ((1 << 64) - 1), span
    if full:
        assert span["meta"]["_dd.p.tid"] == f"{trace >> 64:016x}", span
    else:
        assert "_dd.p.tid" not in span["meta"], span
    assert [log["message"] for log in observed["logs"]] == ["outside-before", "inside", "outside-after"]
    for log in observed["logs"]:
        fields = log["fields"]
        if not enabled:
            assert all(value is None for value in fields.values()), fields
            continue
        assert all(fields[key] == value for key, value in IDENTITY.items()), fields
        inside = log["message"] == "inside"
        expected_trace = f"{trace:032x}" if full else str(trace & ((1 << 64) - 1))
        assert fields["dd.trace_id"] == (expected_trace if inside else "0"), fields
        assert fields["dd.span_id"] == (identity["span_id"] if inside else "0"), fields


def check_runtime(messages, enabled):
    lines = [line for message in messages for line in message.splitlines()]
    if not enabled:
        assert not lines, lines
        return
    assert lines, "SDK runtime worker did not send UDP metrics"
    expected = {"runtime.python.gc.count.gen0", "runtime.python.gc.count.gen1", "runtime.python.gc.count.gen2"}
    observed = set()
    for line in lines:
        name, body = line.split(":", 1)
        if not name.startswith("runtime.python."):
            continue
        value, kind, *options = body.split("|")
        assert kind in {"g", "h", "d"} and float(value) >= 0, line
        tags = {tag for option in options if option.startswith("#") for tag in option[1:].split(",")}
        assert {"service:signals-service", "env:signals-env", "version:signals-version"} <= tags, line
        observed.add(name)
    assert expected <= observed, (expected, observed, lines)


def execute(args, sink, out):
    results = []
    for name, enabled, full, url_precedence in [
        ("signals-enabled", True, False, False),
        ("signals-128-bit", True, True, True),
        ("signals-disabled", False, False, True),
    ]:
        get(sink + "/reset?protocol=datadog", "POST")
        with UDP() as selected, UDP() as unused:
            env = dict(BASE_ENV, DD_TRACE_AGENT_URL=sink, DD_TRACE_API_VERSION=args.wire,
                DD_SERVICE="signals-service", DD_ENV="signals-env", DD_VERSION="signals-version",
                DD_LOGS_INJECTION=str(enabled).lower(), DD_TRACE_LOG_LEVEL="INFO",
                DD_TRACE_STARTUP_LOGS=str(enabled).lower(), DD_RUNTIME_METRICS_ENABLED=str(enabled).lower(),
                DD_RUNTIME_METRICS_INTERVAL="0.25", DD_AGENT_HOST="127.0.0.1", DD_DOGSTATSD_PORT=str(unused.port if url_precedence else selected.port))
            if url_precedence:
                env["DD_DOGSTATSD_URL"] = "udp://127.0.0.1:" + str(selected.port)
            command = [args.launcher, "--runtime=python", "--rootfs=" + args.rootfs] + args.injection_flags
            command += ["--instance=" + name] + ["--env=" + key + "=" + value for key, value in sorted(env.items())]
            command += ["--", args.app, "--trace-bits=" + ("128" if full else "64")]
            clean = {key: value for key, value in os.environ.items() if not key.startswith(("DD_", "OTEL_")) and key != "PYTHONOPTIMIZE"}
            child = subprocess.run(command, env=clean, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
            log = child.stdout.decode("utf-8")
            (out / (name + ".log")).write_text(log)
            assert child.returncode == 0, log
        observed = json.loads(next(line[7:] for line in log.splitlines() if line.startswith("RESULT ")))
        capture = get(sink + "/dump?protocol=datadog")
        (out / (name + ".capture.json")).write_bytes(capture)
        udp = json.dumps({"selected": selected.messages, "unused": unused.messages}, indent=2).encode()
        (out / (name + ".udp.json")).write_bytes(udp)
        check_logs(observed, native_spans(capture, args.wire), enabled, full)
        check_runtime(selected.messages, enabled)
        assert not unused.messages, unused.messages
        startup = [line for line in log.splitlines() if "DATADOG TRACER CONFIGURATION" in line]
        assert bool(startup) == enabled, log
        if enabled:
            data = ast.literal_eval(startup[0].split("- DATADOG TRACER CONFIGURATION - ", 1)[1])
            assert data["service"] == "signals-service" and data["env"] == "signals-env", data
            assert data["ddtrace_enabled"] is True and data["runtime_metrics_enabled"] is True, data
            assert data["log_injection_enabled"] is True and data["dd_version"] == "signals-version", data
            # Both rendered formats come from the same SDK-patched LogRecord.
            assert "TEXT inside dd.service=signals-service" in log, log
            structured = [json.loads(line[11:]) for line in log.splitlines() if line.startswith("STRUCTURED ")]
            assert [entry["dd.trace_id"] for entry in structured] == [entry["fields"]["dd.trace_id"] for entry in observed["logs"]]
        capabilities = ["log_injection", "structured_log_injection", "unstructured_log_injection", "log_tracer_status_at_startup", "runtime_metrics", "dogstatsd_agent_connection"]
        if full:
            capabilities.append("log_injection_128bit_traceid")
        files = [name + suffix for suffix in [".capture.json", ".log", ".udp.json"]]
        results.append({"name": name, "status": "passed", "wire": args.wire, "configuration": env,
            "capabilityInventoryRevision": REVISION, "capabilityNames": capabilities,
            "captureFile": name + ".capture.json", "captureSha256": hashlib.sha256(capture).hexdigest(),
            "artifacts": [{"file": path, "sha256": hashlib.sha256((out / path).read_bytes()).hexdigest()} for path in files]})
        print(args.wire, name, "passed", flush=True)
    (out / "datadog-signals-results.json").write_text(json.dumps({"results": results}, indent=2) + "\n")


def main():
    if not __debug__:
        raise RuntimeError("Signal assertions require optimization disabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--wire", choices=["v0.4", "v0.5"], required=True)
    for option in ("launcher", "rootfs", "app"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--injection-flag", action="append", default=[], dest="injection_flags")
    args = parser.parse_args()
    for key in ("launcher", "rootfs", "app"):
        setattr(args, key, resolve(getattr(args, key)))
    args.injection_flags = ["--instrumentation-rootfs=" + resolve(flag.split("=", 1)[1]) if flag.startswith("--instrumentation-rootfs=") else flag for flag in args.injection_flags]
    ports = json.loads(os.environ["ASSIGNED_PORTS"])
    port = next(value for key, value in ports.items() if key.endswith("//harness:otel_sink_service"))
    out = Path(os.environ["TEST_UNDECLARED_OUTPUTS_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    execute(args, "http://127.0.0.1:" + str(port), out)


if __name__ == "__main__":
    main()
