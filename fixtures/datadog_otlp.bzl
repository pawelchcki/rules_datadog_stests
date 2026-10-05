"""Datadog-configured OpenTelemetry metrics/logs using a pinned SDK overlay."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_otlp_tests():
    app = "//harness/datadog_otlp:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    overlay = "//harness/datadog_otlp:sdk_overlay"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_otlp_test",
        timeout = "long",
        services = ["@rules_stests//harness:otel_sink_service"],
        test = "//harness/datadog_otlp:probe",
        data = [app, rootfs, overlay, injection.rootfs, "@rules_stests//harness:app_launcher", "//bazel:otel_wheels.lock.json"],
        args = [
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
            "--overlay=$(rlocationpath {})".format(overlay),
            "--wheel-lock=$(rlocationpath //bazel:otel_wheels.lock.json)",
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "otel"],
    )
