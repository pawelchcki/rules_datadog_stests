"""Placeholder app repository; override with checksum-verified prepared binaries."""

def _apps(ctx):
    lock = json.decode(ctx.read(ctx.attr.lock))
    lines = ["package(default_visibility = [\"//visibility:public\"])"]
    for runtime in lock["runtimes"]:
        for backend in ["plain", "orchestrion", "alibaba"]:
            stem = runtime["version"].replace(".", "_") + "_" + backend
            files = [stem + "/app", stem + "/manifest.json"]
            ctx.file(files[0], "#!/bin/sh\nexit 1\n", executable = True)
            ctx.file(files[1], json.encode({
                "status": "unprepared",
                "runtime": runtime["version"],
                "architecture": "amd64",
                "backend": backend,
                "reason": "Run tools/build_go_runtime_matrix.py and pass its --override_repository flag",
            }))
            lines.append("exports_files(" + repr(files) + ")")
            lines.append("filegroup(name=" + repr(stem) + ", srcs=" + repr(files) + ")")
    ctx.file("BUILD.bazel", "\n".join(lines))

_applications = repository_rule(implementation = _apps, attrs = {"lock": attr.label(mandatory = True)})

def _impl(ctx):
    _applications(name = "go_runtime_apps", lock = Label("//harness/go_runtime:versions.lock.json"))
    return ctx.extension_metadata(root_module_direct_deps = ["go_runtime_apps"], root_module_direct_dev_deps = [], reproducible = True)

go_runtime_deps = module_extension(implementation = _impl)
