#!/usr/bin/env python3
"""Report shared SDK evidence and manifest exclusions across the full inventory."""
import argparse
from collections import Counter
import json
from pathlib import Path

from datadog_capabilities import coverage_matrix, evidence_results
from ci_profile import PROFILES, sdk_cases

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("go", "python")


def load(path):
    return json.loads(Path(path).read_text())


def manifest_exclusions(feature, manifest):
    results = []
    for node in feature['testCases']:
        matches = [entry for entry in manifest['declarations'] if entry['reason'] and
                   (node == entry['selector'] or node.startswith(entry['selector'] + '::') or
                    node.startswith(entry['selector'].rstrip('/') + '/'))]
        if matches:
            results.append(dict(method=node, **max(matches, key=lambda entry: len(entry['selector']))))
    return results


def build_report(inventory, sdk_mapping, http_mapping, python_mapping, go_manifest, results, local_root):
    sdk = coverage_matrix(inventory, sdk_mapping, LANGUAGES, results=results, local_root=local_root)
    http = coverage_matrix(inventory, http_mapping, LANGUAGES, results=results, local_root=local_root)
    assert go_manifest['revision'] == inventory['revision'] and go_manifest['sdkVersion'] == '2.10.1'
    sdk_rows = {row['name']: row for row in sdk['capabilities']}
    http_rows = {row['name']: row for row in http['capabilities']}
    existing = {row['name']: row for row in python_mapping['capabilities']}
    registry = {row['name']: row for row in sdk_mapping['capabilities']}
    rows = []
    case_outcomes = {}
    for language in LANGUAGES:
        selected = [r for r in results if r.get('language') == language and r.get('application') == 'shared-sdk']
        case_outcomes[language] = dict(Counter(r.get('status') for r in selected))
    for feature in sorted(inventory['features'], key=lambda row: row['name']):
        name = feature['name']
        exclusions = manifest_exclusions(feature, go_manifest)
        common = sdk_rows[name] if sdk_rows[name]['status'] == 'implemented' else http_rows[name]
        cells = {}
        for language in LANGUAGES:
            cell = common['languages'][language]
            observed = [r for r in results if r.get('language') == language and name in r.get('capabilityNames', [])]
            xfails = [dict(case=r['name'], failures=r.get('expectedFailures', [])) for r in observed
                      if r.get('status') == 'unsupported' and r.get('expectedFailures')]
            if cell['verified']:
                status = 'passed'
            elif common['status'] == 'implemented':
                status = 'xfail' if xfails and not cell['missingEvidenceCases'] else 'unverified'
            elif language == 'python' and existing.get(name, {}).get('status') == 'implemented':
                status = 'existing-language-suite'
            elif language == 'go' and feature['testCases'] and len(exclusions) == len(feature['testCases']):
                status = 'manifest-excluded'
            else:
                status = 'adapter-missing'
            cells[language] = dict(status=status, verified=cell['verified'], expectedFailures=xfails,
                                   missingEvidenceCases=cell['missingEvidenceCases'], failedEvidenceCases=cell['failedEvidenceCases'])
        rows.append(dict(name=name, id=feature['id'], commonAssertionScope=registry.get(name, {}).get('assertionScope'),
                         existingPythonChecks=existing.get(name, {}).get('checks', []),
                         upstreamGoExclusions=exclusions, languages=cells))
    return dict(schemaVersion=1, upstreamRevision=inventory['revision'], sdkVersions={'go': '2.10.1', 'python': '4.15.5'},
                denominator=len(rows), fullUpstreamCaseParity=False, caseOutcomes=case_outcomes,
                languageOutcomes={lang: dict(Counter(row['languages'][lang]['status'] for row in rows)) for lang in LANGUAGES},
                sdkCoverage=sdk, httpCoverage=http, capabilities=rows)


