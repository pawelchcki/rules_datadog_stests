#!/usr/bin/env python3
"""Retain matrix evidence, including read-only Bazel outputs, across reruns."""
import argparse
from pathlib import Path
import shutil
import zipfile


def writable_directories(root):
    for path in [root, *root.rglob('*')]:
        if path.is_dir():
            path.chmod(path.stat().st_mode | 0o700)


def retain(logs, applications, output, package='fixtures', versions=None):
    for source in (logs/package).glob('*/test.outputs'):
        if package == 'fixtures' and not source.parent.name.startswith('go_runtime_'):
            continue
        if versions and not any(source.parent.name.endswith('_' + version.replace('.', '_') + '_test') for version in versions):
            continue
        destination = output/'tests'/source.parent.relative_to(logs)
        if destination.exists():
            writable_directories(destination)
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        writable_directories(destination)
        archive = destination/'outputs.zip'
        if archive.exists():
            with zipfile.ZipFile(archive) as stream:
                stream.extractall(destination)
        for name in ('test.log', 'test.xml'):
            path = source.parent/name
            if path.exists():
                shutil.copy2(path, destination/name)
    for manifest in applications.glob('go1_*_*/manifest.json'):
        if versions and not manifest.parent.name.startswith(tuple(version.replace('.', '_') + '_' for version in versions)):
            continue
        destination = output/'applications'/manifest.parent.name
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('app', 'manifest.json', 'build.log'):
            source = manifest.parent/name
            if source.exists():
                (destination/name).unlink(missing_ok=True)
                shutil.copy2(source, destination/name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logs', required=True, type=Path)
    parser.add_argument('--applications', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--package', default='fixtures')
    parser.add_argument('--version', action='append')
    args = parser.parse_args()
    retain(args.logs, args.applications, args.output, args.package, args.version)


if __name__ == '__main__':
    main()
