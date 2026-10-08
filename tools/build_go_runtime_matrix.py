#!/usr/bin/env python3
"""Build pinned, dynamically linked Go capability apps and a local Bazel repository."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(entry, cache):
    path = cache / entry['sha256']
    if not path.exists() or sha(path) != entry['sha256']:
        temporary = path.with_suffix('.tmp')
        with urlopen(entry['url'], timeout=60) as source, temporary.open('wb') as output:
            shutil.copyfileobj(source, output)
        assert sha(temporary) == entry['sha256'], 'Download checksum mismatch: ' + entry['url']
        temporary.replace(path)
    return path


def elf_metadata(path):
    data = Path(path).read_bytes()
    assert data[:4] == b'\x7fELF' and data[4] == 2 and data[5] == 1, 'Expected little-endian ELF64'
    header = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
    _, machine, _, _, phoff, shoff, _, _, phsize, phnum, shsize, shnum, shstr = header
    interpreter = None
    for index in range(phnum):
        kind, _, offset, _, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', data, phoff+index*phsize)
        if kind == 3:
            interpreter = data[offset:offset+size].rstrip(b'\0').decode()
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff+index*shsize) for index in range(shnum)]
    strings = data[sections[shstr][4]:sections[shstr][4]+sections[shstr][5]]
    pclntab = None
    for section in sections:
        name = strings[section[0]:].split(b'\0', 1)[0]
        if name == b'.gopclntab':
            pclntab = data[section[4]:section[4]+4].hex()
    assert interpreter, 'Capability binaries must be dynamically linked for LD_PRELOAD'
    return dict(machine=machine, interpreter=interpreter, pclntabMagic=pclntab, sha256=sha(path))


def run(command, cwd, env, log):
    with log.open('ab') as output:
        output.write(('\n' + repr(command) + '\n').encode())
        subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)


def build(args):
    lock = json.loads((ROOT/'harness/go_runtime/versions.lock.json').read_text())
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache).resolve() if args.cache else output/'cache'
    cache.mkdir(parents=True, exist_ok=True)
    architecture = args.arch
    host_arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    assert architecture == host_arch, 'Use a native Linux ' + architecture + ' builder (CGO is required)'
    assert platform.system() == 'Linux', 'The capability matrix requires Linux'
    rows = [row for row in lock['runtimes'] if not args.version or row['version'] in args.version]
    if args.version:
        assert {row['version'] for row in rows} == set(args.version), 'Unknown pinned Go version'
    source_paths = sorted((ROOT/'harness/go_runtime/app').glob('*.go')) + sorted((ROOT/'harness/go_runtime/app').glob('*.c'))
    fingerprint = hashlib.sha256()
    for path in [Path(__file__), ROOT/'harness/go_runtime/versions.lock.json', *source_paths,
                 ROOT/'harness/go_runtime/orchestrion/go.mod', ROOT/'harness/go_runtime/orchestrion/go.sum',
                 ROOT/'harness/go_runtime/orchestrion/orchestrion.tool.go']:
        fingerprint.update(str(path.relative_to(ROOT)).encode()+b'\0'+path.read_bytes()+b'\0')
    for row in rows:
        version = row['version']
        sdk_entry = row['toolchains'].get(architecture)
        if not sdk_entry:
            for backend in args.backend:
                directory = output/(version.replace('.', '_')+'_'+backend)
                directory.mkdir(exist_ok=True)
                (directory/'app').write_text('#!/bin/sh\nexit 1\n')
                (directory/'manifest.json').write_text(json.dumps(dict(
                    schemaVersion=1, runtime=version, architecture=architecture, backend=backend,
                    status='unsupported-toolchain', reason='No official Linux '+architecture+' archive'), indent=2)+'\n')
            continue
        sdk_root = cache / (version+'-'+architecture)
        if not (sdk_root/'go/bin/go').exists():
            archive = download(sdk_entry, cache)
            sdk_root.mkdir(exist_ok=True)
            with tarfile.open(archive) as stream:
                stream.extractall(sdk_root, filter='data')
        go = sdk_root/'go/bin/go'
        actual = subprocess.check_output([str(go), 'version'], text=True,
                                         env=dict(os.environ, GOROOT=str(sdk_root/'go'), GOTOOLCHAIN='local'))
        assert ('go version '+version+' linux/'+architecture) in actual, actual
        for backend in args.backend:
            stem = version.replace('.', '_')+'_'+backend
            directory = output/stem
            directory.mkdir(exist_ok=True)
            metadata_path = directory/'manifest.json'
            build_key = hashlib.sha256((fingerprint.hexdigest()+version+architecture+backend).encode()).hexdigest()
            if metadata_path.exists():
                old = json.loads(metadata_path.read_text())
                if old.get('buildKey') == build_key and (old['status'] == 'unsupported-build' or
                        ((directory/'app').exists() and sha(directory/'app') == old['elf']['sha256'])):
                    print(version, backend, 'verified build cache', flush=True)
                    continue
            metadata = dict(schemaVersion=1, runtime=version, architecture=architecture, backend=backend,
                            buildKey=build_key, toolchain=sdk_entry, sourceSha256=fingerprint.hexdigest())
            pin = lock['instrumentation'].get(backend)
            if pin and row['minor'] < pin['minimumGoMinor']:
                metadata.update(status='unsupported-build', reason=backend+' '+pin['version']+
                                ' requires Go 1.'+str(pin['minimumGoMinor'])+' or newer', instrumentation=pin)
                (directory/'app').write_text('#!/bin/sh\nexit 1\n')
                metadata_path.write_text(json.dumps(metadata, indent=2)+'\n')
                print(version, backend, metadata['reason'], flush=True)
                continue
            work = directory/'build'
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            for path in source_paths:
                shutil.copy2(path, work/path.name)
            env = dict(os.environ, GOROOT=str(sdk_root/'go'), GOOS='linux', GOARCH=architecture,
                       CGO_ENABLED='1', GOTOOLCHAIN='local', GOWORK='off', GOFLAGS='',
                       GOPATH=str(cache/'gopath'), GOCACHE=str(cache/(version+'-'+backend+'-build-cache')),
                       CGO_CFLAGS='-O2 -g -fcommon', CGO_LDFLAGS='-no-pie')
            env['PATH'] = str(go.parent)+os.pathsep+env['PATH']
            command = [str(go), 'build', '-ldflags=-linkmode=external -extldflags=-no-pie', '-o', str(directory/'app')]
            if backend == 'plain':
                env['GO111MODULE'] = 'off'
            elif backend == 'orchestrion':
                env['GO111MODULE'] = 'on'
                for name in ('go.mod', 'go.sum', 'orchestrion.tool.go'):
                    shutil.copy2(ROOT/'harness/go_runtime/orchestrion'/name, work/name)
                command = [str(go), 'run', 'github.com/DataDog/orchestrion', 'go', *command[1:]]
                metadata['instrumentation'] = pin
            elif backend == 'alibaba':
                env['GO111MODULE'] = 'on'
                tool_entry = pin['tool'].get(architecture)
                if not tool_entry:
                    raise RuntimeError('Pin an Alibaba tool checksum for ' + architecture)
                tool = download(tool_entry, cache)
                tool.chmod(0o755)
                (work/'go.mod').write_text('module example.com/go-runtime-capability\n\ngo 1.25.0\n')
                command = [str(tool), *command[1:]]
                command.insert(1, 'go')
                metadata['instrumentation'] = pin
            else:
                raise ValueError('Unknown backend: '+backend)
            log = directory/'build.log'
            log.unlink(missing_ok=True)
            print(version, backend, 'building', flush=True)
            try:
                run(command, work, env, log)
            except subprocess.CalledProcessError:
                print(log.read_text(errors='replace')[-8000:], flush=True)
                raise
            metadata.update(status='built', elf=elf_metadata(directory/'app'))
            if row['minor'] >= 13:
                metadata['buildInfo'] = subprocess.check_output([str(go), 'version', '-m', str(directory/'app')], env=env, text=True)
                if backend == 'orchestrion':
                    # Orchestrion injects dependencies after Go computes build
                    # info, so injected modules are absent from `version -m`.
                    metadata['sdkModuleVersion'] = subprocess.check_output(
                        [str(go), 'list', '-m', '-f', '{{.Version}}', 'github.com/DataDog/dd-trace-go/v2'], cwd=work, env=env, text=True).strip()
                    assert metadata['sdkModuleVersion'] == 'v'+pin['sdkVersion'], 'Unexpected Datadog SDK version'
            if (work/'go.mod').exists():
                metadata['moduleFiles'] = {name: (work/name).read_text() for name in ('go.mod', 'go.sum') if (work/name).exists()}
            assert metadata['elf']['machine'] == {'amd64': 62, 'arm64': 183}[architecture]
            metadata_path.write_text(json.dumps(metadata, indent=2)+'\n')
    # Prepared files are inputs to service tests; compilation never downloads
    # dependencies inside the test action. Work/cache trees stay out of runfiles.
    lines = ['package(default_visibility = ["//visibility:public"])']
    for path in sorted(output.glob('go1_*_*/manifest.json')):
        stem = path.parent.name
        names = [stem+'/manifest.json']
        if (path.parent/'app').exists():
            names.append(stem+'/app')
        lines.append('filegroup(name='+repr(stem)+', srcs='+repr(names)+')')
        lines.append('exports_files('+repr(names)+')')
    (output/'BUILD.bazel').write_text('\n'.join(lines)+'\n')
    (output/'MODULE.bazel').write_text('module(name="go_runtime_apps")\n')
    flags = ['--override_repository=go_runtime_apps='+str(output)]
    # Local repository symlink targets inside /tmp need read-only visibility
    # in Linux's private /tmp. Copy actions still declare app/manifest inputs.
    for directory in sorted(output.glob('go1_*_*')):
        if directory.is_dir() and directory.resolve().is_relative_to(Path('/tmp')):
            flags.append('--sandbox_add_mount_pair='+str(directory.resolve()))
    (output/'bazel.flags').write_text('\n'.join(flags)+'\n')
    print('\n'.join(flags), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cache')
    parser.add_argument('--arch', choices=('amd64', 'arm64'), default='amd64')
    parser.add_argument('--version', action='append')
    parser.add_argument('--backend', action='append', choices=('plain', 'orchestrion', 'alibaba'))
    args = parser.parse_args()
    args.backend = args.backend or ['plain', 'orchestrion', 'alibaba']
    build(args)


if __name__ == '__main__':
    main()
