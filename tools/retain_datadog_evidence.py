#!/usr/bin/env python3
"""Copy and gate each complete Datadog execution before Bazel replaces outputs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from ci_profile import PROFILES, ruby_runtimes

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--revision", required=True)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--gate", required=True, type=Path)
parser.add_argument("--ruby-matrix-only", action="store_true", help="retain only the versioned Ruby runtime suite")
parser.add_argument("--ci-profile", choices=PROFILES, default="full")
args = parser.parse_args()
if args.output.exists():
    parser.error("output already exists; evidence must never overwrite an earlier run")
args.output.mkdir(parents=True)


def copy_into_read_only_tree(source: Path, directory: Path, name: str) -> None:
    """Add a file after copytree has preserved Bazel's read-only output mode.

    Test outputs are intentionally mode 0555.  copytree preserves that mode,
    so adding test.log after the tree has been copied needs a temporary owner
    write bit.  Restore the exact original mode afterwards: retained evidence
    stays an immutable copy of the Bazel output tree plus its sibling log.
    """
    original_mode = directory.stat().st_mode
    try:
        os.chmod(directory, original_mode | 0o200)
        shutil.copyfile(source, directory / name)
    finally:
        os.chmod(directory, original_mode)


profiles = {
    "python-aiohttp-datadog-v4-15-5-v04": "aiohttp_datadog_v04",
    "python-aiohttp-datadog-v4-15-5-v05": "aiohttp_datadog",
    "python-django-datadog-v4-15-5-v04": "django_datadog_v04",
    "python-django-datadog-v4-15-5-v05": "django_datadog",
    "ruby-rails-datadog-v2-43-0-v04": "rails_datadog",
    "ruby-falcon-datadog-v2-43-0-v04": "falcon_datadog",
    "go-gin-datadog-v2-10-1-v04": "gin_datadog",
}
if args.ruby_matrix_only:
    profiles = {}

# Derive the added runtimes from the built compatibility manifest, so a new
# upstream runtime cannot be silently omitted from retained evidence.
compatibility = json.loads(Path("bazel-bin/harness/datadog_ruby_compatibility.json").read_text())
selected_runtimes = ruby_runtimes(compatibility, args.ci_profile)
for runtime in selected_runtimes:
    series = runtime["series"]
    profiles["ruby-sinatra-" + series.replace(".", "-") + "-datadog-v2-43-0-v04"] = "ruby_" + series.replace(".", "_") + "_datadog"
command = [str(args.gate.resolve()), "--revision", args.revision]
contract_profiles = ["ruby-sinatra-" + runtime["series"].replace(".", "-") + "-datadog-v2-43-0-v04" for runtime in selected_runtimes]
(args.output / "ci-profile.json").write_text(json.dumps({"ciProfile": args.ci_profile, "profiles": sorted(profiles)}) + "\n")
(args.output / "contract-profiles.json").write_text(json.dumps(contract_profiles) + "\n")
shutil.copyfile("bazel-bin/harness/datadog_ruby_compatibility.json", args.output / "ruby-compatibility.json")
for profile in contract_profiles:
    command += ["--contract-profile", profile]
for profile, prefix in profiles.items():
    source = Path("bazel-bin/corpus") / (profile + ".profile.json")
    manifest = json.loads(source.read_text())
    destination = args.output / source.name
    shutil.copyfile(source, destination)
    command += ["--manifest", str(destination.resolve())]
    artifacts = source.parent / (profile + ".validators")
    if artifacts.exists():
        shutil.copytree(artifacts, args.output / artifacts.name)
    for scenario in manifest["scenarios"]:
        outputs = Path("bazel-testlogs/fixtures") / (prefix + "_hurl_test_" + scenario) / "test.outputs"
        receipts = list((outputs / "datadog/receipts" / profile).glob("*.json"))
        receipts = [p for p in receipts if not p.name.endswith(".capture.json")]
        if len(receipts) != 1:
            raise RuntimeError(f"expected one receipt for {profile}/{scenario}, found {receipts}")
        directory = args.output / profile / scenario
        shutil.copytree(outputs, directory)
        receipt = directory / receipts[0].relative_to(outputs)
        capture = receipt.with_name(receipt.stem + ".capture.json")
        command += ["--receipt", str(receipt.resolve()), "--capture", str(capture.resolve())]
        testlog = outputs.parent / "test.log"
        copy_into_read_only_tree(testlog, directory, "test.log")
result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(args.output / "coverage-gate.log").write_text(result.stdout)
print(result.stdout, end="")
raise SystemExit(result.returncode)
