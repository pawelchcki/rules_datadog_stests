"""Datadog tracing on every compatible upstream Ruby runtime."""

load("@datadog_ruby_matrix_config//:versions.bzl", "DATADOG_RUBY_RUNTIMES")
load("@rules_stests//corpus:registry.bzl", "REALWORLD_BASE_HURL_CASES")
load("@rules_stests//rules:defs.bzl", "corpus_service", "realworld_service_tests")
load("//rules:injection.bzl", "datadog_env", "datadog_ruby_injection")

DATADOG_RUBY_APPS = ["ruby_" + runtime["series"].replace(".", "_") for runtime in DATADOG_RUBY_RUNTIMES]

def ruby_matrix_fixture(app):
    """Version-specific application, interpreter and tracing payload labels."""
    return struct(
        rootfs = "@rules_stests//fixtures:" + app + "_rootfs",
        ruby_rootfs = "@rules_stests//fixtures:" + app + "_runtime",
        runtime = "ruby",
        command = ["bin/server", "--host", "127.0.0.1", "--port", "$${PORT}"],
        injection = datadog_ruby_injection(rootfs = "//harness:" + app + "_datadog_rootfs"),
        wires = ["v0.4"],
        profile = "ruby-sinatra-" + app[5:].replace("_", "-") + "-datadog-v2-43-0-",
    )

def datadog_ruby_matrix_tests():
    tests = []
    for app in DATADOG_RUBY_APPS:
        config = ruby_matrix_fixture(app)
        corpus_service(
            name = app + "_datadog_service",
            rootfs = config.rootfs,
            ruby_rootfs = config.ruby_rootfs,
            runtime = config.runtime,
            instance = app + "-datadog",
            command = config.command[0],
            args = config.command[1:],
            injection = config.injection,
            env = datadog_env(service = app + "-datadog", wire_version = "v0.4", extra = {"LANG": "C.UTF-8"}),
            deps = ["@rules_stests//harness:telemetry_sink_service"],
            autoassign_port = True,
            so_reuseport_aware = False,
            expected_start_duration = "5s",
            http_health_check_address = "http://127.0.0.1:$${PORT}/api/tags",
            hygienic = False,
            shutdown_timeout = "10s",
            tags = ["manual", "ruby-matrix"],
        )
        realworld_service_tests(
            name = app + "_datadog",
            service = ":" + app + "_datadog_service",
            telemetry_profile = "//corpus:" + config.profile + "v04",
            telemetry_sink = "@rules_stests//harness:telemetry_sink_service",
            scenarios = REALWORLD_BASE_HURL_CASES + ["propagation_datadog"],
            tags = ["datadog", "ruby-matrix", "manual"],
        )
        tests.extend([":" + app + "_datadog_hurl_test", ":" + app + "_datadog_test", ":" + app + "_datadog_service_hygiene_test"])
    native.test_suite(name = "datadog_ruby_matrix_suite", tests = tests, tags = ["datadog", "ruby-matrix", "manual"])
