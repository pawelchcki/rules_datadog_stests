#!/usr/bin/env python3
"""Gate and summarize retained Go runtime capability receipts and native captures."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.go_runtime import probe


def validate(path, applications):
    receipt = json.loads(path.read_text())
    directory = path.parent
    backend = 'plain' if receipt['backend'] == 'custom' else receipt['backend']
    stem = receipt['runtime'].replace('.', '_')+'_'+backend
    manifest = json.loads((applications/stem/'manifest.json').read_text())
    assert receipt['applicationManifest'] == manifest, 'Application manifest changed: '+stem
    assert receipt['architecture'] == manifest['architecture']
    assert receipt['status'] != 'failed', receipt.get('detail')
    if receipt['status'] in ('unsupported-build', 'unsupported-toolchain'):
        assert manifest['status'] == receipt['status']
        lock = json.loads(probe.LOCK.read_text())
        row = next(row for row in lock['runtimes'] if row['version'] == receipt['runtime'])
        if receipt['status'] == 'unsupported-toolchain':
            assert receipt['architecture'] not in row['toolchains']
        else:
            pin = lock['instrumentation'][backend]
            assert manifest['instrumentation'] == pin and row['minor'] < pin['minimumGoMinor']
        return receipt
    assert manifest['status'] == 'built' and probe.sha(applications/stem/'app') == manifest['elf']['sha256']
    control_stem = receipt['runtime'].replace('.', '_')+'_plain'
    assert receipt['controlManifest'] == json.loads((applications/control_stem/'manifest.json').read_text())
    assert probe.sha(applications/control_stem/'app') == receipt['controlManifest']['elf']['sha256']
    assert receipt['repetitions'] == 2
    files = {}
    for entry in receipt['artifacts']:
        relative = Path(entry['file'])
        assert not relative.is_absolute() and '..' not in relative.parts
        assert entry['file'] not in files, 'Duplicate artifact'
        artifact = directory/relative
        assert artifact.is_file() and probe.sha(artifact) == entry['sha256'], 'Missing or changed artifact: '+str(artifact)
        files[entry['file']] = artifact

    def read(name):
        return json.loads(files[name].read_text())

    for phase in ('primary', 'repeat'):
        control = read(phase+'.control.json')
        assert control['health']['runtime'] == receipt['runtime'] and control['health']['architecture'] == receipt['architecture']
        probe.assert_workload(control['response'], 200)
        assert not probe.spans(read(phase+'.control.capture.json'))
        if receipt['backend'] == 'plain':
            assert receipt['status'] == 'control-only'
            continue
        assert receipt['status'] == 'passed'
        health = read(phase+'.health.json')['health']
        assert health['runtime'] == receipt['runtime'] and health['architecture'] == receipt['architecture'] and health['cgo']
        if receipt['mode'] == 'attach':
            assert not probe.spans(read(phase+'.before-attach.capture.json'))
            assert read(phase+'.attach.json')['pid'] == health['pid']
        for status in (200, 503):
            response = read(phase+'.'+str(status)+'.response.json')
            probe.assert_workload(response, status)
            trace_id = hashlib.sha256((phase+str(status)).encode()).hexdigest()[:32]
            records = read(phase+'.'+str(status)+'.capture.json')
            probe.assert_trace(records, trace_id, '0000000000000123', response, status)
            probe.assert_instrumentor(records, receipt['backend'])
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipts', required=True, type=Path)
    parser.add_argument('--applications', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--arch', choices=('amd64', 'arm64'), default='amd64')
    parser.add_argument('--version', action='append')
    parser.add_argument('--backend', action='append', choices=('plain', 'orchestrion', 'alibaba', 'custom'))
    args = parser.parse_args()
    lock = json.loads(probe.LOCK.read_text())
    versions = args.version or [row['version'] for row in lock['runtimes']]
    assert set(versions) <= {row['version'] for row in lock['runtimes']}
    backends = args.backend or ['plain', 'orchestrion', 'alibaba']
    expected = {(version, backend) for version in versions for backend in backends}
    rows = {}
    for path in args.receipts.rglob('go-runtime-results.json'):
        receipt = json.loads(path.read_text())
        key = receipt['runtime'], receipt['backend']
        if key not in expected:
            continue
        assert key not in rows, 'Duplicate runtime receipt: '+str(key)
        assert receipt['architecture'] == args.arch
        rows[key] = validate(path, args.applications)
    assert rows.keys() == expected, 'Missing runtime receipts: '+str(expected-rows.keys())
    report = dict(schemaVersion=1, architecture=args.arch, statusCounts=dict(Counter(row['status'] for row in rows.values())),
                  rows=[rows[key] for key in sorted(rows)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['statusCounts'], sort_keys=True))


if __name__ == '__main__':
    main()
