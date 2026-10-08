"""Native Datadog SDK experiments and concurrent RealWorld scenarios."""
load(":datadog_ruby_matrix.bzl", "DATADOG_RUBY_APPS", "ruby_matrix_fixture")
load("@rules_itest//:itest.bzl", "service_test")
load("@rules_stests//rules:defs.bzl", "corpus_service", "REALWORLD_APPS")
load("@rules_stests//rules:hurl_test.bzl", "realworld_parallel_hurl_test")
load("@rules_stests//corpus:registry.bzl", "REALWORLD_BASE_HURL_CASES")
load("//rules:injection.bzl", "datadog_env", "datadog_python_injection", "datadog_ruby_injection")

def _runfiles_data_impl(ctx):
    # rules_itest service_test includes data files but does not merge their
    # runfiles. Project the hermetic Python launcher's runfiles into its data.
    return [DefaultInfo(files = ctx.attr.target[DefaultInfo].default_runfiles.files)]

_runfiles_data = rule(
    implementation = _runfiles_data_impl,
    attrs = {"target": attr.label(mandatory = True)},
)

# Applications with native Datadog fixtures. Image-based Ruby/Go fixtures and
# the versioned Ruby matrix run through their explicitly selected suites.
_DATADOG_APPS = ["aiohttp", "django", "rails", "falcon", "gin"] + DATADOG_RUBY_APPS
_LOCAL_DATADOG_APPS = ["rails", "falcon", "gin"] + DATADOG_RUBY_APPS

# Each shared contract has independent startup, controls, receipts and failure
# status. The runner regression test binds this registry to its executable cases.
_DATADOG_SHARED_CASES = [
    "generate-128",
    "generate-64",
    "extract-64",
    "malformed-zero",
    "malformed-overflow",
    "datadog",
    "origin",
    "tracecontext",
    "b3",
    "b3multi",
    "none",
    "malformed",
    "precedence",
    "identity",
    "tags-comma",
    "tags-space",
    "tags-colon-value",
    "tags-identity-precedence",
    "agent-url-precedence",
    "sample-one",
    "sample-zero",
    "rule-precedence",
    "disabled",
    "runtime-identity",
    "tags-unicode",
    "sample-service-glob",
    "server-custom-error",
    "client-ip-override",
    "tracecontext-zero-trace",
    "tracecontext-zero-parent",
    "tracecontext-invalid-hex",
    "tracecontext-forbidden-version",
    "tracecontext-short-trace",
    "b3-zero",
    "b3-invalid-hex",
    "b3multi-zero",
    "b3multi-invalid-hex",
    "outbound-datadog",
    "outbound-tracecontext",
    "outbound-b3",
    "outbound-b3multi",
    "head",
    "query-redaction",
    "nested",
    "manual-keep",
    "manual-drop",
    "manual-drop-rule",
    "exception",
    "outbound",
    "partial-1",
    "partial-2",
    "partial-1000",
    "partial-disabled",
]

# Only these contracts execute original upstream Python method bodies.
_DATADOG_UPSTREAM_CASES = ["origin", "malformed-zero"]

def _datadog_fixture(app):
    if app in DATADOG_RUBY_APPS:
        return ruby_matrix_fixture(app)
    if app == "falcon":
        return struct(
            rootfs = "@rules_stests//harness:falcon_rootfs",
            runtime = "ruby",
            command = ["bin/server", "--host", "127.0.0.1", "--port", "$${PORT}"],
            injection = datadog_ruby_injection(),
            wires = ["v0.4"],
            profile = "ruby-falcon-datadog-v2-43-0-",
        )
    config = REALWORLD_APPS[app]
    return struct(
        rootfs = "//harness:gin_datadog_rootfs" if app == "gin" else config.rootfs,
        runtime = "native" if app == "gin" else config.runtime,
        command = ["opt/app/bin/realworld-gin-datadog"] + config.command if app == "gin" else config.command,
        injection = datadog_ruby_injection() if app == "rails" else (None if app == "gin" else datadog_python_injection(aiohttp = app == "aiohttp")),
        wires = ["v0.4"] if app in ["rails", "gin"] else ["v0.4", "v0.5"],
        profile = {"rails": "ruby-rails-datadog-v2-43-0-", "gin": "go-gin-datadog-v2-10-1-"}.get(app, "python-" + app + "-datadog-v4-15-5-"),
    )

