#!/usr/bin/env python3
"""Publish verified SDK candidates and prepare their coordinated immutable pins."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
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
    # Registry credentials remain in the existing host's container auth.
    result = subprocess.run([str(a) for a in arguments], cwd=root, check=True,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.stdout.decode().strip() if capture else None


def clean_contexts(root: Path) -> None:
    for context, *_ in FIXTURES.values():
        if run(["git", "status", "--porcelain", "--untracked-files=all", "--", context], root=root, capture=True):
            raise ValueError("publication requires committed source contexts")


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
    original_oci = (root / OCI_LOCK).read_text()
    original_payload = (root / PAYLOAD_LOCK).read_text()
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
        if (root / OCI_LOCK).read_text() != original_oci or (root / PAYLOAD_LOCK).read_text() != original_payload:
            raise ValueError("publication locks changed concurrently")
        # Both provider operations finish before installing either tracked lock.
        # A failed/interrupted producer cannot emit a candidate from a dirty tree.
        (root / OCI_LOCK).write_text(updated_oci)
        if candidate:
            updated_payload = {key: {field: pin[field] for field in ("version", "rootfs")}
                               for key, pin in pins.items()}
            (root / PAYLOAD_LOCK).write_text(json.dumps(updated_payload, indent=2) + "\n")
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
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Datadog fixture publication failed; candidate not accepted")


if __name__ == "__main__":
    main()
