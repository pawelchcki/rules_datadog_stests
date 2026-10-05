"""Native SDK evidence for discovery, stable config and propagation."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_sdk_extra_tests():
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    app = "//harness/datadog_sdk_extra:workload.py"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_sdk_extra_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_sdk_extra:probe",
        data = [app, rootfs, "//harness/datadog_sdk_extra:sca_overlay", injection.rootfs, "@rules_stests//harness:app_launcher"],
        args = [
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
            "--overlay=$(rlocationpath //harness/datadog_sdk_extra:sca_overlay)",
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab"],
    )
