#!/usr/bin/env python3
"""Fan out fresh RBE suites, then gate their complete, exact-head evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request

from ci_profile import PROFILES, select_targets


ENDPOINT = "https://pawel.buildbuddy.io"
SUITES = {
    "scenarios": "//fixtures:datadog_suite",
    "parallel": "//fixtures:datadog_parallel_suite",
    "features": "//fixtures:datadog_external_features_suite",
    "shared-sdk": "//fixtures:datadog_shared_sdk_suite",
    "capabilities": "//fixtures:datadog_capability_suite",
}
# Main retains every variant. PR stages cover the declared representative set.
STAGES = {"Datadog scenarios": ("scenarios", "0/1")}
STAGES.update({f"Datadog features {i + 1}/8": ("features", f"{i}/8") for i in range(8)})
STAGES.update({f"Datadog shared SDK {i + 1}/2": ("shared-sdk", f"{i}/2") for i in range(2)})
STAGES["Datadog capabilities"] = ("capabilities", "0/1")
PR_STAGES = {f"Datadog PR {suite}": (suite, "0/1")
             for suite in ("scenarios", "features", "shared-sdk", "capabilities")}


def stages(profile):
    return {"full": STAGES, "pr": PR_STAGES}[profile]


def parent_profile(metadata):
    match = re.search(r"--build_metadata=PULL_REQUEST_NUMBER=(\d+)", metadata)
    number = int(match[1]) if match else 0
    return ("pr" if number else "full"), number


def profile_targets(suite, profile):
    labels = targets(SUITES[suite])
    registry = json.loads(Path("harness/shared_sdk/cases.json").read_text()) if suite == "shared-sdk" else None
    return select_targets(labels, suite, profile, registry)


def targets(suite):
    result = subprocess.run(["bazel", "query", f"tests({suite})", "--output=label"],
                            check=True, text=True, stdout=subprocess.PIPE)
    labels = sorted(set(result.stdout.splitlines()))
    if not labels or any(not re.fullmatch(r"//fixtures:[A-Za-z0-9_.+-]+", t) for t in labels):
        raise ValueError(f"Invalid or empty test suite: {suite}")
    return labels


def partition(labels, shard):
    index, count = map(int, shard.split("/"))
    if count <= 0 or not 0 <= index < count or len(labels) != len(set(labels)):
        raise ValueError("Invalid shard or duplicate targets")
    selected = sorted(labels)[index::count]
    if not selected:
        raise ValueError("Empty shard")
    return selected


def validate_manifest(manifest, name, revision, expected, profile="full"):
    stage, shard = stages(profile)[name]
    if manifest != {"schemaVersion": 2, "stage": name, "revision": revision,
                    "suite": stage, "shard": shard, "targets": expected, "ciProfile": profile}:
        raise ValueError(f"Stale, incomplete or incorrect stage manifest: {name}")


def workflow(invocation):
    return next((e["buildEvent"]["workflowConfigured"] for e in invocation.get("event", [])
                 if "workflowConfigured" in e.get("buildEvent", {})), {})


def validate_invocation(invocation, name, revision, repo):
    configured = workflow(invocation)
    if (invocation.get("invocationStatus") != "COMPLETE_INVOCATION_STATUS"
            or invocation.get("success") is not True
            or invocation.get("commitSha") != revision
            or configured.get("commitSha") != revision
            or configured.get("actionName") != name
            or configured.get("pushedRepoUrl", "").removesuffix(".git") != repo.removesuffix(".git")):
        raise ValueError(f"Failed or mismatched exact-head invocation: {name}")


def artifact(invocation):
    matches = [f for group in invocation.get("targetGroups", [])
               for target in group.get("targets", []) for f in target.get("files", [])
               if f.get("name") == "datadog-evidence.tar.gz"]
    unique = {f["uri"]: f for f in matches}
    if len(unique) != 1:
        raise ValueError("Expected exactly one retained stage evidence archive")
    return next(iter(unique.values()))


def verify_archive(path, uri):
    match = re.fullmatch(r"bytestream://[^/]+/.*/blobs/([a-f0-9]{64})/(\d+)", uri)
    if not match:
        raise ValueError("Invalid SHA-256 evidence artifact URI")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != match[1] or size != int(match[2]):
        raise ValueError("Evidence archive size or checksum mismatch")


def extract_archive(path, destination):
    with tarfile.open(path, "r:gz") as archive:
        # Python 3.12's data filter rejects escapes, devices and unsafe links.
        archive.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("Evidence archive must have one root directory")
    return roots[0]


def download_stage(client, invocation, name, invocation_id, revision, expected, directory, profile="full"):
    file = artifact(invocation)
    url = ENDPOINT + "/file/download?" + urllib.parse.urlencode({
        "bytestream_url": file["uri"], "invocation_id": invocation_id})
    directory.mkdir()
    archive = directory / "evidence.tar.gz"
    with client.request(url) as source, archive.open("wb") as output:
        shutil.copyfileobj(source, output)
    verify_archive(archive, file["uri"])
    root = extract_archive(archive, directory / "extracted")
    manifest = json.loads((root / "ci-stage.json").read_text())
    validate_manifest(manifest, name, revision, expected, profile)
    return name, root, manifest


class BuildBuddy:
    def __init__(self):
        self.key = os.environ["BUILDBUDDY_API_KEY"]

    def request(self, url, data=None):
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(ENDPOINT).netloc:
            raise ValueError("Refusing to send BuildBuddy credentials to another host")
        headers = {"x-buildbuddy-api-key": self.key}
        if data is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=60)

    def rpc(self, method, data):
        with self.request(f"{ENDPOINT}/rpc/BuildBuddyService/{method}", data) as response:
            return json.load(response)

    def invocation(self, invocation_id, *, allow_queued=False):
        try:
            return self.rpc("GetInvocation", {"lookup": {"invocationId": invocation_id}})["invocation"][0]
        except urllib.error.HTTPError as error:
            # ExecuteWorkflow returns IDs before queued runners publish their BEP.
            # BuildBuddy's JSON RPC maps this specific gRPC NotFound to HTTP 500.
            if (allow_queued and error.code == 500
                    and error.read().decode().strip()
                    == "rpc error: code = NotFound desc = invocation not found"):
                return {}
            raise


def stage(args):
    profile = "pr" if args.name in PR_STAGES else "full"
    suite, shard = stages(profile)[args.name]
    selected = partition(profile_targets(suite, profile), shard)
    env = dict(os.environ, DATADOG_PARITY_STAGE=suite, DATADOG_PARITY_SHARD=shard,
               DATADOG_BAZEL_CONFIG="buildbuddy", DATADOG_CI_PROFILE=profile)
    # Test concurrency is bounded separately from compilation. RBE still
    # reserves each test's EstimatedCPU before scheduling it on an executor.
    env["DATADOG_PARITY_JOBS"] = "8" if suite == "scenarios" else "4"
    env["DATADOG_PARITY_BUILD_JOBS"] = "32"
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "ci-stage.json").write_text(json.dumps({
        "schemaVersion": 2, "stage": args.name, "revision": args.revision,
        "suite": suite, "shard": shard, "targets": selected, "ciProfile": profile}, indent=2) + "\n")
    started = time.monotonic()
    try:
        if suite == "scenarios":
            subprocess.run(["tools/run_datadog_preflight.sh"], env=env, check=True)
            subprocess.run(["bazel", "build", "--config=buildbuddy", "--spawn_strategy=remote,local",
                            "//:telemetry_api_check"], cwd="examples/plugin_agent", env=env, check=True)
        subprocess.run(["tools/run_datadog_parity.sh", args.images, args.revision, args.evidence],
                       env=env, check=True)
    finally:
        timings = evidence / "ci-timings"
        timings.mkdir(exist_ok=True)
        (timings / (args.name.replace("/", "-").replace(" ", "-") + ".json")).write_text(
            json.dumps({"stage": args.name, "revision": args.revision,
                        "elapsedSeconds": time.monotonic() - started,
                        "testJobs": int(env["DATADOG_PARITY_JOBS"]),
                        "buildJobs": int(env["DATADOG_PARITY_BUILD_JOBS"])}, indent=2) + "\n")


def aggregate(args):
    client = BuildBuddy()
    rc = Path(os.environ["BUILDBUDDY_CI_RUNNER_ROOT_DIR"]) / "buildbuddy.bazelrc"
    match = re.search(r"--build_metadata=PARENT_INVOCATION_ID=(\S+)", rc.read_text())
    if not match:
        raise ValueError("Missing parent invocation metadata")
    parent = workflow(client.invocation(match[1]))
    if parent.get("commitSha") != args.revision or parent.get("actionName") != "Full test suite":
        raise ValueError("Incorrect parent workflow revision or action")
    request = {k: parent[k] for k in ("workflowId", "pushedRepoUrl", "pushedBranch",
                                      "targetRepoUrl", "targetBranch")}
    profile, pr = parent_profile(rc.read_text())
    selected_stages = stages(profile)
    request.update(commitSha=args.revision, actionNames=list(selected_stages), visibility="PUBLIC", async_=True)
    request["async"] = request.pop("async_")
    if pr:
        request["pullRequestNumber"] = pr
    response = client.rpc("ExecuteWorkflow", request)
    statuses = response.get("actionStatuses", [])
    children = {s["actionName"]: s["invocationId"] for s in statuses
                if not s.get("status", {}).get("code", 0) and s.get("invocationId")}
    if set(children) != set(selected_stages) or len(statuses) != len(selected_stages):
        raise ValueError("BuildBuddy did not start every required test stage")
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "ci-invocations.json").write_text(json.dumps(children, indent=2) + "\n")
    print(json.dumps(children, indent=2), flush=True)
    # Leave time for merging and strict reports before the runner's one-hour limit.
    deadline = time.monotonic() + 50 * 60
    remaining = dict(children)
    completed = {}
    while remaining:
        pending = list(remaining.items())
        with ThreadPoolExecutor(max_workers=4) as pool:
            invocations = pool.map(lambda item: client.invocation(item[1], allow_queued=True), pending)
            polled = list(zip(pending, invocations))
        for (name, invocation_id), invocation in polled:
            if invocation.get("invocationStatus") == "COMPLETE_INVOCATION_STATUS":
                completed[name] = invocation
                del remaining[name]
                print(f"{name}: {'passed' if invocation.get('success') else 'FAILED'}", flush=True)
        if remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"RBE stages did not finish: {list(remaining)}")
            print(f"Waiting for {len(remaining)} RBE stages", flush=True)
            time.sleep(30)
    inventories = {suite: profile_targets(suite, profile)
                   for suite in dict.fromkeys(suite for suite, _ in selected_stages.values())}
    for name, invocation in completed.items():
        validate_invocation(invocation, name, args.revision, parent["pushedRepoUrl"])
    # Bound downloads to four streams. Merge in registry order on one thread
    # so overlap detection and complete-evidence gating remain deterministic.
    with tempfile.TemporaryDirectory(prefix="datadog-stages-") as temporary, ThreadPoolExecutor(max_workers=4) as pool:
        def download(name):
            suite, shard = selected_stages[name]
            expected = partition(inventories[suite], shard)
            directory = Path(temporary) / name.replace("/", "-").replace(" ", "-")
            return download_stage(client, completed[name], name, children[name], args.revision, expected, directory, profile)

        for name, root, manifest in pool.map(download, selected_stages):
            stage_manifests = evidence / "stages"
            stage_manifests.mkdir(exist_ok=True)
            (stage_manifests / (name.replace("/", "-").replace(" ", "-") + ".json")).write_text(
                json.dumps(manifest, indent=2) + "\n")
            for path in root.iterdir():
                if path.name == "ci-stage.json":
                    continue
                target = evidence / path.name
                if path.is_dir():
                    # Shards have disjoint target labels; fail if evidence overlaps.
                    for source in path.rglob("*"):
                        if source.is_file():
                            destination = target / source.relative_to(path)
                            if destination.exists():
                                raise ValueError(f"Overlapping stage evidence: {destination}")
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)
                else:
                    if target.exists():
                        raise ValueError(f"Overlapping stage report: {target}")
                    shutil.copy2(path, target)
    env = dict(os.environ, DATADOG_PARITY_STAGE="gate", DATADOG_BAZEL_CONFIG="buildbuddy", DATADOG_CI_PROFILE=profile)
    subprocess.run(["tools/run_datadog_parity.sh", args.images, args.revision, args.evidence],
                   env=env, check=True)
    shutil.copy2(evidence / "datadog-report.html",
                 Path(os.environ["BUILDBUDDY_ARTIFACTS_DIRECTORY"]) / "datadog-report.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("targets")
    select.add_argument("--suite", required=True, choices=SUITES.values())
    select.add_argument("--shard", default="0/1")
    select.add_argument("--ci-profile", choices=PROFILES, default=os.environ.get("DATADOG_CI_PROFILE", "full"))
    for command in ("stage", "aggregate"):
        action = sub.add_parser(command)
        action.add_argument("--revision", required=True)
        action.add_argument("--images", required=True)
        action.add_argument("--evidence", required=True)
        if command == "stage":
            action.add_argument("--name", required=True, choices=list(STAGES) + list(PR_STAGES))
    args = parser.parse_args()
    if args.command == "targets":
        suite = next(name for name, label in SUITES.items() if label == args.suite)
        print("\n".join(partition(profile_targets(suite, args.ci_profile), args.shard)))
    elif args.command == "stage":
        stage(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
