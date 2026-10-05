"""Extract reviewed wheel archives, rejecting traversal, symlinks and collisions."""
import pathlib
import stat
import sys
import zipfile


def extract(destination, wheels):
    root = pathlib.Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                path = pathlib.PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise ValueError("unsafe wheel path: " + member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("wheel symlinks unsupported")
                if member.is_dir():
                    continue
                parts = path.parts
                if parts[0].endswith(".data"):
                    if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                        continue  # Console scripts/data are not imported by this lab.
                    path = pathlib.PurePosixPath(*parts[2:])
                target = root / path
                if target.exists():
                    raise ValueError("wheel collision: " + str(path))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))


if __name__ == "__main__":
    extract(sys.argv[1], sys.argv[2:])
