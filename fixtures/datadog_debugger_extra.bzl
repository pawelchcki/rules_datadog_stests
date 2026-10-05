"""Runtime-verified debugger, appsec-RC and telemetry capabilities beyond the base labs."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

_SCENARIOS = ["probes", "crash"]

def _datadog_debugger_extra_test(scenario):
    app = "//harness/datadog_debugger_extra/" + scenario + ":workload.py"
    rootfs = "@rules_stests//harness:django_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    service_test(
        name = "datadog_debugger_extra_" + scenario + "_test",
        timeout = "long",
        services = [],
        test = "//harness/datadog_debugger_extra/" + scenario + ":probe",
        data = [app, rootfs, agent_rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"] + (["//harness/datadog_debugger_extra/probes:target.py"] if scenario == "probes" else []),
        args = [
            "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
        ] + ["--injection-flag=" + flag for flag in injection.flags],
        tags = ["datadog", "lab", "agent", "debugger"],
    )

def datadog_debugger_extra_tests():
    for scenario in _SCENARIOS:
        _datadog_debugger_extra_test(scenario)
