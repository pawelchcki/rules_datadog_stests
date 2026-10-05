"""Third-party integration labs: Anthropic, Google GenAI, GraphQL, LLMObs datasets, DSM/botocore, DBM."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def _integration_test(name, probe, app, extra_data = [], extra_args = []):
    rootfs = "@rules_stests//harness:aiohttp_rootfs"
    agent_rootfs = "//harness:datadog_agent_rootfs"
    injection = datadog_python_injection()
    service_test(
        name = name,
        timeout = "long",
        services = [],
        test = "//harness/datadog_integrations:" + probe,
        data = [
            "//harness/datadog_integrations:sdk_overlay",
            "//bazel:integrations_wheels.lock.json",
            app,
            rootfs,
            agent_rootfs,
            injection.rootfs,
            "@rules_stests//harness:app_launcher",
        ] + extra_data,
        args = [
            "--sdk-overlay=$(rlocationpath //harness/datadog_integrations:sdk_overlay)",
            "--sdk-lock=$(rlocationpath //bazel:integrations_wheels.lock.json)",
            "--agent-rootfs=$(rlocationpath {})".format(agent_rootfs),
            "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
            "--rootfs=$(rlocationpath {})".format(rootfs),
            "--app=$(rlocationpath {})".format(app),
        ] + ["--injection-flag=" + flag for flag in injection.flags] + extra_args,
        tags = ["datadog", "lab", "agent"],
    )

def datadog_integrations_tests():
    _integration_test("datadog_anthropic_test", "anthropic_probe", "//harness/datadog_integrations:anthropic_workload.py")
    _integration_test("datadog_genai_test", "genai_probe", "//harness/datadog_integrations:genai_workload.py")
    _integration_test("datadog_graphql_test", "graphql_probe", "//harness/datadog_integrations:graphql_workload.py")
    _integration_test("datadog_datasets_test", "datasets_probe", "//harness/datadog_integrations:datasets_workload.py")
    _integration_test("datadog_dsm_test", "dsm_probe", "//harness/datadog_integrations:dsm_workload.py")
    _integration_test("datadog_dbm_test", "dbm_probe", "//harness/datadog_integrations:dbm_workload.py", extra_data = ["//harness:datadog_mariadb_rootfs"], extra_args = ["--mariadb-rootfs=$(rlocationpath //harness:datadog_mariadb_rootfs)"])
    _integration_test("datadog_otel_mysql_test", "otel_mysql_probe", "//harness/datadog_integrations:otel_mysql_workload.py", extra_data = ["//harness:datadog_mariadb_rootfs"], extra_args = ["--mariadb-rootfs=$(rlocationpath //harness:datadog_mariadb_rootfs)"])
