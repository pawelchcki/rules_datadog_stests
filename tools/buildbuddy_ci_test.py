"""Required-check invariants for sharded, retained RBE evidence."""
import hashlib
import io
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import buildbuddy_ci as ci
from archive_evidence import archive_evidence
from ci_profile import PR_GO_VERSIONS, PR_RUBY_SERIES, ruby_runtimes, sdk_cases, select_targets


class BuildBuddyCITest(unittest.TestCase):
    def test_failed_feature_stage_batches_complete_outputs_and_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'tools'
            scripts.mkdir()
            for name in ('run_datadog_parity.sh', 'buildbuddy_ci.py', 'ci_profile.py'):
                shutil.copyfile(Path(__file__).with_name(name), scripts / name)
            binary = root / 'bin'
            binary.mkdir()
            bazel = binary / 'bazel'
            bazel.write_text('#!/usr/bin/env bash\n'
                             'case "$1" in\n'
                             'query) printf "%s\\n" //fixtures:with_outputs //fixtures:log_only //fixtures:missing ;;\n'
                             'test) exit 14 ;;\n'
                             'esac\n')
            bazel.chmod(0o755)
            images = root / 'images'
            images.mkdir()
            (images / 'bazel.flags').write_text('')
            outputs = root / 'bazel-testlogs/fixtures/with_outputs/test.outputs'
            outputs.mkdir(parents=True)
            (outputs / 'capture with spaces.json').write_bytes(b'native capture\n')
            native = root / 'native'
            native.mkdir()
            (native / 'receipt.json').write_bytes(b'native receipt\n')
            (outputs / 'linked').symlink_to(native, target_is_directory=True)
            (outputs.parent / 'test.log').write_text('failed assertion\n')
            log_only = root / 'bazel-testlogs/fixtures/log_only'
            log_only.mkdir(parents=True)
            (log_only / 'test.log').write_text('startup failure\n')
            evidence = root / 'evidence'
            run = subprocess.run(['bash', str(scripts / 'run_datadog_parity.sh'),
                                  str(images), 'head', str(evidence)], cwd=root,
                                 env=dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                                          PYTHONPATH=str(scripts),
                                          DATADOG_PARITY_STAGE='features', DATADOG_CI_PROFILE='full'),
                                 capture_output=True, text=True)
            self.assertEqual(14, run.returncode, run.stderr)
            retained = evidence / 'features/bazel-testlogs/fixtures'
            self.assertEqual(b'native capture\n',
                             (retained / 'with_outputs/test.outputs/capture with spaces.json').read_bytes())
            self.assertEqual(b'native receipt\n',
                             (retained / 'with_outputs/test.outputs/linked/receipt.json').read_bytes())
            self.assertFalse((retained / 'with_outputs/test.outputs/linked').is_symlink())
            self.assertEqual('failed assertion\n', (retained / 'with_outputs/test.log').read_text())
            self.assertEqual('startup failure\n', (retained / 'log_only/test.log').read_text())

    def test_aggregate_dispatch_and_gate_use_same_profile_with_each_inventory_queried_once(self):
        for profile, pr in (("pr", 20), ("full", 0)):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "buildbuddy.bazelrc").write_text(
                    f"build --build_metadata=PARENT_INVOCATION_ID=parent --build_metadata=PULL_REQUEST_NUMBER={pr}")
                configured = dict(workflowId="workflow", pushedRepoUrl="repo.git", pushedBranch="branch",
                                  targetRepoUrl="repo.git", targetBranch="main", commitSha="head", actionName="Full test suite")
                expected_stages = ci.stages(profile)
                client = Mock()
                client.rpc.return_value = {"actionStatuses": [{"actionName": name, "invocationId": name} for name in expected_stages]}

                def invocation(name, **kwargs):
                    action = configured if name == "parent" else dict(configured, actionName=name)
                    return dict(success=True, commitSha="head", invocationStatus="COMPLETE_INVOCATION_STATUS",
                                event=[{"buildEvent": {"workflowConfigured": action}}])

                def download(client, invocation, name, invocation_id, revision, expected, directory, actual_profile):
                    directory.mkdir()
                    suite, shard = expected_stages[name]
                    manifest = dict(schemaVersion=2, stage=name, revision=revision, suite=suite,
                                    shard=shard, targets=expected, ciProfile=actual_profile)
                    ci.validate_manifest(manifest, name, revision, expected, profile)
                    return name, directory, manifest

                evidence = root / "evidence"
                def gate(command, *, env, check):
                    self.assertEqual(profile, env["DATADOG_CI_PROFILE"])
                    self.assertEqual("gate", env["DATADOG_PARITY_STAGE"])
                    (evidence / "datadog-report.html").write_text("verified report")

                client.invocation.side_effect = invocation
                with patch.dict(ci.os.environ, {"BUILDBUDDY_CI_RUNNER_ROOT_DIR": str(root),
                                               "BUILDBUDDY_ARTIFACTS_DIRECTORY": str(root)}), \
                        patch.object(ci, "BuildBuddy", return_value=client), \
                        patch.object(ci, "profile_targets", return_value=[f"//fixtures:case_{i}" for i in range(16)]) as inventory, \
                        patch.object(ci, "download_stage", side_effect=download), \
                        patch.object(ci.subprocess, "run", side_effect=gate):
                    ci.aggregate(SimpleNamespace(revision="head", images="images", evidence=str(evidence)))
                request = client.rpc.call_args.args[1]
                self.assertEqual(list(expected_stages), request["actionNames"])
                self.assertEqual(pr or None, request.get("pullRequestNumber"))
                self.assertEqual(4, inventory.call_count)
                self.assertEqual("verified report", (root / "datadog-report.html").read_text())

    def test_push_and_manual_metadata_default_to_full_pr_metadata_selects_representatives(self):
        for metadata in ("", "build --build_metadata=PULL_REQUEST_NUMBER=0"):
            self.assertEqual(("full", 0), ci.parent_profile(metadata))
        self.assertEqual(("pr", 20), ci.parent_profile("build --build_metadata=PULL_REQUEST_NUMBER=20"))

    def test_pr_sdk_representatives_cover_each_class_in_both_languages(self):
        root = Path(__file__).resolve().parents[1]
        registry = ci.json.loads((root / "harness/shared_sdk/cases.json").read_text())
        selected = sdk_cases(registry, "pr")
        classes = lambda entries: {entry["method"].rsplit(".", 1)[0] for entry in entries}
        self.assertEqual(classes(registry), classes(selected))
        self.assertLess(len(selected), len(registry) // 2)
        labels = [f"//fixtures:datadog_shared_sdk_{language}_{index}_test"
                  for language in ("go", "python") for index in range(len(registry))]
        representatives = select_targets(labels, "shared-sdk", "pr", registry)
        self.assertEqual(len(selected) * 2, len(representatives))
        for index, entry in enumerate(registry):
            for language in ("go", "python"):
                self.assertEqual(entry in selected, f"//fixtures:datadog_shared_sdk_{language}_{index}_test" in representatives)
        self.assertEqual(registry, sdk_cases(registry, "full"))
        with self.assertRaises(ValueError):
            select_targets([], "shared-sdk", "pr", registry)

    def test_pr_keeps_every_framework_and_intake_and_only_selected_ruby_variants(self):
        base = ["//fixtures:aiohttp_datadog_v04_hurl_test_tags", "//fixtures:aiohttp_datadog_hurl_test_tags",
                "//fixtures:django_datadog_hurl_test_tags", "//fixtures:rails_datadog_hurl_test_tags",
                "//fixtures:falcon_datadog_hurl_test_tags", "//fixtures:gin_datadog_hurl_test_tags"]
        ruby = [f"//fixtures:ruby_{series.replace('.', '_')}_datadog_hurl_test_tags"
                for series in (*PR_RUBY_SERIES, "2.6", "3.4")]
        self.assertEqual(base + ruby[:3], select_targets(base + ruby, "scenarios", "pr"))
        self.assertEqual(base, select_targets(base + ruby, "features", "pr"))
        for suite in ("scenarios", "parallel", "features", "capabilities"):
            self.assertEqual(base + ruby, select_targets(base + ruby, suite, "full"))
        compatible = {"supported": [{"series": series} for series in (*PR_RUBY_SERIES, "2.6")]}
        self.assertEqual(list(PR_RUBY_SERIES), [runtime["series"] for runtime in ruby_runtimes(compatible, "pr")])
        with self.assertRaises(ValueError):
            ruby_runtimes({"supported": []}, "pr")

    def test_pr_go_versions_cover_legacy_control_minimum_and_latest_instrumentation(self):
        root = Path(__file__).resolve().parents[1]
        lock = ci.json.loads((root / "harness/go_runtime/versions.lock.json").read_text())
        pinned = {row["version"]: row for row in lock["runtimes"]}
        self.assertLess(len(PR_GO_VERSIONS), len(pinned))
        self.assertTrue(set(PR_GO_VERSIONS) <= pinned.keys())
        self.assertIn(lock["runtimes"][0]["version"], PR_GO_VERSIONS)
        self.assertIn(lock["runtimes"][-1]["version"], PR_GO_VERSIONS)
        for pin in lock["instrumentation"].values():
            self.assertIn(pin["minimumGoMinor"], [pinned[version]["minor"] for version in PR_GO_VERSIONS])

    def test_representative_manifest_cannot_satisfy_full_matrix_gate(self):
        name = "Datadog PR features"
        manifest = {"schemaVersion": 2, "stage": name, "revision": "head",
                    "suite": "features", "shard": "0/1", "targets": ["//fixtures:a"], "ciProfile": "pr"}
        ci.validate_manifest(manifest, name, "head", manifest["targets"], "pr")
        with self.assertRaises(ValueError):
            ci.validate_manifest(dict(manifest, ciProfile="full"), name, "head", manifest["targets"], "pr")
        with self.assertRaises((KeyError, ValueError)):
            ci.validate_manifest(manifest, name, "head", manifest["targets"], "full")
        self.assertEqual(4, len(ci.stages("pr")))
        self.assertEqual(12, len(ci.stages("full")))

    def test_failed_stage_retains_manifest_and_timing_with_bounded_test_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(name="Datadog features 1/8", revision="head", images="images", evidence=directory)
            labels = [f"//fixtures:test_{i}" for i in range(16)]
            with patch.object(ci, "targets", return_value=labels), patch.object(ci.subprocess, "run") as run:
                run.side_effect = subprocess.CalledProcessError(1, "parity")
                with self.assertRaises(subprocess.CalledProcessError):
                    ci.stage(args)
            env = run.call_args.kwargs["env"]
            self.assertEqual("4", env["DATADOG_PARITY_JOBS"])
            self.assertEqual("32", env["DATADOG_PARITY_BUILD_JOBS"])
            manifest = ci.json.loads((Path(directory) / "ci-stage.json").read_text())
            ci.validate_manifest(manifest, args.name, "head", ci.partition(labels, "0/8"))
            timing = ci.json.loads(next((Path(directory) / "ci-timings").glob("*.json")).read_text())
            self.assertEqual("head", timing["revision"])
            self.assertGreaterEqual(timing["elapsedSeconds"], 0)

    def test_stage_respects_fleet_budget_overrides_and_records_actual_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(name="Datadog PR shared-sdk", revision="head", images="images", evidence=directory)
            with patch.dict(ci.os.environ, {"DATADOG_PARITY_JOBS": "6", "DATADOG_PARITY_BUILD_JOBS": "24"}), \
                    patch.object(ci, "profile_targets", return_value=["//fixtures:case"]), \
                    patch.object(ci.subprocess, "run") as run:
                ci.stage(args)
            self.assertEqual("6", run.call_args.kwargs["env"]["DATADOG_PARITY_JOBS"])
            self.assertEqual("24", run.call_args.kwargs["env"]["DATADOG_PARITY_BUILD_JOBS"])
            timing = ci.json.loads(next((Path(directory) / "ci-timings").glob("*.json")).read_text())
            self.assertEqual((6, 24), (timing["testJobs"], timing["buildJobs"]))

    def test_archive_preserves_bytes_and_ignores_temporary_paths_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archives = []
            for index in range(2):
                source = directory / f"evidence-{index}"
                source.mkdir()
                capture = source / "nested" / "capture.json"
                capture.parent.mkdir()
                capture.write_bytes(b'{"raw": "unaltered"}\n')
                os.utime(capture, (100 + index, 100 + index))
                capture.chmod(0o600 if index == 0 else 0o644)
                duplicate = source / "duplicate.json"
                duplicate.write_bytes(capture.read_bytes())
                archive = directory / f"{index}.tar.gz"
                archive_evidence(source, archive)
                archives.append(archive.read_bytes())
                root = ci.extract_archive(archive, directory / f"extracted-{index}")
                self.assertEqual(capture.read_bytes(), (root / "nested/capture.json").read_bytes())
                self.assertEqual(capture.read_bytes(), (root / "duplicate.json").read_bytes())
                with tarfile.open(archive) as retained:
                    self.assertTrue(retained.getmember("datadog-evidence/nested/capture.json").islnk())
            self.assertEqual(*archives)

    def test_only_queued_invocation_not_found_is_pending(self):
        with patch.dict(ci.os.environ, {"BUILDBUDDY_API_KEY": "test-key"}):
            client = ci.BuildBuddy()
        pending = b"rpc error: code = NotFound desc = invocation not found\n"
        for allow, code, body in ((True, 500, pending), (False, 500, pending),
                                  (True, 403, b"Permission denied"), (True, 500, b"Internal error")):
            error = urllib.error.HTTPError("https://pawel.buildbuddy.io", code, "error", {}, io.BytesIO(body))
            with patch.object(client, "rpc", side_effect=error):
                if allow and code == 500 and body == pending:
                    self.assertEqual({}, client.invocation("queued", allow_queued=allow))
                else:
                    with self.assertRaises(urllib.error.HTTPError):
                        client.invocation("queued", allow_queued=allow)

    def test_shards_are_disjoint_and_complete(self):
        labels = [f"//fixtures:test_{i}" for i in range(848)]
        shards = [ci.partition(labels, f"{i}/8") for i in range(8)]
        self.assertEqual(sorted(labels), sorted(t for shard in shards for t in shard))
        self.assertEqual([106] * 8, [len(shard) for shard in shards])
        for shard in ("8/8", "0/0", "-1/8", "848/849"):
            with self.assertRaises(ValueError):
                ci.partition(labels, shard)

    def test_manifest_rejects_missing_extra_or_stale_targets(self):
        expected = ["//fixtures:a", "//fixtures:c"]
        manifest = {"schemaVersion": 2, "stage": "Datadog features 1/8", "revision": "head",
                    "suite": "features", "shard": "0/8", "targets": expected, "ciProfile": "full"}
        ci.validate_manifest(manifest, manifest["stage"], "head", expected)
        for changes in ({"revision": "old"}, {"targets": expected[:1]},
                        {"targets": expected + ["//fixtures:b"]}, {"shard": "1/8"}):
            with self.assertRaises(ValueError):
                ci.validate_manifest(dict(manifest, **changes), manifest["stage"], "head", expected)

    def test_failed_or_stale_invocation_cannot_pass_required_check(self):
        configured = {"actionName": "Datadog scenarios", "commitSha": "head", "pushedRepoUrl": "repo.git"}
        invocation = {"invocationStatus": "COMPLETE_INVOCATION_STATUS", "success": True,
                      "commitSha": "head", "event": [{"buildEvent": {"workflowConfigured": configured}}]}
        ci.validate_invocation(invocation, "Datadog scenarios", "head", "repo")
        for changes in ({"success": False}, {"commitSha": "old"},
                        {"invocationStatus": "PARTIAL_INVOCATION_STATUS"}):
            with self.assertRaises(ValueError):
                ci.validate_invocation(dict(invocation, **changes), "Datadog scenarios", "head", "repo")
        with self.assertRaises(ValueError):
            ci.validate_invocation(invocation, "Datadog capabilities", "head", "repo")
        with self.assertRaises(ValueError):
            ci.validate_invocation(invocation, "Datadog scenarios", "head", "other-repo")

    def test_archive_download_must_match_retained_checksum_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.tar.gz"
            path.write_bytes(b"retained evidence")
            uri = f"bytestream://remote.buildbuddy.io/instance/blobs/{hashlib.sha256(path.read_bytes()).hexdigest()}/{path.stat().st_size}"
            ci.verify_archive(path, uri)
            path.write_bytes(b"different evidence")
            with self.assertRaises(ValueError):
                ci.verify_archive(path, uri)

    def test_stage_download_validates_manifest_after_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            name = "Datadog features 1/8"
            expected = ["//fixtures:a"]
            manifest = {"schemaVersion": 2, "stage": name, "revision": "head",
                        "suite": "features", "shard": "0/8", "targets": expected, "ciProfile": "full"}
            (source / "ci-stage.json").write_text(ci.json.dumps(manifest))
            archive = root / "archive.tar.gz"
            archive_evidence(source, archive)
            contents = archive.read_bytes()
            uri = f"bytestream://remote.buildbuddy.io/instance/blobs/{hashlib.sha256(contents).hexdigest()}/{len(contents)}"
            invocation = {"targetGroups": [{"targets": [{"files": [{"name": "datadog-evidence.tar.gz", "uri": uri}]}]}]}
            from unittest.mock import Mock
            client = Mock()
            client.request.side_effect = lambda url: io.BytesIO(contents)
            downloaded_name, extracted, actual = ci.download_stage(
                client, invocation, name, "invocation", "head", expected, root / "good")
            self.assertEqual((name, manifest), (downloaded_name, actual))
            self.assertTrue((extracted / "ci-stage.json").exists())
            with self.assertRaisesRegex(ValueError, "stage manifest"):
                ci.download_stage(client, invocation, name, "invocation", "old", expected, root / "stale")
            client.request.side_effect = lambda url: io.BytesIO(contents + b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                ci.download_stage(client, invocation, name, "invocation", "head", expected, root / "corrupt")

    def test_archive_cannot_write_outside_extract_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                item = tarfile.TarInfo("../../escape")
                item.size = 4
                archive.addfile(item, io.BytesIO(b"oops"))
            with self.assertRaises(tarfile.FilterError):
                ci.extract_archive(path, Path(directory) / "extracted")


if __name__ == "__main__":
    unittest.main()
