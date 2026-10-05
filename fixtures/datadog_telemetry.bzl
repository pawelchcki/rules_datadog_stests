"""SDK telemetry through the pinned real Agent and local backend."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_telemetry_tests():
    app = "//harness/datadog_telemetry:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_telemetry_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_telemetry:probe",
        data = [app, rootfs, agent_rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
        args = [
            "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "agent", "telemetry"],
    )
