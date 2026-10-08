"""Public Go runtime capability matrix with replaceable startup/attach instrumentation."""
load("@rules_itest//:itest.bzl", "service_test")
load("//harness/go_runtime:versions.bzl", "GO_RUNTIME_VERSIONS")
load(":files.bzl", "copy_file")

_PROBE = Label("//harness/go_runtime:probe")
_SINK = Label("@rules_stests//harness:otel_sink_service")

def go_runtime_capability_tests(name, backend = "orchestrion", versions = GO_RUNTIME_VERSIONS, architecture = "amd64", mode = "startup", library = None, attacher = None, attach_args = [], environment = {}, applications = {}, controls = {}, data = [], tags = []):
    """Runs observational capability checks using auto-traced or tracer-free apps.

    Custom backends use plain app binaries. Supply a library for LD_PRELOAD at
    startup, or an attacher executable for the existing PID after readiness.
    Attach argv supports {pid}, {app}, {library}, {sink}, {url}, {output}, {phase}.
    applications can override version -> struct(app, manifest, data) with labels
    from a downstream prepared repository. Default apps use @go_runtime_apps.
    """
    if backend not in ["plain", "orchestrion", "alibaba", "custom"]:
        fail("Unknown Go instrumentation backend: " + backend)
    if architecture not in ["amd64", "arm64"] or mode not in ["startup", "attach"]:
        fail("Expected Linux amd64/arm64 and startup/attach activation")
    if mode == "attach" and (backend != "custom" or not attacher):
        fail("Attach mode needs backend=custom and an attacher executable")
    if backend != "custom" and (library or attacher):
        fail("Replacement instrumentation must use backend=custom")
    tests = []
    for version in versions:
        app_backend = "plain" if backend == "custom" else backend
        stem = version.replace(".", "_") + "_" + app_backend
        application = applications.get(version)
        if application == None:
            application = struct(
                app = Label("@go_runtime_apps//:" + stem + "/app"),
                manifest = Label("@go_runtime_apps//:" + stem + "/manifest.json"),
                data = [Label("@go_runtime_apps//:" + stem)],
            )
        target = name + "_" + version.replace(".", "_") + "_test"
        control = application
        if backend in ["orchestrion", "alibaba"]:
            plain_stem = version.replace(".", "_") + "_plain"
            control = controls.get(version, struct(
                app = Label("@go_runtime_apps//:" + plain_stem + "/app"),
                manifest = Label("@go_runtime_apps//:" + plain_stem + "/manifest.json"),
                data = [],
            ))
        # Materialize declared inputs as ordinary outputs, including prepared
        # repositories below /tmp. The pinned copy tool works in sandboxes/RBE.
        app_output = target + "_files/app"
        manifest_output = target + "_files/manifest.json"
        control_app = target + "_files/control-app"
        control_manifest = target + "_files/control-manifest.json"
        for index, (source, output) in enumerate([(application.app, app_output), (application.manifest, manifest_output), (control.app, control_app), (control.manifest, control_manifest)]):
            copy_file(name = target + "_input_" + str(index), src = source, out = output, tags = tags)
        native.filegroup(
            name = target + "_inputs",
            srcs = [app_output, manifest_output, control_app, control_manifest],
            tags = tags,
        )
        args = [
            "--app=$(rlocationpath :{})".format(app_output),
            "--manifest=$(rlocationpath :{})".format(manifest_output),
            "--control-app=$(rlocationpath :{})".format(control_app),
            "--control-manifest=$(rlocationpath :{})".format(control_manifest),
            "--runtime=" + version,
            "--arch=" + architecture,
            "--backend=" + backend,
            "--mode=" + mode,
        ]
        inputs = data + [":" + app_output, ":" + manifest_output, ":" + control_app, ":" + control_manifest] + application.data + control.data
        if library:
            library = native.package_relative_label(library)
            args.append("--library=$(rlocationpath {})".format(library))
            inputs = inputs + [library]
        if attacher:
            attacher = native.package_relative_label(attacher)
            args.append("--attacher=$(rlocationpath {})".format(attacher))
            inputs = inputs + [attacher]
            args = args + ["--attach-arg=" + arg for arg in attach_args]
        args = args + ["--env=" + key + "=" + value for key, value in sorted(environment.items())]
        service_test(
            name = target,
            test = _PROBE,
            services = [_SINK],
            data = depset([native.package_relative_label(item) for item in inputs]).to_list(),
            args = args,
            tags = tags + ["go-runtime", "capability"],
            timeout = "long",
        )
        tests.append(":" + target)
    native.test_suite(name = name, tests = tests, tags = tags)
