"""Official OpenAI client instrumentation through APM and the real Agent EVP proxy."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_openai_tests():
    app = "//harness/datadog_openai:workload.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_openai_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_openai:probe",
        data = ["//harness/datadog_openai:sdk_overlay", app, rootfs, agent_rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
        args = [
            "--sdk-overlay=$(rlocationpath //harness/datadog_openai:sdk_overlay)",
            "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "agent", "llmobs"],
    )