def markdown(report):
    lines = ['# Shared SDK capability coverage', '',
             'Go SDK **2.10.1**, Python SDK **4.15.5**, native **v0.4**; upstream `' + report['upstreamRevision'] + '`.', '',
             'All **' + str(report['denominator']) + '** inventory features remain visible. A pass verifies the declared local scope, not every upstream method. '
             '`xfail` means executed assertions failed as expected; `manifest-excluded` is a declared upstream exclusion without a ported local workload. '
             '`existing-language-suite` identifies a Python implementation awaiting a shared adapter. None of these three statuses counts as a pass.', '',
             '| Language | Passed cases | Executed xfails | Failed cases |', '| --- | ---: | ---: | ---: |']
    if report.get('ciProfile') == 'pr':
        lines[2:2] = ['Representative PR selection: **' + str(report['selectedCaseCount']) +
                      '** cases per language. The exhaustive parameter matrix runs on main; missing cases remain unverified.', '']
    for language, counts in report['caseOutcomes'].items():
        lines.append(f"| {language} | {counts.get('passed', 0)} | {counts.get('unsupported', 0)} | {counts.get('failed', 0)} |")
    lines += ['', '| Capability | Go | Python | Go manifest reason |', '| --- | --- | --- | --- |']
    for row in report['capabilities']:
        reasons = sorted({entry['reason'] for entry in row['upstreamGoExclusions']})
        reason = '; '.join(reasons).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| `{row['name']}` | {row['languages']['go']['status']} | {row['languages']['python']['status']} | {reason} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--evidence', action='append', default=[])
    parser.add_argument('--evidence-dir', action='append', default=[])
    parser.add_argument('--output', required=True)
    parser.add_argument('--format', choices=('json', 'markdown'), default='json')
    parser.add_argument('--require-complete-matrix', action='store_true', help='Require each registered case in both languages; accept manifest-backed xfails')
    parser.add_argument('--ci-profile', choices=PROFILES, default='full')
    parser.add_argument('--require-selected-matrix', action='store_true', help='Require every representative case in both languages with unchanged evidence validation')
    args = parser.parse_args()
    root = Path(args.root)
    paths = [Path(p) for p in args.evidence]
    for directory in args.evidence_dir:
        paths.extend(Path(directory).glob('**/datadog-shared-sdk-results.json'))
        paths.extend(Path(directory).glob('**/datadog-shared-results.json'))
    results = list(evidence_results(sorted(set(paths))))
    registry = load(root/'harness/shared_sdk/cases.json')
    entries = {entry['name']: entry for entry in registry}
    if args.require_complete_matrix and args.ci_profile != 'full':
        parser.error('--require-complete-matrix requires the full CI profile')
    expected = {entry['name'] for entry in sdk_cases(registry, args.ci_profile)}
    problems = []
    for language in LANGUAGES:
        selected = [r for r in results if r.get('language') == language and r.get('application') == 'shared-sdk']
        names = {r['name'] for r in selected}
        if (args.require_complete_matrix or args.require_selected_matrix) and names != expected:
            problems.append(language + ': missing or unknown registered cases')
        for row in selected:
            valid = row['_captureVerified'] and row['_repeatVerified'] and row['_baselineVerified'] and row.get('repetitions') == 2
            valid = valid and row.get('status') in ('passed', 'unsupported')
            entry = entries.get(row['name'])
            valid = valid and entry is not None and row.get('sourceMethod') == entry['method']
            valid = valid and entry is not None and row.get('adaptedFrom') == entry.get('adaptedFrom')
            valid = valid and entry is not None and row.get('capabilityNames') == entry['features']
            valid = valid and row.get('sdkVersion') == {'go': '2.10.1', 'python': '4.15.5'}[language] and row.get('wire') == 'v0.4'
            if row.get('status') == 'unsupported':
                valid = valid and bool(row.get('expectedFailures'))
            if not valid:
                problems.append(language + ': invalid or failed evidence for ' + row['name'])
    report = build_report(load(root/'docs/datadog-capabilities-inventory.json'),
                          load(root/'docs/datadog-shared-sdk-capabilities-mapping.json'),
                          load(root/'docs/datadog-shared-capabilities-mapping.json'),
                          load(root/'docs/datadog-capabilities-mapping.json'),
                          load(root/'harness/shared_sdk/go-manifest.json'), results, root)
    report['ciProfile'] = args.ci_profile
    report['selectedCaseCount'] = len(expected)
    Path(args.output).write_text(markdown(report) if args.format == 'markdown' else json.dumps(report, indent=2) + '\n')
    if problems:
        raise SystemExit('\n'.join(problems))


if __name__ == '__main__':
    main()
