"""Independent shared SDK cases, with Go as a first-class native fixture."""
load("@rules_itest//:itest.bzl", "service_test")
load("//harness/shared_sdk:cases.bzl", "SHARED_SDK_CASES")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_shared_sdk_tests():
    suites = {}
    for language in ["go", "python"]:
        tests = []
        for index, case in enumerate(SHARED_SDK_CASES):
            name = "datadog_shared_sdk_" + language + "_" + str(index) + "_test"
            if language == "go":
                app = "//harness/shared_sdk/go:sdk"
                data = [app]
                launch_args = []
            else:
                app = "//fixtures/apps/python/datadog-lab:app.py"
                rootfs = "@rules_stests//harness:aiohttp_rootfs"
                injection = datadog_python_injection()
                data = [app, rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"]
                launch_args = [
                    "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                    "--rootfs=$(rlocationpath {})".format(rootfs),
                ] + ["--injection-flag=" + flag for flag in injection.flags]
            service_test(
                name = name,
                timeout = "long",
                exec_properties = {"test.EstimatedCPU": "2"},
                services = ["@rules_stests//harness:otel_sink_service"],
                test = "//harness/shared_sdk:probe",
                data = data + ["//harness/shared_sdk:cases.json", "//harness/upstream_lab:vendor/manifest.json"],
                args = [
                    "--language=" + language,
                    "--app=$(rlocationpath {})".format(app),
                    "--registry=$(rlocationpath //harness/shared_sdk:cases.json)",
                    "--vendor=$(rlocationpath //harness/upstream_lab:vendor/manifest.json)",
                    "--case=" + case,
                ] + launch_args,
                tags = ["datadog", "shared-sdk"],
            )
            tests.append(":" + name)
        suites[language] = tests
        native.test_suite(name = "datadog_shared_sdk_" + language + "_suite", tests = tests)
    native.test_suite(name = "datadog_shared_sdk_suite", tests = suites["go"] + suites["python"])
    native.test_suite(
        name = "datadog_go_capability_suite",
        tests = suites["go"] + [":gin_datadog_external_features_v04", ":go_runtime_capability_suite"],
        tags = ["manual"],
    )
