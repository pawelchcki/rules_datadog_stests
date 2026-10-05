"""Live Datadog OpenFeature provider with pinned upstream evaluation vectors."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_ffe_tests():
    app = "//harness/datadog_ffe:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    overlay = "//harness/datadog_ffe:sdk_overlay"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_ffe_test",
        timeout = "long",
        services = ["@rules_stests//harness:otel_sink_service"],
        test = "//harness/datadog_ffe:probe",
        data = [app, rootfs, overlay, injection.rootfs, "@rules_stests//harness:app_launcher",
                "//bazel:ffe_wheels.lock.json", "//bazel:otel_wheels.lock.json", "//harness/datadog_ffe:vendor", "//harness/datadog_ffe:vendor/manifest.json"],
        args = [
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
            "--overlay=$(rlocationpath {})".format(overlay),
            "--wheel-lock=$(rlocationpath //bazel:ffe_wheels.lock.json)",
            "--otel-wheel-lock=$(rlocationpath //bazel:otel_wheels.lock.json)",
            "--manifest=$(rlocationpath //harness/datadog_ffe:vendor/manifest.json)",
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "feature-flags"],
    )
