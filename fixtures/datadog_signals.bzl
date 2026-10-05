"""Log correlation, startup diagnostics and real runtime UDP controls."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_signals_tests():
    app = "//harness/datadog_signals:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    injection = datadog_python_injection()
    tests = []
    for wire in ["v0.4", "v0.5"]:
        name = "datadog_signals_" + wire.replace(".", "") + "_test"
        service_test(
            name = name,
            timeout = "long",
            services = ["@rules_stests//harness:otel_sink_service"],
            test = "//harness/datadog_signals:probe",
            data = [app, rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
            args = ["--wire=" + wire, "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                "--rootfs=$(rlocationpath {})".format(rootfs), "--app=$(rlocationpath {})".format(app)] +
                ["--injection-flag=" + flag for flag in injection.flags],
            tags = ["datadog", "lab", "signals"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_signals_suite", tests = tests)
