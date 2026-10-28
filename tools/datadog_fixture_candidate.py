#!/usr/bin/env python3
"""Verify locally built SDK candidates before proposing new immutable pins."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

# Operational scripts also run under Bazel's safe-path Python environment.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_tree import context_tree

METADATA_LIMIT = 1024 * 1024
LAYER_LIMIT = 4 * 1024 * 1024 * 1024
IDENTITY_LIMIT = 64 * 1024
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+"
DIGEST = r"sha256:[0-9a-f]{64}"


def document(path: Path):
    with path.open("rb") as stream:
        value = stream.read(METADATA_LIMIT + 1)
    if len(value) > METADATA_LIMIT:
        raise ValueError("OCI metadata exceeds its bound")
    return json.loads(value)


def blob(directory: Path, descriptor: dict, limit: int) -> Path:
    digest, size = descriptor.get("digest"), descriptor.get("size")
    if not isinstance(digest, str) or not re.fullmatch(DIGEST, digest):
        raise ValueError("invalid OCI digest")
    if type(size) is not int or not 0 < size <= limit:
        raise ValueError("invalid OCI blob size")
    path = directory / "blobs" / "sha256" / digest[7:]
    if path.is_symlink() or not path.is_file():
        raise ValueError("OCI blob must be a regular file")
    checksum, length = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            length += len(chunk)
            if length > size:
                raise ValueError("OCI blob size mismatch")
            checksum.update(chunk)
    if length != size or "sha256:" + checksum.hexdigest() != digest:
        raise ValueError("OCI blob content mismatch")
    return path


class HashedReader:
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()
        self.length = 0

    def read(self, size):
        value = self.stream.read(size)
        self.length += len(value)
        if self.length > LAYER_LIMIT:
            raise ValueError("uncompressed candidate payload exceeds its bound")
        self.hash.update(value)
        return value


def sdk_version(context: Path, fixture: str) -> str:
    if fixture == "ruby":
        versions = re.findall(r'^gem "datadog", "(' + VERSION + r')"$',
                              (context / "Gemfile").read_text(), re.M)
        if len(versions) != 1:
            raise ValueError("candidate requires one exact Datadog gem version")
        version = versions[0]
        lock = (context / "Gemfile.lock").read_text()
        if len(re.findall(r"^    datadog \(" + re.escape(version) + r"\)$", lock, re.M)) != 1:
            raise ValueError("candidate gem lock does not match the requested SDK")
    else:
        modules = re.findall(r"^\s*github\.com/DataDog/dd-trace-go/v2 v(" + VERSION + r")(?:\s|$)",
                             (context / "datadog/go.mod").read_text(), re.M)
        if len(modules) != 1:
            raise ValueError("candidate requires one exact Go SDK module version")
        version = modules[0]
        checksums = (context / "datadog/go.sum").read_text()
        if not re.search(r"^github\.com/DataDog/dd-trace-go/v2 v" + re.escape(version)
                         + r" h1:[A-Za-z0-9+/]{43}=$", checksums, re.M):
            raise ValueError("candidate Go SDK lacks its source checksum")
    return version


def inspect_image(directory: Path, fixture: str, version: str, module_checksum: str = "") -> dict:
    index = document(directory / "index.json")
    if index.get("schemaVersion") != 2 or len(index.get("manifests", [])) != 1:
        raise ValueError("candidate must contain one image manifest")
    descriptor = index["manifests"][0]
    if descriptor.get("mediaType") != "application/vnd.oci.image.manifest.v1+json":
        raise ValueError("candidate must use an OCI image manifest")
    manifest = document(blob(directory, descriptor, METADATA_LIMIT))
    if manifest.get("schemaVersion") != 2:
        raise ValueError("invalid candidate image manifest")
    config = document(blob(directory, manifest["config"], METADATA_LIMIT))
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ValueError("candidate must target linux amd64")
    layers = manifest.get("layers", [])
    diff_ids = config.get("rootfs", {}).get("diff_ids", [])
    if (len(layers) != 1 or len(diff_ids) != 1
            or config.get("rootfs", {}).get("type") != "layers"
            or not isinstance(diff_ids[0], str) or not re.fullmatch(DIGEST, diff_ids[0])):
        raise ValueError("candidate requires exactly one rootfs layer")
    layer = blob(directory, layers[0], LAYER_LIMIT)
    media = layers[0].get("mediaType")
    if media == "application/vnd.oci.image.layer.v1.tar+gzip":
        opened = gzip.open(layer, "rb")
    elif media == "application/vnd.oci.image.layer.v1.tar":
        opened = layer.open("rb")
    else:
        raise ValueError("unsupported candidate layer encoding")
    identity_path = {"ruby": "datadog-ruby/sdk-version.json",
                     "gin": "opt/app/datadog-build-info.txt"}[fixture]
    identity = None
    with opened as stream:
        reader = HashedReader(stream)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if name != identity_path:
                    continue
                if identity is not None or not member.isfile() or member.size > IDENTITY_LIMIT:
                    raise ValueError("invalid or duplicate SDK identity record")
                identity = archive.extractfile(member).read(IDENTITY_LIMIT + 1)
        while reader.read(64 * 1024):
            pass
        if "sha256:" + reader.hash.hexdigest() != diff_ids[0]:
            raise ValueError("candidate rootfs content mismatch")
    if identity is None:
        raise ValueError("candidate is missing its built SDK identity")
    if fixture == "ruby":
        if json.loads(identity) != {"sdk": "datadog", "version": version}:
            raise ValueError("built Ruby SDK differs from the locked version")
    elif len(re.findall(rb"^\s*dep\tgithub\.com/DataDog/dd-trace-go/v2\tv"
                        + re.escape(version.encode()) + rb"\t" + re.escape(module_checksum.encode()) + rb"$",
                        identity, re.M)) != 1 or b"\t=>\t" in identity:
        raise ValueError("built Go SDK differs from the locked source module")
    return {"manifest": descriptor["digest"], "rootfs": diff_ids[0], "version": version}


def prepare(images: Path, contexts: dict[str, Path], trees: dict[str, str]) -> dict:
    result = {"contract": "datadog.fixture-candidates/v1", "fixtures": {}}
    for fixture, context in contexts.items():
        before = context_tree(context)
        if trees.get(fixture) != before:
            raise ValueError("candidate source differs from the built context")
        version = sdk_version(context, fixture)
        module_checksum = ""
        if fixture == "gin":
            module_checksum = re.search(
                r"^github\.com/DataDog/dd-trace-go/v2 v" + re.escape(version) + r" (h1:[A-Za-z0-9+/]{43}=)$",
                (context / "datadog/go.sum").read_text(), re.M)[1]
        image = inspect_image(images / fixture, fixture, version, module_checksum)
        if before != context_tree(context):
            raise ValueError("candidate source context changed during verification")
        result["fixtures"][fixture] = dict(image, sourceTree=before)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--ruby-context", type=Path, required=True)
    parser.add_argument("--gin-context", type=Path, required=True)
    parser.add_argument("--ruby-tree", required=True)
    parser.add_argument("--gin-tree", required=True)
    args = parser.parse_args()
    receipt = args.images / "candidate.json"
    receipt.unlink(missing_ok=True)
    result = prepare(args.images, {"ruby": args.ruby_context, "gin": args.gin_context},
                     {"ruby": args.ruby_tree, "gin": args.gin_tree})
    # Only publish a complete receipt after both independent payload checks pass.
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    (args.images / "candidate.json").write_text(encoded)


if __name__ == "__main__":
    main()