def datadog_external_feature_tests():
    tests = []
    adapter = "//harness/upstream_datadog:adapter"
    adapter_runfiles = ":datadog_upstream_adapter_runfiles"
    _runfiles_data(name = adapter_runfiles[1:], target = adapter)
    for app in _DATADOG_APPS:
        config = _datadog_fixture(app)
        args = ["--runtime=" + config.runtime, "--rootfs=$(rlocationpath {})".format(config.rootfs)]
        data = [config.rootfs, "@rules_stests//harness:app_launcher"]
        if getattr(config, "ruby_rootfs", None):
            args.append("--ruby-rootfs=$(rlocationpath {})".format(config.ruby_rootfs))
            data.append(config.ruby_rootfs)
        if config.injection:
            args += config.injection.flags
            data.append(config.injection.rootfs)
        args += ["--"] + [arg.replace("$${PORT}", "{PORT}") for arg in config.command]
        for wire in config.wires:
            name = app + "_datadog_external_features_" + wire.replace(".", "")
            profile_tests = []
            for case in _DATADOG_SHARED_CASES:
                case_name = name + "_" + case.replace("-", "_")
                case_data = data
                upstream_args = []
                if case in _DATADOG_UPSTREAM_CASES:
                    case_data = data + [adapter, adapter_runfiles, "@datadog_system_tests_headers//file"]
                    upstream_args = [
                        "--upstream-datadog-adapter=$(rlocationpath {})".format(adapter),
                        "--upstream-datadog-test=$(rlocationpath @datadog_system_tests_headers//file)",
                    ]
                service_test(
                    name = case_name,
                    timeout = "long",
                    # Native Ruby startup needs CPU headroom across RBE shards.
                    exec_properties = {"test.EstimatedCPU": "2"},
                    services = ["@rules_stests//harness:otel_sink_service"],
                    test = "//harness/external_features:probe",
                    data = case_data,
                    args = [
                        "--protocol=datadog",
                        "--wire-version=" + wire,
                        "--app=" + app,
                        "--case=" + case,
                        "--launcher=$(rlocationpath @rules_stests//harness:app_launcher)",
                        "--launch-args='" + json.encode(args) + "'",
                    ] + upstream_args,
                    tags = ["datadog", "external-features"] + (["manual"] if app in _LOCAL_DATADOG_APPS else []),
                )
                profile_tests.append(":" + case_name)
            native.test_suite(name = name, tests = profile_tests)
            tests.extend(profile_tests)
    native.test_suite(name = "datadog_external_features_suite", tests = tests)
    native.test_suite(name = "datadog_ruby_external_features_suite", tests = [":" + app + "_datadog_external_features_v04" for app in DATADOG_RUBY_APPS], tags = ["manual", "ruby-matrix"])

def datadog_parallel_tests():
    tests = []
    sink = "//harness:datadog_stress_sink_service"
    for app in _DATADOG_APPS:
        config = _datadog_fixture(app)
        for wire in config.wires:
            suffix = wire.replace(".", "")
            name = app + "_datadog_" + suffix + "_parallel"
            corpus_service(
                name = name + "_service",
                rootfs = config.rootfs,
                ruby_rootfs = getattr(config, "ruby_rootfs", None),
                runtime = config.runtime,
                instance = app + "-datadog-stress",
                command = config.command[0],
                args = config.command[1:],
                injection = config.injection,
                env = datadog_env(service = app + "-datadog", wire_version = wire, sink = sink, extra = {
                    "RULES_STESTS_SQL_MARKERS": "true",
                    "DD_TRACE_HEADER_TAGS": "x-rules-stests-request-id:rules_stests.request_id",
                }),
                deps = [sink],
                so_reuseport_aware = app not in ["rails", "falcon"] + DATADOG_RUBY_APPS,
                autoassign_port = True,
                expected_start_duration = "5s",
                http_health_check_address = "http://127.0.0.1:$${PORT}/api/tags",
                hygienic = False,
                shutdown_timeout = "10s",
                tags = ["manual"],
            )
            realworld_parallel_hurl_test(
                name = name + "_test",
                service = ":" + name + "_service",
                profile = "//corpus:" + config.profile + suffix,
                sink = sink,
                cases = REALWORLD_BASE_HURL_CASES + ["propagation_datadog"],
                tags = ["manual", "ruby-matrix"] if app in DATADOG_RUBY_APPS else [],
            )
            tests.append(":" + name + "_test")
    native.test_suite(name = "datadog_parallel_suite", tests = tests, tags = ["manual"])
    native.test_suite(name = "datadog_ruby_parallel_suite", tests = [":" + app + "_datadog_v04_parallel_test" for app in DATADOG_RUBY_APPS], tags = ["manual", "ruby-matrix"])
