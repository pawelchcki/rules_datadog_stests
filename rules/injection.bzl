"""Pinned Datadog activation and deterministic tracer configuration."""
load("@rules_stests//rules:realworld_app.bzl", "instrumentation_injection")

def datadog_python_injection(rootfs = Label("//harness:datadog_python_rootfs"), aiohttp = False):
    """Activates the pinned dd-trace-py package through Python sitecustomize."""
    payload = "{instrumentation_rootfs}"
    return instrumentation_injection(
        rootfs = rootfs,
        env = {
            "RULES_STESTS_DATADOG_AIOHTTP_ENABLED": "true",
            # aiosqlite executes DB-API calls on a private worker thread with
            # no request context. Trace its SQLAlchemy engine before work is
            # queued, using ddtrace's supported SQLAlchemy integration.
            "DD_TRACE_SQLALCHEMY_ENABLED": "true",
            "DD_TRACE_SQLITE3_ENABLED": "false",
        } if aiohttp else {},
        prepend_path = {"PYTHONPATH": payload},
        require = [payload + "/sitecustomize.py", payload + "/ddtrace_pkgs"],
    )

def datadog_ruby_injection(rootfs = Label("//harness:datadog_ruby_rootfs")):
    """Activates a locked, ABI-matched Datadog payload before the Ruby application boots."""
    payload = "{instrumentation_rootfs}/datadog-ruby"
    return instrumentation_injection(
        rootfs = rootfs,
        env = {
            "RULES_STESTS_DATADOG_RUBY_ROOT": payload,
            "RUBYOPT": "-r" + payload + "/activation.rb",
        },
        require = [payload + "/activation.rb", payload + "/abi.json", payload + "/specifications"],
    )

def datadog_env(service = "realworld-datadog", wire_version = "v0.5", sink = Label("@rules_stests//harness:telemetry_sink_service"), extra = {}):
    """Returns deterministic traces-only Datadog intake configuration."""
    if wire_version not in ["v0.4", "v0.5"]:
        fail("Datadog intake wire_version must be v0.4 or v0.5")
    if not service:
        fail("Datadog requires a non-empty service identity")
    env = {
        "DD_SERVICE": service,
        "DD_ENV": "test",
        "DD_VERSION": "1",
        "DD_TRACE_ENABLED": "true",
        "DD_TRACE_AGENT_URL": "http://127.0.0.1:$${%s}" % str(native.package_relative_label(sink)),
        "DD_TRACE_API_VERSION": wire_version,
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1.0}]',
        "DD_TRACE_RATE_LIMIT": "-1",
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.1",
        "DD_TRACE_PARTIAL_FLUSH_ENABLED": "false",
        "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v0",
        "DD_TRACE_PROPAGATION_STYLE_EXTRACT": "datadog,tracecontext",
        "DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog,tracecontext",
        "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": "true",
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
        "DD_RUNTIME_METRICS_ENABLED": "false",
        "DD_PROFILING_ENABLED": "false",
        "DD_APPSEC_ENABLED": "false",
        "DD_IAST_ENABLED": "false",
        "DD_SCA_ENABLED": "false",
        "DD_DYNAMIC_INSTRUMENTATION_ENABLED": "false",
        "DD_EXCEPTION_REPLAY_ENABLED": "false",
        "DD_DATA_STREAMS_ENABLED": "false",
        "DD_LLMOBS_ENABLED": "false",
        "DD_LOGS_INJECTION": "false",
        "DD_TRACE_COMPUTE_STATS": "false",
        "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "false",
        "DD_TRACE_OTEL_ENABLED": "false",
        "DD_TRACE_STARTUP_LOGS": "false",
    }
    env.update(extra)
    return env
