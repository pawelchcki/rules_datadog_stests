"""Fetch individually hashed wheels from reviewed, platform-specific JSON locks."""

def _bundle_impl(ctx):
    lock = json.decode(ctx.read(ctx.attr.lock))
    if lock["python"] not in ("3.11", "3.12") or lock["platform"] != "manylinux2014_x86_64":
        fail("Only the pinned CPython 3.11/3.12 Linux lab platforms are supported")
    for wheel in lock["wheels"]:
        filename = wheel["filename"]
        if "/" in filename or not filename.endswith(".whl"):
            fail("Invalid wheel filename: " + filename)
        ctx.download(url = wheel["url"], output = filename, sha256 = wheel["sha256"])
    ctx.file("BUILD.bazel", 'filegroup(name="wheels", srcs=glob(["*.whl"]), visibility=["//visibility:public"])\n')

_bundle = repository_rule(implementation = _bundle_impl, attrs = {"lock": attr.label(mandatory = True)})

def _extension_impl(ctx):
    for mod in ctx.modules:
        for bundle in mod.tags.bundle:
            _bundle(name = bundle.name, lock = bundle.lock)

wheel_deps = module_extension(
    implementation = _extension_impl,
    tag_classes = {"bundle": tag_class(attrs = {"name": attr.string(mandatory = True), "lock": attr.label(mandatory = True)})},
)
