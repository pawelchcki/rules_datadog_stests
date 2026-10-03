"""Digest-locked Datadog fixture repositories."""

load("@rules_oci//oci:pull.bzl", "oci_pull")
load(":oci_images.lock.bzl", "DATADOG_PYTHON", "DATADOG_RUBY", "OCI_IMAGES")

def _impl(ctx):
    direct = []
    for name, image in dict(OCI_IMAGES, datadog_python = DATADOG_PYTHON, datadog_ruby = DATADOG_RUBY).items():
        oci_pull(
            name = name,
            image = image.repository,
            digest = image.digest,
            platforms = ["linux/amd64"],
            is_bzlmod = True,
        )
        direct.extend([name, name + "_linux_amd64"])
    if any([module.is_root for module in ctx.modules]):
        return ctx.extension_metadata(
            root_module_direct_deps = direct,
            root_module_direct_dev_deps = [],
            reproducible = True,
        )
    return ctx.extension_metadata(reproducible = True)

oci_deps = module_extension(implementation = _impl)
