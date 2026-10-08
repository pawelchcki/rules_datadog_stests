"""Copy declared OCI layout inputs into a tree artifact without host tools."""

from pathlib import Path
import shutil
import sys


def materialize(output, inputs):
    output = Path(output)
    (output / "blobs/sha256").mkdir(parents=True)
    for name in inputs:
        source = Path(name)
        if source.parts[-3:-1] == ("blobs", "sha256"):
            target = output / "blobs/sha256" / source.name
        elif source.name in ("index.json", "oci-layout"):
            target = output / source.name
        else:
            raise ValueError("unexpected OCI layout input: " + name)
        if target.exists():
            raise ValueError("duplicate OCI layout input: " + name)
        shutil.copyfile(source, target)


if __name__ == "__main__":
    materialize(sys.argv[1], sys.argv[2:])
