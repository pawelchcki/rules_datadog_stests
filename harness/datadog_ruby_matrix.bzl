"""Build one native MessagePack extension per pinned Ruby ABI."""

load("@datadog_ruby_matrix_config//:versions.bzl", "DATADOG_RUBY_GEMS", "DATADOG_RUBY_RUNTIMES")
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")


def _msgpack_impl(ctx):
    headers = ctx.attr.runtime[OutputGroupInfo].headers.to_list()[0]
    prefix = headers.path + "/usr/local/include/ruby-" + ctx.attr.abi
    toolchain = find_cc_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = toolchain)
    defines = ["HAVE_RB_PROC_CALL_WITH_BLOCK", "HASH_ASET_DEDUPE=1"]
    defines.append("STR_UMINUS_DEDUPE_FROZEN=" + ("1" if ctx.attr.abi >= "3.0.0" else "0"))
    if ctx.attr.abi >= "3.0.0":
        defines.append("HAVE_RB_ENC_INTERNED_STR")
    if ctx.attr.abi >= "3.2.0":
        defines.append("HAVE_RB_HASH_NEW_CAPA")
    _, objects = cc_common.compile(
        name = ctx.label.name,
        actions = ctx.actions,
        cc_toolchain = toolchain,
        feature_configuration = features,
        srcs = ctx.files.srcs,
        public_hdrs = ctx.files.hdrs,
        includes = [prefix, prefix + "/x86_64-linux", prefix + "/x86_64-linux-gnu"],
        additional_inputs = [headers],
        defines = defines,
        disallow_nopic_outputs = True,
        user_compile_flags = ["-O2", "-g0", "-std=gnu99", "-Wno-incompatible-function-pointer-types", "-Wno-int-conversion", "-Wno-compound-token-split-by-macro", "-Wno-ignored-attributes"],
    )
    output = ctx.actions.declare_file(ctx.label.name + ".so")
    ctx.actions.run(
        executable = cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.c_compile),
        arguments = ["-fuse-ld=lld", "-shared", "-nostdlib", "-o", output.path] + [obj.path for obj in objects.pic_objects],
        inputs = depset(objects.pic_objects, transitive = [toolchain.all_files]),
        outputs = [output],
        mnemonic = "DatadogMessagePackLink",
    )
    return [DefaultInfo(files = depset([output]))]

_msgpack = rule(
    implementation = _msgpack_impl,
    attrs = {"runtime": attr.label(mandatory = True), "abi": attr.string(), "srcs": attr.label_list(allow_files = True), "hdrs": attr.label_list(allow_files = True)},
    toolchains = use_cc_toolchain(),
    fragments = ["cpp"],
)

def _payload_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    runtime = ctx.attr.runtime[DefaultInfo].files.to_list()[0]
    app = ctx.attr.app[DefaultInfo].files.to_list()[0]
    args = ctx.actions.args()
    args.add_all([output.path, runtime.path, app.path, ctx.executable._launcher.path, ctx.file._bootstrap.path, ctx.file.msgpack.path, ctx.file._activation.path, ctx.file._probes.path, ctx.file._paths.path])
    args.add_all(ctx.files.gems)
    ctx.actions.run(
        executable = ctx.executable._builder,
        arguments = [args],
        inputs = [runtime, app, ctx.file.msgpack, ctx.file._bootstrap, ctx.file._activation, ctx.file._probes, ctx.file._paths] + ctx.files.gems,
        tools = [ctx.executable._builder, ctx.executable._launcher],
        outputs = [output],
        mnemonic = "DatadogRubyPayload",
        progress_message = "Checking Datadog 2.43.0 on %{label}",
    )
    return [DefaultInfo(files = depset([output]), runfiles = ctx.runfiles(files = [output]))]

_payload = rule(
    implementation = _payload_impl,
    attrs = {
        "runtime": attr.label(mandatory = True),
        "app": attr.label(mandatory = True),
        "msgpack": attr.label(allow_single_file = True),
        "gems": attr.label_list(allow_files = True),
        "_builder": attr.label(default = Label("//tools:build_datadog_ruby"), executable = True, cfg = "exec"),
        "_launcher": attr.label(default = Label("@rules_stests//harness:app_launcher"), executable = True, cfg = "exec"),
        "_bootstrap": attr.label(default = Label("//fixtures/agents/datadog-ruby:matrix_build.rb"), allow_single_file = True),
        "_activation": attr.label(default = Label("//fixtures/agents/datadog-ruby:matrix_activation.rb"), allow_single_file = True),
        "_paths": attr.label(default = Label("//fixtures/agents/datadog-ruby:matrix_paths.rb"), allow_single_file = True),
        "_probes": attr.label(default = Label("//fixtures/agents/datadog-ruby:matrix_probes.rb"), allow_single_file = True),
    },
)

def datadog_ruby_payloads():
    for runtime in DATADOG_RUBY_RUNTIMES:
        app = "ruby_" + runtime["series"].replace(".", "_")
        interpreter = "@rules_stests//fixtures:" + app + "_runtime"
        _msgpack(
            name = app + "_datadog_msgpack",
            runtime = interpreter,
            abi = runtime["abi"],
            srcs = ["@datadog_gem_msgpack//:native_sources"],
            hdrs = ["@datadog_gem_msgpack//:native_headers"],
        )
        _payload(
            name = app + "_datadog_rootfs",
            runtime = interpreter,
            app = "@rules_stests//fixtures:" + app + "_rootfs",
            msgpack = ":" + app + "_datadog_msgpack",
            gems = ["@datadog_gem_" + gem["name"].replace("-", "_") + "//:payload" for gem in DATADOG_RUBY_GEMS],
        )
