#!/usr/bin/env python3
"""Publish verified SDK candidates and prepare their coordinated immutable pins."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_tree import context_tree
from datadog_fixture_candidate import DIGEST, VERSION, document, prepare, sdk_version
from materialize_shared_app import APP, materialize, shared_files

FIXTURES = {
    "ruby": ("fixtures/agents/datadog-ruby", "ghcr.io/pawelchcki/rules_stest_agents", "datadog_ruby", "datadog-ruby"),
    "gin": (APP.as_posix(), "ghcr.io/pawelchcki/rules_stest_apps", "gin_datadog_realworld", "gin-datadog"),
}
PAYLOAD_LOCK = Path("bazel/datadog_fixture_payloads.json")
OCI_LOCK = Path("bazel/oci_images.lock.bzl")


class PublicationCommandError(subprocess.SubprocessError):
    """A fixed diagnostic which never retains child arguments or output."""


def command_failure(argv: list[str], status: int, stderr: bytes) -> PublicationCommandError:
    operation = "command"
    reason = "command-failed"
    if argv[:2] == ["skopeo", "copy"]:
        operation = "registry-transfer"
        for fixture, (_, repository, _, _) in FIXTURES.items():
            if argv[-1].startswith("docker://" + repository + ":"):
                operation = fixture + "-registry-push"
            elif argv[-2].startswith("docker://" + repository + "@"):
                operation = fixture + "-anonymous-pull"
        # Inspect private bytes only to select a fixed category. Never include
        # provider text, command arguments or credential material in an error.
        diagnostic = stderr.lower()
        categories = (
            ("registry-permission-denied", (b"permission_denied:write_package", b"permission_denied: write_package", b"requested access to the resource is denied")),
            ("registry-authentication-failed", (b"unauthorized", b"authentication required")),
            ("invalid-json-input", (b"unexpected end of json input",)),
            ("registry-manifest-missing", (b"manifest unknown",)),
            ("registry-unreachable", (b"i/o timeout", b"context deadline exceeded", b"connection refused", b"tls handshake timeout", b"temporary failure in name resolution")),
        )
        for category, patterns in categories:
            if any(pattern in diagnostic for pattern in patterns):
                reason = category
                break
    elif len(argv) > 3 and argv[1].endswith("/tools/local_oci_repository.py"):
        operation = "payload-verification"
        for fixture, (_, _, name, _) in FIXTURES.items():
            if argv[3] == name:
                operation = fixture + "-payload-verification"
    elif argv[:2] == ["git", "status"]:
        operation = "source-status"
    return PublicationCommandError(
        f"Datadog fixture publication failed ({operation}; {reason}; exit {status}); candidate not accepted")


def payloads(path: Path) -> dict:
    result = document(path)
    if not isinstance(result, dict) or set(result) != set(FIXTURES):
        raise ValueError("payload lock requires Ruby and Gin")
    for item in result.values():
        if (not isinstance(item, dict) or set(item) != {"version", "rootfs"}
                or not isinstance(item["version"], str) or not re.fullmatch(VERSION, item["version"])
                or not isinstance(item["rootfs"], str) or not re.fullmatch(DIGEST, item["rootfs"])):
            raise ValueError("invalid reviewed payload pin")
    return result


def run(arguments, *, root: Path, capture=False):
    # Existing container keyring auth remains available to local hosts. Actions
    # may instead supply their ephemeral scoped token entirely through memory.
    argv=[str(a) for a in arguments]
    environment=dict(os.environ)
    token=environment.pop("DATADOG_REGISTRY_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    environment.pop("GH_TOKEN", None)
    authentication=None
    if token and argv[:2]==["skopeo","copy"] and argv[-1].startswith((
            "docker://ghcr.io/pawelchcki/rules_stest_agents:",
            "docker://ghcr.io/pawelchcki/rules_stest_apps:")):
        username=environment.get("DATADOG_REGISTRY_USER", "")
        if not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}",username) or len(token)>4096 or any(c in token for c in "\r\n"):
            raise ValueError("invalid registry host identity")
        encoded=base64.b64encode((username+":"+token).encode()).decode()
        authentication=json.dumps({"auths":{"ghcr.io":{"auth":encoded}}}).encode()
        argv[2:2]=["--authfile","/dev/stdin"]
    failure = None
    try:
        result = subprocess.run(argv, cwd=root, check=True, input=authentication,env=environment,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        failure = command_failure(argv, error.returncode, error.stderr or b"")
    # Raise outside the handler so the public error has no exception context
    # containing the child's private stderr or authentication-bearing input.
    if failure is not None:
        raise failure
    return result.stdout.decode().strip() if capture else None


def clean_contexts(root: Path) -> None:
    if os.environ.get("RULES_STESTS_SOURCE_ROOT"):
        raise ValueError("publication cannot use a development shared-source override")
    selections = ["MODULE.bazel", "MODULE.bazel.lock", ".bazelrc", ".bazelversion"]
    for context in [value[0] for value in FIXTURES.values()] + selections:
        if run(["git", "status", "--porcelain", "--untracked-files=all", "--", context], root=root, capture=True):
            raise ValueError("publication requires committed source contexts")


def install_locks(root: Path, replacements: dict, originals: dict) -> None:
    # Stage every new file and rollback copy before replacing any tracked file.
    # Each rename is atomic. Catchable errors/interruptions restore originals;
    # an uncatchable process kill leaves an unaccepted dirty producer checkout.
    staging = Path(tempfile.mkdtemp(prefix=".datadog-pins-", dir=root / "bazel"))
    installed = []
    cleanup = True
    try:
        for index, (path, content) in enumerate(replacements.items()):
            for suffix, value in (("new", content), ("old", originals[path])):
                staged = staging / f"{index}.{suffix}"
                with staged.open("wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                staged.chmod(stat.S_IMODE((root / path).stat().st_mode))
        for index, path in enumerate(replacements):
            installed.append((index, path))
            os.replace(staging / f"{index}.new", root / path)
    except BaseException:
        # If storage also refuses rollback, retain both original copies for
        # recovery. Never report that candidate as accepted.
        cleanup = False
        for index, path in reversed(installed):
            os.replace(staging / f"{index}.old", root / path)
        cleanup = True
        raise
    finally:
        if cleanup:
            shutil.rmtree(staging)


def update_lock(text: str, name: str, pin: dict) -> str:
    start = (r'^DATADOG_RUBY = struct\(\n' if name == "datadog_ruby"
             else r'^    "gin_datadog_realworld": struct\(\n')
    matches = list(re.finditer(start + r'.*?^\s*\),?$', text, re.M | re.S))
    if len(matches) != 1:
        raise ValueError("unexpected OCI lock structure")
    match = matches[0]
    block = match.group()
    for field, value in (("digest", pin["manifest"]), ("tree", pin["sourceTree"])):
        block, count = re.subn(r'(?m)^(\s+' + field + r' = ")[^"]+(",)$',
                               lambda m: m[1] + value + m[2], block)
        if count != 1:
            raise ValueError("missing immutable OCI pin")
    if name == "datadog_ruby":
        block, count = re.subn(r'(?m)^(\s+version = ")[^"]+(",)$',
                               lambda m: m[1] + pin["version"] + m[2], block)
        if count != 1:
            raise ValueError("missing Ruby OCI version")
    return text[:match.start()] + block + text[match.end():]


def publish(root: Path, images: Path, *, candidate: bool) -> None:
    root, images = root.resolve(), images.resolve()
    clean_contexts(root)
    reviewed = payloads(root / PAYLOAD_LOCK)
    originals = {path: (root / path).read_bytes() for path in (OCI_LOCK, PAYLOAD_LOCK)}
    original_oci = originals[OCI_LOCK].decode()
    with tempfile.TemporaryDirectory(prefix="datadog-publication-") as scratch:
        scratch = Path(scratch)
        materialize(shared_files(root), root / APP, scratch / "gin")
        contexts = {"ruby": root / FIXTURES["ruby"][0], "gin": scratch / "gin"}
        trees = {fixture: context_tree(context) for fixture, context in contexts.items()}
        if candidate:
            # A receipt cannot authorize different bytes: inspect both actual
            # images, SDK identities and freshly composed source contexts again.
            selected = prepare(images, contexts, trees)
            if selected != document(images / "candidate.json"):
                raise ValueError("candidate receipt differs from verified images and sources")
            pins = selected["fixtures"]
        else:
            if (images / "gin.source-tree").read_text().strip() != trees["gin"]:
                raise ValueError("Gin publication source differs from the built context")
            pins = {}
            for fixture in FIXTURES:
                if sdk_version(contexts[fixture], fixture) != reviewed[fixture]["version"]:
                    raise ValueError("source SDK version differs from reviewed payload pin")
                manifests = document(images / fixture / "index.json").get("manifests", [])
                if len(manifests) != 1:
                    raise ValueError("publication requires one immutable image")
                pins[fixture] = dict(reviewed[fixture], manifest=manifests[0]["digest"], sourceTree=trees[fixture])
        updated_oci = original_oci
        for fixture, (_, _, name, _) in FIXTURES.items():
            pin = pins[fixture]
            if not re.fullmatch(DIGEST, pin["manifest"]):
                raise ValueError("invalid publication manifest")
            run([sys.executable, root / "tools/local_oci_repository.py", images / fixture, name,
                 "--digest", pin["manifest"], "--rootfs-digest", pin["rootfs"]], root=root)
            updated_oci = update_lock(updated_oci, name, pin)
        # Every payload and proposed lock passes before the first registry write.
        for fixture, (_, repository, name, tag) in FIXTURES.items():
            pin = pins[fixture]
            clean_contexts(root)
            if any(context_tree(context) != trees[key] for key, context in contexts.items()):
                raise ValueError("publication source changed during verification")
            destination = f"docker://{repository}:{tag}-tree-{pin['sourceTree']}-{pin['manifest'][7:]}"
            run(["skopeo", "copy", "--preserve-digests", "oci:" + str(images / fixture), destination], root=root)
            pulled = scratch / (fixture + "-pulled")
            run(["skopeo", "copy", "--src-no-creds", "--preserve-digests",
                 f"docker://{repository}@{pin['manifest']}", "oci:" + str(pulled)], root=root)
            run([sys.executable, root / "tools/local_oci_repository.py", pulled, name,
                 "--digest", pin["manifest"], "--rootfs-digest", pin["rootfs"]], root=root)
        clean_contexts(root)
        if any(context_tree(context) != trees[key] for key, context in contexts.items()):
            raise ValueError("publication source changed during anonymous verification")
        if any((root / path).read_bytes() != original for path, original in originals.items()):
            raise ValueError("publication locks changed concurrently")
        replacements = {OCI_LOCK: updated_oci.encode()}
        if candidate:
            updated_payload = {key: {field: pin[field] for field in ("version", "rootfs")}
                               for key, pin in pins.items()}
            replacements[PAYLOAD_LOCK] = (json.dumps(updated_payload, indent=2) + "\n").encode()
        install_locks(root, replacements, originals)
        for fixture, (_, repository, _, _) in FIXTURES.items():
            pin = pins[fixture]
            print(f"{fixture} {repository}@{pin['manifest']} source-tree={pin['sourceTree']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path)
    parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args()
    try:
        publish(Path(__file__).resolve().parents[1], args.images, candidate=args.candidate)
    except PublicationCommandError as error:
        sys.exit(str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Datadog fixture publication failed; candidate not accepted")


if __name__ == "__main__":
    main()
