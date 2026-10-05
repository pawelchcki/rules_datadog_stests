"""Build a Python import directory without pip or network access at test time."""

def _overlay_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(out.path)
    args.add_all(ctx.files.wheels)
    ctx.actions.run(executable = ctx.attr._extractor[DefaultInfo].files_to_run, arguments = [args], inputs = ctx.files.wheels, outputs = [out], mnemonic = "WheelOverlay")
    return [DefaultInfo(files = depset([out]), runfiles = ctx.runfiles(files = [out]))]

wheel_overlay = rule(implementation = _overlay_impl, attrs = {
    "wheels": attr.label(mandatory = True, allow_files = True),
    "_extractor": attr.label(default = "//harness/wheel_overlay:extract", executable = True, cfg = "exec"),
})
