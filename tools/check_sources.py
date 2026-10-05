#!/usr/bin/env python3
"""Validate syntax of tracked Python, Bash and JSON files without building fixtures."""

import json
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    for name in files:
        path = root / name
        if path.suffix == ".py":
            compile(path.read_bytes(), name, "exec")
        elif path.suffix == ".sh":
            subprocess.run(["bash", "-n", str(path)], check=True)
        elif path.suffix == ".json":
            json.loads(path.read_text())
    print("Tracked Python, shell and JSON syntax is valid.")


if __name__ == "__main__":
    main()
