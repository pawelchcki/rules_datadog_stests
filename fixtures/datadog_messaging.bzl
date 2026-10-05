"""Native Kombu AMQP tracing and data-streams evidence."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_messaging_tests():
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    app = "//harness/datadog_messaging:workload.py"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_messaging_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_messaging:probe",
        data = [app, "//harness/datadog_messaging:peer.py", rootfs, "//harness/datadog_messaging:sdk_overlay", injection.rootfs, "@rules_stests//harness:app_launcher"],
        args = [
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
            "--overlay=$(rlocationpath //harness/datadog_messaging:sdk_overlay)",
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab"],
    )
