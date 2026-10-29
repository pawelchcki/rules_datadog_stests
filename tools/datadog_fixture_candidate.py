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
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+"
DIGEST = r"sha256:[0-9a-f]{64}"


def document(path: Path):
    with path.open("rb") as stream:
        value = stream.read(METADATA_LIMIT + 1)
    if len(value) > METADATA_LIMIT:
        raise ValueError("OCI metadata exceeds its bound")
    return json.loads(value)


def evidence(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(METADATA_LIMIT + 1)
    if len(value) > METADATA_LIMIT:
        raise ValueError("SDK identity evidence exceeds its bound")
    return value


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


def go_modules(context: Path, version: str) -> dict:
    modules = {}
    for module, selected in re.findall(
            r"^[\t ]*(?:require[\t ]+)?(github\.com/DataDog/dd-trace-go/\S+)[\t ]+(v[^\s]+)",
            (context / "datadog/go.mod").read_text(), re.M):
        if selected != "v" + version or module in modules:
            raise ValueError("Go tracer and contrib versions must match the requested SDK")
        sums = re.findall(r"^" + re.escape(module + " " + selected) + r" (h1:[A-Za-z0-9+/]{43}=)$",
                          (context / "datadog/go.sum").read_text(), re.M)
        if len(sums) != 1:
            raise ValueError("Go tracer or contrib lacks one exact source checksum")
        modules[module] = sums[0]
    if "github.com/DataDog/dd-trace-go/v2" not in modules:
        raise ValueError("candidate Go graph lacks its core tracer")
    return modules


def identity_files(directory: Path, fixture: str, version: str, modules: dict) -> dict:
    if fixture == "ruby":
        identity = json.loads(evidence(directory.parent / "ruby.identity.json"))
        if identity.get("sdk") != "datadog" or identity.get("version") != version:
            raise ValueError("built Ruby SDK differs from the locked version")
        paths = {f"datadog-ruby/specifications/datadog-{version}.gemspec",
                 f"datadog-ruby/gems/datadog-{version}/lib/datadog/version.rb"}
        files = identity.get("files", [])
        if len(files) != 2 or {item.get("path") for item in files} != paths:
            raise ValueError("invalid Ruby SDK identity files")
        expected = {item["path"]: item.get("sha256", "") for item in files}
    else:
        info = evidence(directory.parent / "gin.build-info.txt")
        compiled = {}
        for line in info.splitlines():
            fields = line.lstrip(b" \t").split(b"\t")
            if len(fields) < 2 or fields[0] != b"dep" or not fields[1].startswith(b"github.com/DataDog/dd-trace-go/"):
                continue
            name = fields[1].decode()
            if (len(fields) != 4 or name in compiled or fields[2] != ("v" + version).encode()
                    or modules.get(name) != fields[3].decode()):
                raise ValueError("built Go SDK differs from the locked source modules")
            compiled[name] = fields[3].decode()
        if "github.com/DataDog/dd-trace-go/v2" not in compiled or b"\t=>\t" in info:
            raise ValueError("built Go SDK differs from the locked source modules")
        checksum = evidence(directory.parent / "gin.binary-sha256")
        match = re.fullmatch(rb"([0-9a-f]{64})  /rootfs/opt/app/bin/realworld-gin-datadog\n", checksum)
        if not match:
            raise ValueError("invalid compiled Go binary checksum")
        expected = {"opt/app/bin/realworld-gin-datadog": match[1].decode()}
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in expected.values()):
        raise ValueError("invalid SDK identity file checksum")
    return expected


def inspect_image(directory: Path, fixture: str, version: str, modules: dict | None = None,
                  source_files: dict | None = None) -> dict:
    expected = identity_files(directory, fixture, version, modules or {})
    expected.update(source_files or {})
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
    seen = set()
    with opened as stream:
        reader = HashedReader(stream)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if name not in expected:
                    continue
                if name in seen or not member.isfile():
                    raise ValueError("invalid or duplicate SDK identity file")
                seen.add(name)
                checksum = hashlib.sha256()
                with archive.extractfile(member) as source:
                    while chunk := source.read(64 * 1024):
                        checksum.update(chunk)
                if checksum.hexdigest() != expected[name]:
                    raise ValueError("SDK identity differs from the final payload bytes")
        while reader.read(64 * 1024):
            pass
        if "sha256:" + reader.hash.hexdigest() != diff_ids[0]:
            raise ValueError("candidate rootfs content mismatch")
    if seen != expected.keys():
        raise ValueError("candidate is missing its built SDK identity files")
    return {"manifest": descriptor["digest"], "rootfs": diff_ids[0], "version": version}


def prepare(images: Path, contexts: dict[str, Path], trees: dict[str, str]) -> dict:
    result = {"contract": "datadog.fixture-candidates/v1", "fixtures": {}}
    for fixture, context in contexts.items():
        before = context_tree(context)
        if trees.get(fixture) != before:
            raise ValueError("candidate source differs from the built context")
        version = sdk_version(context, fixture)
        modules = go_modules(context, version) if fixture == "gin" else {}
        source_files = {}
        if fixture == "ruby":
            activation = evidence(context / "activation.rb")
            selected = re.findall(rb'unless tracer\.version\.to_s == "(' + VERSION.encode() + rb')"', activation)
            if selected != [version.encode()]:
                raise ValueError("Ruby activation version differs from the locked SDK")
            source_files["datadog-ruby/activation.rb"] = hashlib.sha256(activation).hexdigest()
        image = inspect_image(images / fixture, fixture, version, modules, source_files)
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
