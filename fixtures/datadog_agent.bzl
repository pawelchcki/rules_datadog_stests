"""Tracer → real Datadog APM Agent → retained local backend tests."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_agent_tests():
    app = "//fixtures/apps/python/datadog-lab:app.py"
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    tests = []
    for wire, core in [("v0.4", False), ("v0.5", False), ("v0.4", True), ("v0.5", True)]:
        name = "datadog_agent_" + ("core_" if core else "") + wire.replace(".", "") + "_test"
        service_test(
            name = name,
            timeout = "long",
            services = ["@rules_stests//harness:otel_sink_service"],
            test = "//harness/datadog_agent:probe",
            data = [app, rootfs, agent_rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
            args = [
                "--wire=" + wire,
                "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
                "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                "--rootfs=$(rlocationpath {})".format(rootfs),
                "--app=$(rlocationpath {})".format(app),
            ] + ["--injection-flag=" + flag for flag in injection.flags] + (["--core-agent"] if core else []),
            tags = ["datadog", "lab", "agent"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_agent_suite", tests = tests)
