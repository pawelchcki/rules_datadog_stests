"""Materialize declared files with the pinned coreutils toolchain."""

_COREUTILS = "@aspect_bazel_lib//lib:coreutils_toolchain_type"

def _copy_impl(ctx):
    ctx.actions.run(
        executable = ctx.toolchains[_COREUTILS].coreutils_info.bin,
        arguments = ["cp", ctx.file.src.path, ctx.outputs.out.path],
        inputs = [ctx.file.src],
        outputs = [ctx.outputs.out],
        mnemonic = "MaterializeFile",
        toolchain = _COREUTILS,
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]))]

copy_file = rule(
    implementation = _copy_impl,
    attrs = {"src": attr.label(mandatory = True, allow_single_file = True), "out": attr.output(mandatory = True)},
    toolchains = [_COREUTILS],
)

def _layout_impl(ctx):
    output = ctx.actions.declare_directory("layout")
    args = ctx.actions.args()
    args.add(output.path)
    args.add_all(ctx.files.srcs)
    ctx.actions.run(
        executable = ctx.attr._assembler[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = ctx.files.srcs,
        outputs = [output],
        mnemonic = "MaterializeOciLayout",
    )
    return [DefaultInfo(files = depset([output]))]

oci_layout = rule(
    implementation = _layout_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "_assembler": attr.label(default = Label("//tools:materialize_oci_layout"), executable = True, cfg = "exec"),
    },
)
