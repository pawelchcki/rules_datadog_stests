#!/usr/bin/env python3
"""Fan out fresh RBE suites, then gate their complete, exact-head evidence."""
import argparse
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
import urllib.request


ENDPOINT = "https://pawel.buildbuddy.io"
SUITES = {
    "features": "//fixtures:datadog_external_features_suite",
    "shared-sdk": "//fixtures:datadog_shared_sdk_suite",
    "capabilities": "//fixtures:datadog_capability_suite",
}
# Every shard remains a fresh execution. No target or evidence gate is omitted.
STAGES = {"Datadog scenarios": ("scenarios", "0/1")}
STAGES.update({f"Datadog features {i + 1}/8": ("features", f"{i}/8") for i in range(8)})
STAGES.update({f"Datadog shared SDK {i + 1}/2": ("shared-sdk", f"{i}/2") for i in range(2)})
STAGES["Datadog capabilities"] = ("capabilities", "0/1")


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


def validate_manifest(manifest, name, revision, expected):
    stage, shard = STAGES[name]
    if manifest != {"schemaVersion": 1, "stage": name, "revision": revision,
                    "suite": stage, "shard": shard, "targets": expected}:
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

    def invocation(self, invocation_id):
        return self.rpc("GetInvocation", {"lookup": {"invocationId": invocation_id}})["invocation"][0]


def stage(args):
    suite, shard = STAGES[args.name]
    selected = partition(targets(SUITES[suite]), shard) if suite in SUITES else []
    env = dict(os.environ, DATADOG_PARITY_STAGE=suite, DATADOG_PARITY_SHARD=shard,
               DATADOG_BAZEL_CONFIG="buildbuddy")
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "ci-stage.json").write_text(json.dumps({
        "schemaVersion": 1, "stage": args.name, "revision": args.revision,
        "suite": suite, "shard": shard, "targets": selected}, indent=2) + "\n")
    if suite == "scenarios":
        subprocess.run(["tools/run_datadog_preflight.sh"], env=env, check=True)
        subprocess.run(["bazel", "build", "--config=buildbuddy", "--spawn_strategy=remote,local",
                        "//:telemetry_api_check"], cwd="examples/plugin_agent", env=env, check=True)
    subprocess.run(["tools/run_datadog_parity.sh", args.images, args.revision, args.evidence],
                   env=env, check=True)


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
    request.update(commitSha=args.revision, actionNames=list(STAGES), visibility="PUBLIC", async_=True)
    request["async"] = request.pop("async_")
    pr = re.search(r"--build_metadata=PULL_REQUEST_NUMBER=(\d+)", rc.read_text())
    if pr:
        request["pullRequestNumber"] = int(pr[1])
    response = client.rpc("ExecuteWorkflow", request)
    statuses = response.get("actionStatuses", [])
    children = {s["actionName"]: s["invocationId"] for s in statuses
                if not s.get("status", {}).get("code", 0) and s.get("invocationId")}
    if set(children) != set(STAGES) or len(statuses) != len(STAGES):
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
        for name, invocation_id in list(remaining.items()):
            invocation = client.invocation(invocation_id)
            if invocation.get("invocationStatus") == "COMPLETE_INVOCATION_STATUS":
                completed[name] = invocation
                del remaining[name]
                print(f"{name}: {'passed' if invocation.get('success') else 'FAILED'}", flush=True)
        if remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"RBE stages did not finish: {list(remaining)}")
            print(f"Waiting for {len(remaining)} RBE stages", flush=True)
            time.sleep(30)
    inventories = {suite: targets(label) for suite, label in SUITES.items()}
    for name, invocation in completed.items():
        validate_invocation(invocation, name, args.revision, parent["pushedRepoUrl"])
        file = artifact(invocation)
        url = ENDPOINT + "/file/download?" + urllib.parse.urlencode({
            "bytestream_url": file["uri"], "invocation_id": children[name]})
        with tempfile.TemporaryDirectory(prefix="datadog-stage-") as temporary:
            directory = Path(temporary)
            archive = directory / "evidence.tar.gz"
            with client.request(url) as source, archive.open("wb") as output:
                shutil.copyfileobj(source, output)
            verify_archive(archive, file["uri"])
            root = extract_archive(archive, directory / "extracted")
            suite, shard = STAGES[name]
            expected = partition(inventories[suite], shard) if suite in SUITES else []
            manifest = json.loads((root / "ci-stage.json").read_text())
            validate_manifest(manifest, name, args.revision, expected)
            stages = evidence / "stages"
            stages.mkdir(exist_ok=True)
            (stages / (name.replace("/", "-").replace(" ", "-") + ".json")).write_text(
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
    env = dict(os.environ, DATADOG_PARITY_STAGE="gate", DATADOG_BAZEL_CONFIG="buildbuddy")
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
    for command in ("stage", "aggregate"):
        action = sub.add_parser(command)
        action.add_argument("--revision", required=True)
        action.add_argument("--images", required=True)
        action.add_argument("--evidence", required=True)
        if command == "stage":
            action.add_argument("--name", required=True, choices=STAGES)
    args = parser.parse_args()
    if args.command == "targets":
        print("\n".join(partition(targets(args.suite), args.shard)))
    elif args.command == "stage":
        stage(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
