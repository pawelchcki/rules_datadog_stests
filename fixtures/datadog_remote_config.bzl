"""Signed remote configuration through real core and trace Agents."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_remote_config_tests():
    app = "//harness/datadog_remote_config:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_remote_config_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_remote_config:probe",
        data = [app, rootfs, agent_rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
        args = [
            "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "agent", "remote-config"],
    )
