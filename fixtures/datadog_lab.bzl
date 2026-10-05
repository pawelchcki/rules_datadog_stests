"""Standalone pinned Python Datadog SDK tracing lab."""

load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_lab_tests():
    app = "//fixtures/apps/python/datadog-lab:app.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    injection = datadog_python_injection()
    tests = []
    for wire in ["v0.4", "v0.5"]:
        name = "datadog_lab_" + wire.replace(".", "") + "_test"
        service_test(
            name = name,
            timeout = "long",
            services = ["@rules_stests//harness:otel_sink_service"],
            test = "//harness/datadog_lab:probe",
            data = [app, rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
            args = [
                "--wire=" + wire,
                "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                "--rootfs=$(rlocationpath {})".format(rootfs),
                "--app=$(rlocationpath {})".format(app),
            ] + ["--injection-flag=" + flag for flag in injection.flags],
            tags = ["datadog", "lab"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_lab_suite", tests = tests)

def datadog_upstream_lab_tests():
    """Run original pinned system-tests assertions on the existing Python SDK lab."""
    app = "//fixtures/apps/python/datadog-lab:app.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    injection = datadog_python_injection()
    tests = []
    for wire in ["v0.4", "v0.5"]:
        name = "datadog_upstream_lab_" + wire.replace(".", "") + "_test"
        service_test(
            name = name,
            timeout = "long",
            services = ["@rules_stests//harness:otel_sink_service"],
            test = "//harness/upstream_lab:probe",
            data = [app, rootfs, injection.rootfs, "@rules_stests//harness:app_launcher", "//harness/upstream_lab:upstream_sources", "//harness/upstream_lab:vendor/manifest.json"],
            args = [
                "--wire=" + wire,
                "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                "--rootfs=$(rlocationpath {})".format(rootfs),
                "--app=$(rlocationpath {})".format(app),
                "--vendor=$(rlocationpath //harness/upstream_lab:vendor/manifest.json)",
            ] + ["--injection-flag=" + flag for flag in injection.flags],
            tags = ["datadog", "lab", "upstream"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_upstream_lab_suite", tests = tests)
