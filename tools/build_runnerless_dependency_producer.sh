#!/usr/bin/env bash
# Consume the reviewed public Rust producer in an isolated Bazel tools workspace.
# Its local execution policy is independent of my-infra's root-only RBE config.
set -euo pipefail
out="${1:?usage: build_runnerless_dependency_producer.sh OUTPUT_BINARY}"
out="$(realpath -m "$out")"
work="$(mktemp -d "${TMPDIR:-/tmp}/runnerless-producer.XXXXXX")"
cleanup() {
    (cd "$work" && bazel --ignore_all_rc_files shutdown >/dev/null 2>&1) || true
    rm -rf -- "$work"
}
trap cleanup EXIT
script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -- "$script_root/runnerless-producer-module.patch" "$work/producer-module.patch"
cp -- "$script_root/runnerless-hermetic-cc.patch" "$work/hermetic-cc.patch"
: > "$work/BUILD.bazel"
printf '%s\n' '9.2.0' > "$work/.bazelversion"
cat > "$work/MODULE.bazel" <<'MODULE'
module(name = "runnerless_dependency_producer", version = "0.0.0")
bazel_dep(name = "my_infra", version = "0.1.0")
bazel_dep(name = "hermetic_cc_toolchain", version = "4.3.0")
single_version_override(module_name = "hermetic_cc_toolchain", version = "4.3.0", patches = ["//:hermetic-cc.patch"], patch_strip = 1)
bazel_dep(name = "rules_rs", version = "0.0.110")
bazel_dep(name = "rules_python", version = "2.3.2")
python = use_extension("@rules_python//python/extensions:python.bzl", "python")
python.single_version_override(
    python_version = "3.14.6",
    sha256 = {"x86_64-unknown-linux-gnu": "ba960817e542642021135db330d875f410f4e14c64a62e2ae5faf3697ada08b2"},
    urls = ["https://github.com/astral-sh/python-build-standalone/releases/download/20260804/cpython-3.14.6%2B20260804-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"],
)
python.toolchain(is_default = True, python_version = "3.14.6")
use_repo(python, "python_3_14_6")
rust = use_extension("@rules_rs//rs:rules_rust_reexported_extensions.bzl", "rust")
rust.toolchain(edition = "2024", versions = ["1.95.0"])
use_repo(rust, "rust_toolchains")
register_toolchains("@rust_toolchains//:all")
hermetic_cc = use_extension("@hermetic_cc_toolchain//toolchain:ext.bzl", "toolchains")
use_repo(hermetic_cc, "zig_sdk")
register_toolchains("@zig_sdk//toolchain:linux_amd64_gnu.2.28")
git_override(
    module_name = "my_infra",
    remote = "https://github.com/pawelchcki/my-infra.git",
    commit = "148fe13aa8d7435a604ceb8ed24dee8baf7775b2",
    patches = ["//:producer-module.patch"],
    patch_strip = 1,
)
git_override(
    module_name = "rules_cosmo",
    remote = "https://github.com/pawelchcki/my-infra.git",
    commit = "148fe13aa8d7435a604ceb8ed24dee8baf7775b2",
    strip_prefix = "third_party/rules_cosmo",
)
MODULE
(
    cd "$work"
    bazel --ignore_all_rc_files build @my_infra//domains/runnerless:dependency_update_run
    bazel --ignore_all_rc_files cquery @my_infra//domains/runnerless:dependency_update_run --output=files > "$work/binary.path"
    read -r binary < "$work/binary.path"
    [[ -n "$binary" && -f "$binary" && -x "$binary" ]]
    mkdir -p "$(dirname "$out")"
    cp -- "$binary" "$out"
)
"$out" --help >/dev/null
