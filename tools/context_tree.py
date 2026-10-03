#!/usr/bin/env python3
"""Canonical Git tree identity of the actual container build context.

Hash raw file bytes, Git executable modes and symlink targets. Directory entries
use Git's trailing-slash ordering, including named empty directories that normal
Git staging omits. No filters, ignore rules or temporary context paths apply.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat


def _object(kind: bytes, contents: bytes) -> bytes:
    return hashlib.sha1(kind + b" " + str(len(contents)).encode() + b"\0" + contents).digest()


def _tree(directory: Path) -> bytes:
    entries = []
    for path in directory.iterdir():
        mode = path.lstat().st_mode
        name = os.fsencode(path.name)
        if stat.S_ISDIR(mode):
            identity = _tree(path)
            git_mode, order = b"40000", name + b"/"
        elif stat.S_ISLNK(mode):
            identity = _object(b"blob", os.fsencode(os.readlink(path)))
            git_mode, order = b"120000", name
        elif stat.S_ISREG(mode):
            identity = _object(b"blob", path.read_bytes())
            git_mode = b"100755" if mode & stat.S_IXUSR else b"100644"
            order = name
        else:
            raise ValueError(f"unsupported container context entry: {path}")
        entries.append((order, git_mode + b" " + name + b"\0" + identity))
    return _object(b"tree", b"".join(entry for _, entry in sorted(entries)))


def context_tree(directory: Path) -> str:
    return _tree(directory).hex()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    args = parser.parse_args()
    print(context_tree(args.context))


if __name__ == "__main__":
    main()
