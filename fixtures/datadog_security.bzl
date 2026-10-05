"""Security lab on the existing Django runtime and pinned Python tracer."""
load("@rules_itest//:itest.bzl", "service_test")
load("//rules:injection.bzl", "datadog_python_injection")

def datadog_security_tests():
    app = "//harness/datadog_security:app.py"
    rootfs = "@rules_stests//harness:django_rootfs"
    injection = datadog_python_injection()
    tests = []
    for profile, wire in [(p, w) for p in ["default", "waf-controls", "rasp-controls", "api-enabled", "api-disabled", "appsec-standalone", "iast-standalone"] for w in ["v0.4", "v0.5"]]:
        name = "datadog_security_" + ("waf_" if profile == "waf-controls" else "rasp_" if profile == "rasp-controls" else profile.replace("-", "_") + "_" if profile not in ("default", "waf-controls", "rasp-controls") else "") + wire.replace(".", "") + "_test"
        # The bulk profile retains all source/sink reports together. Native
        # binary backing counts toward capture memory, so use the stress sink.
        sink = "//harness:datadog_stress_sink_service" if profile == "default" else "@rules_stests//harness:otel_sink_service"
        service_test(
            name = name,
            timeout = "long",
            services = [sink],
            test = "//harness/datadog_security:probe",
            data = [app, "//harness/datadog_security:security_views.py", rootfs, injection.rootfs, "@rules_stests//harness:app_launcher"],
            args = [
                "--wire=" + wire,
                "--profile=" + profile,
                "--sink-suffix=" + sink.replace("@rules_stests", ""),
                "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                "--rootfs=$(rlocationpath {})".format(rootfs),
                "--app=$(rlocationpath {})".format(app),
            ] + ["--injection-flag=" + flag for flag in injection.flags],
            tags = ["datadog", "security", "lab"],
        )
        tests.append(":" + name)
    native.test_suite(name = "datadog_security_suite", tests = tests)
