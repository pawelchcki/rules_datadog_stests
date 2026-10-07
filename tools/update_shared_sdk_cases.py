#!/usr/bin/env python3
"""Regenerate the shared native SDK registry and version-resolved manifests.

Requires PyYAML only for regeneration; test execution reads checked-in JSON.
The source YAML and method bodies stay pinned to the inventory revision.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness/upstream_lab"))
sys.path.insert(0, str(ROOT / "harness/shared_sdk"))
from adapter import load_cases
from portable_cases import cases as portable_cases
from baggage_cases import adapt_case

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
VERSIONS = {"go": "2.10.1", "python": "4.15.5"}


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)([a-zA-Z.+-].*)?", value)
    if not match:
        raise ValueError("Unsupported manifest version: " + value)
    major, minor, patch, suffix = match.groups()
    return (int(major), int(minor), int(patch), 0 if suffix and not suffix.startswith("+") else 1)


def applies(condition, sdk_version):
    if " " in condition.strip():
        return all(applies(part, sdk_version) for part in condition.split())
    match = re.fullmatch(r"(>=|<=|>|<|==|=)?\s*(v?\d+\.\d+\.\d+(?:[a-zA-Z.+-].*)?)", condition)
    if not match:
        raise ValueError("Unsupported component condition: " + condition)
    operator, required = match.groups()
    actual, required = version(sdk_version), version(required)
    return {">=": actual >= required, "<=": actual <= required,
            ">": actual > required, "<": actual < required,
            "==": actual == required, "=": actual == required,
            None: actual >= required}[operator]


def declaration_reason(value, sdk_version):
    if re.match(r"^v?\d+\.\d+\.\d+", value):
        return None if applies(value.split()[0], sdk_version) else "missing_feature (requires " + value + ")"
    return value


def resolve_manifest(source, sdk_version):
    """Resolve SDK declarations; framework-dependent entries remain unevaluated."""
    declarations = []
    unresolved = []
    for selector, value in source["manifest"].items():
        if isinstance(value, str):
            declarations.append({"selector": selector, "reason": declaration_reason(value, sdk_version)})
            continue
        assert isinstance(value, list), (selector, value)
        for item in value:
            if any(key in item for key in ("weblog_declaration", "weblog", "excluded_weblog", "scenario")):
                unresolved.append({"selector": selector, "declaration": item})
                continue
            if "component_version" not in item or applies(item["component_version"], sdk_version):
                assert "declaration" in item, (selector, item)
                declarations.append({"selector": selector, "reason": declaration_reason(item["declaration"], sdk_version)})
    # Preserve supported declarations for the audit; upstream applies all
    # matching skip rules, including file and class declarations.
    return declarations, unresolved


def mapping_for_registry(registry):
    from collections import defaultdict
    groups = defaultdict(list)
    for index, entry in enumerate(registry):
        for feature in entry['features']:
            groups[feature].append((index, entry))
    scope = ("Common pinned upstream SDK methods plus named local supplements on native v0.4, Python 4.15.5 and Go 2.10.1. "
             "Each independent case repeats in fresh processes with a separate healthy baseline and retained captures. "
             "Manifest-backed xfails remain unsupported runtime evidence; no full upstream or Ruby parity is claimed. "
             "HTTP header supplement proves server request tags only; matching status ranges use explicit configuration. "
             "Raw SDK baggage span-tag controls are excluded from that capability; its positive scope uses actual HTTP instrumentation.")
    rows = []
    for feature, entries in sorted(groups.items()):
        rows.append(dict(name=feature, status='implemented', assertionScope=scope,
                         requiredCases=[dict(name=entry['name'], wire='v0.4') for _, entry in entries],
                         checks=[dict(path='harness/shared_sdk/probe.py', symbol='execute_method',
                                      target=f'//fixtures:datadog_shared_sdk_{language}_{index}_test')
                                 for language in ('go', 'python') for index, _ in entries]))
    return dict(schemaVersion=1, upstreamRevision=REVISION,
                coverageSemantics='All inventory features remain in the denominator. Ported assertion scopes are declarations, not runtime passes. Unsupported/xfail results never verify a capability.',
                capabilities=rows)


def generate():
    import yaml
    base = ROOT / "harness/shared_sdk"
    for language, sdk_version in VERSIONS.items():
        source = base / "golang.yml" if language == "go" else ROOT / "harness/upstream_lab/vendor/python.yml"
        raw = source.read_bytes()
        declarations, unresolved = resolve_manifest(yaml.safe_load(raw), sdk_version)
        result = dict(revision=REVISION, language=language, sdkVersion=sdk_version,
                      sourceFile=source.name, sourceSha256=hashlib.sha256(raw).hexdigest(),
                      declarations=declarations, frameworkDeclarations=unresolved)
        (base / (language + "-manifest.json")).write_text(json.dumps(result, indent=2) + "\n")
    exclusions = json.loads((ROOT / "harness/upstream_lab/exclusions.json").read_text())
    registry = []
    for case in load_cases(ROOT / "harness/upstream_lab/vendor"):
        env = case["parameters"].get("library_env", {})
        node = case["file"] + "::" + case["class"] + "::" + case["method"]
        excluded = exclusions.get(case["name"], exclusions.get(node))
        # Keep the same already implemented native lab scope. Do not re-label
        # upstream test-agent, filesystem, exporter or absent APIs as SDK gaps.
        if excluded or any(key.startswith("agent_") for key in case["parameters"]):
            continue
        if env.get("DD_TRACE_API_VERSION", "v0.4") != "v0.4":
            continue
        if env.get("DD_TRACE_STATS_COMPUTATION_ENABLED", "false") == "true":
            continue
        if any(feature in case["features"] for feature in ("trace_agent_connection", "trace_log_directory", "parametric_endpoint_parity", "f_otel_interoperability")):
            continue
        if "config" in case["method"] and case["file"] == "test_tracer.py":
            continue
        if "remove" in case["method"]:
            continue
        features = [f for f in case["features"] if f not in ("adaptive_sampling", "baggage_span_tags", "dd_service_mapping")]
        if "dd_service_mapping" in case["features"]:
            features.append("unified_service_tagging")
        if case["file"].startswith("test_otel_"):
            features.append("otel_api")
        if "Baggage" in case["class"] and case["file"] == "test_headers_baggage.py":
            features.append("datadog_baggage_headers")
        name = "shared-sdk-" + re.sub(r"[^a-z0-9]+", "-", case["name"].lower()).strip("-")
        case = adapt_case(case)
        entry = dict(name=name, method=case["name"], features=sorted(set(features)))
        if case.get("adaptedFrom"):
            entry["adaptedFrom"] = case["adaptedFrom"]
        registry.append(entry)
    for case in portable_cases():
        registry.append(dict(name="shared-sdk-" + case["method"].replace("test_", "").replace("_", "-"),
                             method=case["name"], features=case["features"]))
    assert len({entry['name'] for entry in registry}) == len(registry)
    (base / "cases.json").write_text(json.dumps(registry, indent=2) + "\n")
    (base / "cases.bzl").write_text('"""Generated by tools/update_shared_sdk_cases.py."""\nSHARED_SDK_CASES = ' +
                                    json.dumps([entry["name"] for entry in registry], indent=4) + "\n")
    (ROOT / "docs/datadog-shared-sdk-capabilities-mapping.json").write_text(json.dumps(mapping_for_registry(registry), indent=2) + "\n")
    print(len(registry), "cases covering", len({f for entry in registry for f in entry["features"]}), "capabilities")


if __name__ == "__main__":
    generate()
