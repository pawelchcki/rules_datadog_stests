"""Locked tracing gems and compatibility for the upstream Ruby runtime matrix."""

_MATRIX = Label("@rules_stests//fixtures/apps/ruby/realworld-sinatra:matrix.json")
_LOCK = Label("//bazel:datadog_ruby.lock.json")

def _config_impl(ctx):
    runtimes = json.decode(ctx.read(ctx.attr.matrix))["runtimes"]
    lock = json.decode(ctx.read(ctx.attr.lock))
    supported = []
    unsupported = []
    for runtime in runtimes:
        version = [int(part) for part in runtime["version"].split(".")]
        if version[:2] >= [2, 5] and version[:2] < [4, 1]:
            supported.append(runtime)
        else:
            unsupported.append(dict(runtime, reason = "Datadog 2.43.0 requires Ruby >= 2.5.0, < 4.1."))
    ctx.file("versions.bzl", "DATADOG_RUBY_RUNTIMES = " + repr(supported) + "\nDATADOG_RUBY_UNSUPPORTED = " + repr(unsupported) + "\nDATADOG_RUBY_GEMS = " + repr(lock["gems"]) + "\n")
    ctx.file("compatibility.json", json.encode({"tracerVersion": lock["tracerVersion"], "rubyRequirement": lock["rubyRequirement"], "supported": supported, "unsupported": unsupported}))
    ctx.file("BUILD.bazel", 'exports_files(["versions.bzl", "compatibility.json"], visibility = ["//visibility:public"])\n')

_config = repository_rule(
    implementation = _config_impl,
    attrs = {"matrix": attr.label(allow_single_file = True), "lock": attr.label(allow_single_file = True)},
)

def _gem_impl(ctx):
    filename = ctx.attr.gem + "-" + ctx.attr.version + ("-" + ctx.attr.platform if ctx.attr.platform else "")
    ctx.download("https://rubygems.org/downloads/" + filename + ".gem", sha256 = ctx.attr.sha256, output = "source.tar")
    ctx.extract("source.tar", output = "package")
    ctx.symlink("source.tar", "source.gem")
    ctx.symlink("package/metadata.gz", "specification.gz")
    ctx.extract("package/data.tar.gz", output = "data")
    ctx.file("BUILD.bazel", """package(default_visibility = ["//visibility:public"])
# Runtime payloads use the reviewed specification and importable libraries.
# Native build sources remain available to the ABI-specific compile targets.
filegroup(name = "payload", srcs = glob(
    ["data/lib/**", "data/vendor/**", "data/LICENSE*", "data/COPYING*"],
    exclude = ["data/lib/datadog/ruby_core_source/ruby-*/**", "data/vendor/**/x86_64-linux-musl/**", "data/vendor/**/include/**"],
    allow_empty = True,
) + ["specification.gz"])
filegroup(name = "native_sources", srcs = glob(["data/ext/msgpack/*.c"], allow_empty = True))
filegroup(name = "native_headers", srcs = glob(["data/ext/msgpack/*.h"], allow_empty = True))
""")

_gem = repository_rule(
    implementation = _gem_impl,
    attrs = {"gem": attr.string(), "version": attr.string(), "platform": attr.string(), "sha256": attr.string()},
)

def _extension_impl(ctx):
    lock = json.decode(ctx.read(_LOCK))
    _config(name = "datadog_ruby_matrix_config", matrix = _MATRIX, lock = _LOCK)
    repositories = ["datadog_ruby_matrix_config"]
    for gem in lock["gems"]:
        name = "datadog_gem_" + gem["name"].replace("-", "_")
        _gem(name = name, gem = gem["name"], version = gem["version"], platform = gem.get("platform", ""), sha256 = gem["sha256"])
        repositories.append(name)
    return ctx.extension_metadata(root_module_direct_deps = repositories, root_module_direct_dev_deps = [], reproducible = True)

datadog_ruby = module_extension(implementation = _extension_impl)
