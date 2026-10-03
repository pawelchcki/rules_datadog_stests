#!/usr/bin/env python3
"""Compose a Datadog Go fixture from the pinned shared app and tracer overlay."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

APP = Path("fixtures/apps/go/realworld-gin")
LABEL = "@rules_stests//fixtures/apps/go/realworld-gin:sources"


def shared_files() -> list[tuple[Path, Path]]:
    # An explicit source checkout is useful for coordinated local development.
    # The builder still validates the resulting reviewed rootfs payload digest.
    override = os.environ.get("RULES_STESTS_SOURCE_ROOT")
    if override:
        root = Path(override).resolve() / APP
        if not root.is_dir():
            raise ValueError(f"shared application directory is missing: {root}")
        return [(source, source.relative_to(root)) for source in sorted(root.rglob("*"))
                if source.is_file() and source.name != "BUILD.bazel"
                and source.relative_to(root).parts[0] != "datadog"]
    config = "--config=" + os.environ.get("DATADOG_BAZEL_CONFIG", "local")
    listing = subprocess.check_output(
        ["bazel", "cquery", config, LABEL, "--output=files", "--noshow_progress"], text=True)
    # cquery does not create execution-root source symlinks. Read fetched
    # repositories directly from output_base/external instead.
    output_base = Path(subprocess.check_output(
        ["bazel", "info", config, "output_base"], text=True).strip())
    files = []
    for line in listing.splitlines():
        path = Path(line)
        marker = "/" + APP.as_posix() + "/"
        _, found, relative = path.as_posix().partition(marker)
        if not found or not relative or ".." in Path(relative).parts:
            raise ValueError(f"unexpected shared source path: {line}")
        files.append((path if path.is_absolute() else output_base / path, Path(relative)))
    if not files:
        raise ValueError("shared application source target is empty")
    return files


def materialize(files: list[tuple[Path, Path]], overlay: Path, output: Path) -> None:
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.mkdir(parents=True)
    for source, relative in files:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(overlay, output, dirs_exist_ok=True)
    if not (output / "Dockerfile").is_file():
        raise ValueError("fixture overlay has no Dockerfile")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    materialize(shared_files(), args.overlay, args.output)


if __name__ == "__main__":
    main()
