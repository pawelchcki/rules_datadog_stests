"""Additional native security capability evidence on the pinned Django runtime."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_security_extra_tests():
    rootfs = "@rules_stests//harness:django_rootfs"
    app = "//harness/datadog_security_extra:app.py"
    injection = datadog_python_injection()
    tests = []
    for profile in ["default", "tagging", "large", "events", "identified", "anonymized", "disabled", "rate", "standalone", "controls", "rasp", "sampling", "renaming", "ip-custom", "ip-disabled", "ip-precedence", "onboarding"]:
        name = "datadog_security_extra_" + profile.replace("-", "_") + "_test"
        service_test(
            name = name,
            timeout = "long",
            services = [],
            test = "//harness/datadog_security_extra:probe",
            data = [app, rootfs, injection.rootfs, "@rules_stests//harness:app_launcher", "//harness/datadog_security_extra:sdk_overlay"] + ["//harness/datadog_security_extra:" + f for f in ["security_extra_views.py", "security_controls.py", "circular_a.py", "circular_b.py", "blocking-rules.json", "tagging-rules.json", "onboarding-responses.json", "onboarding-tagging-responses.json", "large-rules.json", "ato-sdk-rules.json", "rasp-ruleset.json"]],
            args = ["--profile=" + profile,
                    "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                    "--rootfs=$(rlocationpath {})".format(rootfs),
                    "--app=$(rlocationpath {})".format(app),
                    "--sdk-overlay=$(rlocationpath //harness/datadog_security_extra:sdk_overlay)"] + ["--injection-flag=" + flag for flag in injection.flags],
            tags = ["datadog", "security", "lab"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_security_extra_suite", tests = tests)
