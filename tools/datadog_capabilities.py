"""Pinned system-tests capability inventory and evidence-based progress gate.

The inventory reads Python ASTs; it never imports or executes upstream code.
Feature names are keys because upstream numeric IDs are not unique.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

REVISION = "098fe0967c587db8a16b74a1e711777d0a9d5867"
REPOSITORY = "https://github.com/DataDog/system-tests"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return parent + "." + node.attr if parent else None
    return None


def feature_marks(node):
    marks = set()
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            decorator = decorator.func
        name = dotted(decorator)
        if name and name.startswith("features."):
            marks.add(name.split(".", 1)[1])
    return marks


def feature_definitions(source):
    tree = ast.parse(source)
    definitions = {}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name != "_Features":
            continue
        for node in cls.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            calls = [call for call in ast.walk(node) if isinstance(call, ast.Call)
                     and dotted(call.func) == "_mark_test_object"]
            for call in calls:
                kwargs = {kw.arg: kw.value for kw in call.keywords}
                if "feature_id" not in kwargs:
                    continue
                value = kwargs["feature_id"]
                if isinstance(value, ast.Name) and value.id == "NOT_REPORTED_ID":
                    feature_id = -1
                else:
                    feature_id = ast.literal_eval(value)
                definitions[node.name] = {
                    "name": node.name, "id": feature_id,
                    "description": (ast.get_docstring(node) or "").split("\n\n")[0],
                    "owner": dotted(kwargs.get("owner")), "sourceLine": node.lineno,
                }
    return definitions


def inventory(root, revision=REVISION):
    """Index static feature-marked methods, including inherited marks and methods."""
    root = Path(root)
    feature_source = (root / "utils/_features.py").read_bytes()
    definitions = feature_definitions(feature_source)
    modules = {}
    hashes = {"utils/_features.py": sha256(feature_source)}
    references = set()
    parametric_references = set()
    classes = {}
    for path in sorted((root / "tests").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_bytes()
        tree = ast.parse(source, filename=relative)
        module = relative[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        imports = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = ".".join(module.split(".")[:-node.level] + ([prefix] if prefix else []))
                for alias in node.names:
                    imports[alias.asname or alias.name] = prefix + "." + alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.asname or alias.name.split(".")[0]] = alias.name
        modules[module] = (relative, tree, imports)
        hashes[relative] = sha256(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                marks = feature_marks(node)
                references.update(marks)
                if relative.startswith("tests/parametric/"):
                    parametric_references.update(marks)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes[module + "." + node.name] = (module, node)
    unknown = references - definitions.keys()
    if unknown:
        raise ValueError("Unknown upstream feature decorators: " + ", ".join(sorted(unknown)))

    def bases(module, cls):
        imports = modules[module][2]
        result = []
        for base in cls.bases:
            name = dotted(base)
            if not name:
                continue
            first, _, tail = name.partition(".")
            qualified = imports.get(first, module + "." + first)
            if tail:
                qualified += "." + tail
            if qualified in classes:
                result.append(qualified)
        return result

    def class_marks(key, seen=None):
        seen = set() if seen is None else seen
        if key in seen:
            raise ValueError("Cyclic test class inheritance: " + key)
        module, cls = classes[key]
        result = feature_marks(cls)
        for base in bases(module, cls):
            result.update(class_marks(base, seen | {key}))
        return result

    def methods(key, seen=None):
        seen = set() if seen is None else seen
        if key in seen:
            raise ValueError("Cyclic test class inheritance: " + key)
        module, cls = classes[key]
        result = {}
        # Reverse base order preserves Python's preference for the first base.
        for base in reversed(bases(module, cls)):
            result.update(methods(base, seen | {key}))
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    result[node.name] = (module, node)
        return result

    tests = []

    def add_test(relative, identifier, source_module, method, marks):
        marks = sorted(marks - {"not_reported"})
        if not marks:
            return
        tests.append({
            "nodeId": relative + "::" + identifier,
            "sourceFile": modules[source_module][0], "sourceLine": method.lineno,
            "capabilityNames": marks,
        })

    for module, (relative, tree, _) in sorted(modules.items()):
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                add_test(relative, node.name, module, node, feature_marks(node))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                key = module + "." + node.name
                for name, (origin, method) in sorted(methods(key).items()):
                    add_test(relative, node.name + "::" + name, origin, method,
                             class_marks(key) | feature_marks(method))
    features = []
    for name in sorted(references - {"not_reported"}):
        row = dict(definitions[name])
        in_parametric = name in parametric_references or any(
            test["nodeId"].startswith("tests/parametric/") and name in test["capabilityNames"] for test in tests)
        row["scopes"] = ["all"] + (["parametric"] if in_parametric else [])
        row["testCases"] = [test["nodeId"] for test in tests if name in test["capabilityNames"]]
        features.append(row)
    return {
        "schemaVersion": 1, "repository": REPOSITORY, "revision": revision,
        "definitionSha256": sha256(feature_source), "sourceSha256": hashes,
        "countingUnit": "unique named feature decorators, excluding not_reported",
        "rawDecoratorCount": len(references), "features": features, "testCases": tests,
    }


def validate_mapping(data, mapping, local_root=None):
    if mapping.get("upstreamRevision") != data["revision"]:
        raise ValueError("Mapping and inventory revisions differ")
    features = {row["name"] for row in data["features"]}
    rows = mapping.get("capabilities", [])
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate capability mappings")
    for row in rows:
        if row["name"] not in features:
            raise ValueError("Unknown mapped capability: " + row["name"])
        if row.get("status") not in ("implemented", "partial", "missing", "unsupported"):
            raise ValueError("Invalid mapping status: " + row["name"])
        if row["status"] in ("implemented", "partial"):
            if not row.get("checks") or not row.get("requiredCases"):
                raise ValueError("Implemented/partial mapping requires checks and requiredCases: " + row["name"])
        for case in row.get("requiredCases", []):
            if isinstance(case, dict):
                if not isinstance(case.get("name"), str) or not case["name"] or case.get("wire") not in ("v0.4", "v0.5"):
                    raise ValueError("Scoped required case needs name and native wire version")
            elif not isinstance(case, str) or not case:
                raise ValueError("Required case must be a name or wire-scoped selector")
        for check in row.get("checks", []):
            if not check.get("path") or not check.get("symbol") or not check.get("target"):
                raise ValueError("Check requires path, symbol, target")
            path = Path(check["path"])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Check path must stay within local repository")
            if local_root:
                content = (Path(local_root) / path).read_text()
                if check["symbol"] not in content:
                    raise ValueError("Missing local assertion symbol: " + str(path) + "::" + check["symbol"])
    return {row["name"]: row for row in rows}


def evidence_results(paths, require_independent_cases=False):
    for path in paths:
        doc = json.loads(Path(path).read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
            raise ValueError("Evidence requires a results array: " + str(path))
        if require_independent_cases and len(doc["results"]) != 1:
            raise ValueError("Independent evidence requires exactly one case per receipt: " + str(path))
        for original in doc["results"]:
            result = dict(original)
            # Verify retained capture bytes, rather than trusting a hash string.
            capture_file = result.get("captureFile", str(result.get("name", "")) + ".capture.json")
            capture = Path(capture_file)
            if capture.is_absolute() or ".." in capture.parts:
                raise ValueError("Capture path must stay within the evidence directory")
            capture = Path(path).parent / capture
            result["_captureVerified"] = (capture.is_file() and
                sha256(capture.read_bytes()) == result.get("captureSha256"))
            result["_repeatVerified"] = False
            result["_baselineVerified"] = False
            repeat_file = result.get("repeatCaptureFile", str(Path(capture_file).with_name("repeat-" + Path(capture_file).name)))
            artifacts = result.get("artifacts", [])
            if not isinstance(artifacts, list):
                raise ValueError("Artifacts must be a list of relative files and hashes")
            for artifact in artifacts:
                if not isinstance(artifact, dict) or not isinstance(artifact.get("file"), str) or not isinstance(artifact.get("sha256"), str):
                    raise ValueError("Artifact requires file and sha256 strings")
                artifact_file = Path(artifact["file"])
                if artifact_file.is_absolute() or ".." in artifact_file.parts:
                    raise ValueError("Artifact path must stay within the evidence directory")
                artifact_path = Path(path).parent / artifact_file
                artifact_valid = (artifact_path.is_file() and
                    sha256(artifact_path.read_bytes()) == artifact["sha256"])
                result["_captureVerified"] = result["_captureVerified"] and artifact_valid
                if (artifact_valid and artifact["file"] == repeat_file
                        and artifact["file"] != capture_file
                        and artifact["sha256"] == result.get("repeatCaptureSha256")):
                    result["_repeatVerified"] = True
                if (artifact_valid and artifact["file"] not in (capture_file, repeat_file)
                        and artifact["file"].endswith(".capture.json")
                        and artifact["sha256"] == result.get("baselineSha256")):
                    result["_baselineVerified"] = True
            yield result


def coverage(data, mapping, scope="all", results=(), local_root=None, language=None):
    mappings = validate_mapping(data, mapping, local_root)
    features = {row["name"]: row for row in data["features"] if scope in row["scopes"]}
    inventory_names = {row["name"] for row in data["features"]}
    observed = {}
    for result in results:
        if language is not None and result.get("language") != language:
            continue
        names = result.get("capabilityNames", [])
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("capabilityNames must be a list of feature names")
        if len(names) != len(set(names)):
            raise ValueError("Duplicate capability names in receipt")
        if names and result.get("capabilityInventoryRevision") != data["revision"]:
            raise ValueError("Evidence upstream revision differs from inventory")
        for name in names:
            if name not in inventory_names:
                raise ValueError("Evidence claims unknown capability: " + name)
        capture_hash = result.get("captureSha256")
        valid = (result.get("status") == "passed" and isinstance(capture_hash, str)
                 and len(capture_hash) == 64 and all(c in "0123456789abcdef" for c in capture_hash)
                 and isinstance(result.get("configuration"), dict)
                 and result.get("capabilityInventoryRevision") == data["revision"]
                 and result.get("_captureVerified") is True)
        if language is not None:
            valid = (valid and result.get("repetitions") == 2
                     and result.get("_repeatVerified") is True
                     and result.get("_baselineVerified") is True)
        # Failed assertions usually leave capabilityNames empty. Index every
        # outcome so a passing duplicate cannot hide an earlier failure.
        observation = (valid, set(names))
        observed.setdefault(result.get("name"), []).append(observation)
        observed.setdefault((result.get("name"), result.get("wire")), []).append(observation)
    rows = []
    for name, feature in sorted(features.items()):
        row = mappings.get(name, {"status": "missing"})
        required = row.get("requiredCases", [])
        def outcomes(selector):
            key = (selector["name"], selector["wire"]) if isinstance(selector, dict) else selector
            matches = observed.get(key)
            return [valid and name in claims for valid, claims in matches] if matches else None
        verified = (row["status"] == "implemented" and bool(required)
                    and all(outcomes(case) and all(outcomes(case)) for case in required))
        rows.append({"name": name, "id": feature["id"], "status": row["status"],
                     "verified": verified, "upstreamTestCount": len(feature["testCases"]),
                     "missingEvidenceCases": [case for case in required if not outcomes(case)],
                     "failedEvidenceCases": [case for case in required if outcomes(case) and not all(outcomes(case))]})
    denominator = len(rows)
    implemented = sum(row["status"] == "implemented" for row in rows)
    verified = sum(row["verified"] for row in rows)
    return {"schemaVersion": 1, "upstreamRevision": data["revision"], "scope": scope,
            "denominator": denominator, "implementedCapabilities": implemented,
            "verifiedCapabilities": verified,
            "implementedPercent": 100 * implemented / denominator if denominator else 0,
            "verifiedPercent": 100 * verified / denominator if denominator else 0,
            "fullUpstreamCaseParity": False, "capabilities": rows}


def coverage_matrix(data, mapping, languages, scope="all", results=(), local_root=None):
    """Require identical mapped assertions independently in every language.

    Missing language metadata supplies no cross-language proof. The denominator
    always includes all inventory features in the requested scope, even when a
    language or its SDK does not implement them.
    """
    if not languages or len(languages) != len(set(languages)):
        raise ValueError("Expected distinct required languages")
    if any(language not in ("python", "ruby", "go") for language in languages):
        raise ValueError("Unknown required language")
    results = list(results)
    reports = {language: coverage(data, mapping, scope, results, local_root, language)
               for language in languages}
    report = dict(reports[languages[0]])
    report["requiredLanguages"] = list(languages)
    report["requiredRepetitions"] = 2
    report["languageCoverage"] = reports
    report["capabilities"] = []
    for index, original in enumerate(reports[languages[0]]["capabilities"]):
        row = dict(original)
        cells = {language: reports[language]["capabilities"][index] for language in languages}
        row["verified"] = all(cell["verified"] for cell in cells.values())
        row["languages"] = cells
        for field in ("missingEvidenceCases", "failedEvidenceCases"):
            row[field] = [{"language": language, "case": case}
                          for language, cell in cells.items() for case in cell[field]]
        report["capabilities"].append(row)
    report["verifiedCapabilities"] = sum(row["verified"] for row in report["capabilities"])
    report["verifiedPercent"] = (100 * report["verifiedCapabilities"] / report["denominator"]
                                 if report["denominator"] else 0)
    return report


def markdown(report):
    lines = ["# Datadog capability coverage", "",
             f"Pinned upstream: `{report['upstreamRevision']}`. Scope: `{report['scope']}`.", "",
             f"Implemented capability assertions: **{report['implementedCapabilities']}/{report['denominator']} "
             f"({report['implementedPercent']:.1f}%)**. Runtime verified with supplied receipts: "
             f"**{report['verifiedCapabilities']}/{report['denominator']} ({report['verifiedPercent']:.1f}%)**.", "",
             "A capability mapping means specific assertions exercise that feature. It does not establish "
             "full upstream test-case, language, framework, scenario, configuration, or statistical parity. "
             "Partial, unsupported, missing, failed, and absent evidence never pass the runtime gate.", "",
             "| Capability | Upstream ID | Local status | Runtime verified | Upstream test methods |",
             "| --- | ---: | --- | --- | ---: |"]
    for receipt in reversed(report.get("evidenceReceipts", [])):
        lines[6:6] = [f"Evidence receipt: `{receipt['file']}` (SHA-256 `{receipt['sha256']}`).", ""]
    for row in report["capabilities"]:
        lines.append(f"| `{row['name']}` | {row['id']} | {row['status']} | "
                     f"{'yes' if row['verified'] else 'no'} | {row['upstreamTestCount']} |")
    if "requiredLanguages" in report:
        lines += ["", "Identical required assertions must pass in every requested language. "
                  "Unlabelled receipts and unsupported SDKs supply no language coverage.", "",
                  "| Language | Verified capabilities | Percent |", "| --- | ---: | ---: |"]
        for language, cell in report["languageCoverage"].items():
            lines.append(f"| {language} | {cell['verifiedCapabilities']}/{cell['denominator']} | "
                         f"{cell['verifiedPercent']:.1f}% |")
        lines += ["", "| Capability | " + " | ".join(report["requiredLanguages"]) + " |",
                  "| --- | " + " | ".join("---" for _ in report["requiredLanguages"]) + " |"]
        for row in report["capabilities"]:
            missing = "missing"
            if row.get("gapIssues"):
                missing += " (" + ", ".join(f"[issue]({url})" for url in row["gapIssues"]) + ")"
            cells = ["passed" if row["languages"][language]["verified"] else
                     missing if row["status"] == "missing" else "unverified"
                     for language in report["requiredLanguages"]]
            lines.append(f"| `{row['name']}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def annotate_gap_issues(report, gaps):
    """Link tracked implementation gaps without changing verification counts."""
    if gaps.get("upstreamRevision") != report["upstreamRevision"]:
        raise ValueError("Gap tracker revision differs from inventory")
    scope = "shared" if report.get("requiredLanguages") else "broad"
    known = {row["name"] for row in report["capabilities"]}
    linked = {}
    for issue in gaps.get("issues", []):
        if scope not in issue.get("scope", []):
            continue
        url = issue.get("url", "")
        if not re.fullmatch(r"https://github.com/[^/]+/[^/]+/issues/[1-9][0-9]*", url):
            raise ValueError("Gap issue needs a GitHub issue URL")
        for name in issue["capabilities"]:
            if name not in known:
                # A scoped report can omit known capabilities outside its
                # denominator; full-inventory reports cover every issue name.
                if report["scope"] == "all":
                    raise ValueError("Gap tracker names an unknown capability: " + name)
                continue
            linked.setdefault(name, []).append(url)
    for row in report["capabilities"]:
        if row["name"] in linked:
            row["gapIssues"] = linked[row["name"]]
        if row["status"] in ("missing", "unsupported") and not row.get("gapIssues"):
            raise ValueError("Untracked capability gap: " + row["name"])
    report["caseGaps"] = gaps.get("caseGaps", [])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("inventory", help="Read a clean checkout of the pinned upstream")
    generate.add_argument("--upstream", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    report = sub.add_parser("report", help="Report or gate explicitly mapped capability assertions")
    report.add_argument("--inventory", required=True, type=Path)
    report.add_argument("--mapping", required=True, type=Path)
    report.add_argument("--scope", choices=["all", "parametric"], default="all")
    report.add_argument("--evidence", action="append", default=[], type=Path)
    report.add_argument("--local-root", type=Path)
    report.add_argument("--output", type=Path)
    report.add_argument("--format", choices=["json", "markdown"], default="json")
    report.add_argument("--markdown-output", type=Path, help="Also render Markdown from the same verified evidence")
    report.add_argument("--require-percent", type=float)
    report.add_argument("--require-language", action="append", choices=["python", "ruby", "go"], default=[],
                        help="Require the same mapped assertions separately in each language; repeat for a matrix")
    report.add_argument("--require-all-implemented", action="store_true",
                        help="Require every implemented mapping to have verified runtime evidence")
    report.add_argument("--require-independent-cases", action="store_true",
                        help="Require exactly one independent case in each supplied evidence receipt")
    report.add_argument("--gap-issues", type=Path,
                        help="Link issues and require tracking for every missing capability")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        actual = subprocess.check_output(["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip()
        if actual != REVISION:
            parser.error("Upstream checkout must be pinned to " + REVISION)
        dirty = subprocess.check_output(["git", "-C", str(args.upstream), "status", "--porcelain", "--untracked-files=no"], text=True)
        if dirty:
            parser.error("Upstream tracked files must be clean")
        args.output.write_text(json.dumps(inventory(args.upstream), indent=2) + "\n")
        return 0
    data = json.loads(args.inventory.read_text())
    mapping = json.loads(args.mapping.read_text())
    if args.require_language:
        result = coverage_matrix(data, mapping, args.require_language, args.scope,
                                 evidence_results(args.evidence, args.require_independent_cases), args.local_root)
    else:
        result = coverage(data, mapping, args.scope,
                          evidence_results(args.evidence, args.require_independent_cases), args.local_root)
    if args.require_independent_cases:
        result["requiredIndependentCases"] = True
    if args.gap_issues:
        annotate_gap_issues(result, json.loads(args.gap_issues.read_text()))
    result["evidenceReceipts"] = [{"file": path.name, "sha256": sha256(path.read_bytes())} for path in args.evidence]
    rendered = markdown(result) if args.format == "markdown" else json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    if args.markdown_output:
        args.markdown_output.write_text(markdown(result))
    if args.require_all_implemented and (not result["implementedCapabilities"] or
            result["verifiedCapabilities"] != result["implementedCapabilities"]):
        print("Capability gate failed: not every implemented assertion has verified evidence")
        return 1
    if args.require_percent is not None:
        if not 0 <= args.require_percent <= 100:
            parser.error("--require-percent must be between 0 and 100")
        if result["verifiedPercent"] < args.require_percent:
            print(f"Capability gate failed: {result['verifiedPercent']:.1f}% verified < {args.require_percent:.1f}% required")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
