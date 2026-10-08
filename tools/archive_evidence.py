"""Archive retained proof bytes with stable paths and normalized metadata."""

import gzip
import hashlib
from pathlib import Path
import stat
import sys
import tarfile


def archive_evidence(source, destination):
    source = Path(source)
    destination = Path(destination)
    # Stream compression: captures can be much larger than runner memory.
    with destination.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0, compresslevel=6) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                # Scenario snapshots repeat validators and diagnostics. Store
                # identical files once while preserving every evidence path.
                contents_by_digest = {}
                for path in [source, *sorted(source.rglob("*"))]:
                    name = "datadog-evidence"
                    if path != source:
                        name += "/" + path.relative_to(source).as_posix()
                    info = archive.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.pax_headers = {}
                    info.mode = 0o755 if info.isdir() or info.mode & stat.S_IXUSR else 0o644
                    if info.isfile():
                        with path.open("rb") as contents:
                            digest = hashlib.file_digest(contents, "sha256").digest()
                            key = (digest, info.size, info.mode)
                            if key in contents_by_digest:
                                info.type = tarfile.LNKTYPE
                                info.linkname = contents_by_digest[key]
                                info.size = 0
                                archive.addfile(info)
                            else:
                                contents_by_digest[key] = name
                                contents.seek(0)
                                archive.addfile(info, contents)
                    else:
                        archive.addfile(info)


if __name__ == "__main__":
    archive_evidence(*sys.argv[1:])
