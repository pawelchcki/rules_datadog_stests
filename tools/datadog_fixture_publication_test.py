"""Publication uses actual OCI bytes and never accepts partial verification."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import datadog_fixture_publication as publication
from datadog_fixture_candidate import prepare
from datadog_fixture_candidate_test import activation, contexts, image
from context_tree import context_tree

TOOLS = Path(__file__).parent


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.root / "repo"
        (self.repo / "bazel").mkdir(parents=True)
        (self.repo / "tools").mkdir()
        shutil.copyfile(TOOLS / "local_oci_repository.py", self.repo / "tools/local_oci_repository.py")
        (self.repo / publication.OCI_LOCK).write_text('''OCI_IMAGES = {
    "gin_datadog_realworld": struct(
        repository = "ghcr.io/pawelchcki/rules_stest_apps",
        digest = "sha256:''' + "1" * 64 + '''",
        tree = "''' + "2" * 40 + '''",
    ),
}
DATADOG_RUBY = struct(
    repository = "ghcr.io/pawelchcki/rules_stest_agents",
    digest = "sha256:''' + "3" * 64 + '''",
    tree = "''' + "4" * 40 + '''",
    version = "2.43.0",
)
''')
        self.sources = contexts(self.root / "source")
        for fixture, (relative, *_) in publication.FIXTURES.items():
            shutil.copytree(self.sources[fixture], self.repo / relative)
        (self.repo / publication.APP / "Dockerfile").write_text("FROM scratch\n")
        self.shared = self.root / "shared"
        (self.shared / publication.APP).mkdir(parents=True)
        self.images = self.root / "images"
        pins = {}
        for fixture, version in (("ruby", "2.44.0"), ("gin", "2.11.0")):
            _, rootfs = image(self.images / fixture, fixture, version)
            pins[fixture] = {"version": "2.43.0" if fixture == "ruby" else "2.10.1", "rootfs": rootfs}
        (self.repo / publication.PAYLOAD_LOCK).write_text(json.dumps(pins))
        self.contexts = {key: self.repo / value[0] for key, value in publication.FIXTURES.items()}
        self.trees = {key: context_tree(value) for key, value in self.contexts.items()}
        self.receipt = prepare(self.images, self.contexts, self.trees)
        (self.images / "candidate.json").write_text(json.dumps(self.receipt))
        self.original = self.lock_bytes()
        self.transfers = []
        self.fail_pull = None
        self.corrupt_pull = None
        self.after_pull = None
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("RULES_STESTS_SOURCE_ROOT", None)
        self.enterContext(patch.object(publication, "shared_files", return_value=[]))
        self.actual_run = publication.run
        self.enterContext(patch.object(publication, "run", side_effect=self.command))

    def test_ephemeral_registry_auth_uses_only_stdin_and_never_reaches_anonymous_reads(self):
        import base64
        import secrets
        from types import SimpleNamespace
        # Generated, non-credential bytes stay in memory; no literal token is
        # stored in a fixture and failure assertions never render its value.
        token=secrets.token_urlsafe(24)
        with patch.dict(os.environ,{"DATADOG_REGISTRY_TOKEN":token,"DATADOG_REGISTRY_USER":"github-actions[bot]"}), \
                patch.object(publication.subprocess,"run",return_value=SimpleNamespace(stdout=b"")) as command:
            self.actual_run(["skopeo","copy","oci:/private/candidate","docker://ghcr.io/pawelchcki/rules_stest_agents:source"],root=self.repo)
            args,options=command.call_args
            self.assertTrue(all(token not in item for item in args[0]))
            self.assertTrue("DATADOG_REGISTRY_TOKEN" not in options["env"])
            self.assertIn("/dev/stdin",args[0])
            encoded=json.loads(options["input"])["auths"]["ghcr.io"]["auth"]
            self.assertTrue(base64.b64decode(encoded).decode()=="github-actions[bot]:"+token)
            self.actual_run(["skopeo","copy","--src-no-creds","docker://ghcr.io/pawelchcki/rules_stest_agents@sha256:"+"1"*64,"oci:/private/pulled"],root=self.repo)
            self.assertIsNone(command.call_args.kwargs["input"])
            self.assertTrue("DATADOG_REGISTRY_TOKEN" not in command.call_args.kwargs["env"])

    def lock_bytes(self):
        return {path: (self.repo / path).read_bytes() for path in (publication.OCI_LOCK, publication.PAYLOAD_LOCK)}

    def test_real_child_stderr_and_stdin_never_escape_failure(self):
        import secrets
        token = secrets.token_urlsafe(24)
        actual_subprocess = subprocess.run

        def reject(arguments, **options):
            # A real child echoes the entire in-memory auth document to its
            # stderr, as an unsafe provider could. Neither it nor the child
            # exception may become part of the public diagnostic.
            return actual_subprocess(
                [sys.executable, "-c", "import sys; sys.stderr.buffer.write(sys.stdin.buffer.read()); sys.exit(7)"],
                **options)

        with patch.dict(os.environ, {"DATADOG_REGISTRY_TOKEN": token, "DATADOG_REGISTRY_USER": "github-actions[bot]"}), \
                patch.object(publication.subprocess, "run", side_effect=reject):
            with self.assertRaises(publication.PublicationCommandError) as failure:
                self.actual_run(["skopeo", "copy", "oci:/private/candidate", "docker://ghcr.io/pawelchcki/rules_stest_agents:source"], root=self.repo)
        self.assertEqual(str(failure.exception), "Datadog fixture publication failed (ruby-registry-push; command-failed; exit 7); candidate not accepted")
        self.assertIsNone(failure.exception.__context__)
        self.assertTrue(token not in repr(vars(failure.exception)))
        self.assertTrue(token not in str(failure.exception.args))

    def test_registry_failures_use_fixed_categories_for_push_and_anonymous_pull(self):
        cases = (
            ("permission_denied: write_package", "registry-permission-denied"),
            ("unauthorized", "registry-authentication-failed"),
            ("unexpected end of JSON input", "invalid-json-input"),
            ("manifest unknown", "registry-manifest-missing"),
            ("connection refused", "registry-unreachable"),
            ("unrecognized provider diagnostic", "command-failed"),
        )
        for message, category in cases:
            for fixture, (_, repository, _, _) in publication.FIXTURES.items():
                for operation in ("registry-push", "anonymous-pull"):
                    args = (["skopeo", "copy", "oci:/private/candidate", "docker://" + repository + ":source"]
                            if operation == "registry-push" else
                            ["skopeo", "copy", "--src-no-creds", "docker://" + repository + "@sha256:" + "1" * 64, "oci:/private/pulled"])
                    child_error = subprocess.CalledProcessError(1, args, stderr=message.encode())
                    with patch.object(publication.subprocess, "run", side_effect=child_error):
                        with self.assertRaises(publication.PublicationCommandError) as failure:
                            self.actual_run(args, root=self.repo)
                    self.assertEqual(str(failure.exception), f"Datadog fixture publication failed ({fixture}-{operation}; {category}; exit 1); candidate not accepted")
                    self.assertIsNone(failure.exception.__context__)

    def test_payload_failure_identifies_fixture_without_exposing_child_output(self):
        child_error = subprocess.CalledProcessError(1, ["private-arguments"], stderr=b"private-provider-output")
        with patch.object(publication.subprocess, "run", side_effect=child_error):
            with self.assertRaises(publication.PublicationCommandError) as failure:
                self.actual_run([sys.executable, self.repo / "tools/local_oci_repository.py", self.images / "gin", "gin_datadog_realworld"], root=self.repo)
        self.assertEqual(str(failure.exception), "Datadog fixture publication failed (gin-payload-verification; command-failed; exit 1); candidate not accepted")

    def command(self, arguments, *, root, capture=False):
        args = [str(a) for a in arguments]
        if args[0] == "git":
            return ""
        if args[0] != "skopeo":
            return self.actual_run(arguments, root=root, capture=capture)
        self.transfers.append(args)
        if "--src-no-creds" not in args:
            self.assertIn("--preserve-digests", args)
            self.assertIn("-tree-", args[-1])
            return
        fixture = "ruby" if "rules_stest_agents@" in args[-2] else "gin"
        if fixture == self.fail_pull:
            raise subprocess.CalledProcessError(1, "skopeo")
        pulled = Path(args[-1].removeprefix("oci:"))
        shutil.copytree(self.images / fixture, pulled)
        if fixture == self.corrupt_pull:
            index = json.loads((pulled / "index.json").read_text())
            (pulled / "blobs/sha256" / index["manifests"][0]["digest"][7:]).write_bytes(b"corrupt")
        if self.after_pull:
            self.after_pull(fixture)

    def publish(self, *, candidate=True):
        publication.publish(self.repo, self.images, candidate=candidate)

    def test_updates_both_pins_only_after_anonymous_digest_verification(self):
        self.publish()
        self.assertEqual(len(self.transfers), 4)
        locked = publication.payloads(self.repo / publication.PAYLOAD_LOCK)
        self.assertEqual(locked["ruby"]["version"], "2.44.0")
        self.assertEqual(locked["gin"]["version"], "2.11.0")
        text = (self.repo / publication.OCI_LOCK).read_text()
        for fixture, pin in self.receipt["fixtures"].items():
            self.assertEqual(locked[fixture]["rootfs"], pin["rootfs"])
            self.assertIn(pin["manifest"], text)
            self.assertIn(pin["sourceTree"], text)
        self.assertIn('version = "2.44.0"', text)

    def test_second_invalid_image_prevents_every_registry_write(self):
        index = json.loads((self.images / "gin/index.json").read_text())
        (self.images / "gin/blobs/sha256" / index["manifests"][0]["digest"][7:]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.transfers, [])
        self.assertEqual(self.lock_bytes(), self.original)

    def test_forged_receipt_does_not_replace_inspection(self):
        self.receipt["fixtures"]["ruby"]["rootfs"] = "sha256:" + "f" * 64
        (self.images / "candidate.json").write_text(json.dumps(self.receipt))
        with self.assertRaisesRegex(ValueError, "receipt differs"):
            self.publish()
        self.assertEqual(self.transfers, [])
        self.assertEqual(self.lock_bytes(), self.original)

    def test_stale_source_receipt_cannot_publish(self):
        (self.contexts["gin"] / "Dockerfile").write_text("FROM scratch\n# changed recipe\n")
        with self.assertRaisesRegex(ValueError, "receipt differs"):
            self.publish()
        self.assertEqual(self.transfers, [])

    def test_second_anonymous_pull_failure_preserves_both_locks(self):
        self.fail_pull = "gin"
        with self.assertRaises(subprocess.SubprocessError):
            self.publish()
        self.assertEqual(len(self.transfers), 4)
        self.assertEqual(self.lock_bytes(), self.original)

    def test_anonymous_bytes_must_match_the_immutable_manifest(self):
        self.corrupt_pull = "gin"
        with self.assertRaises(subprocess.SubprocessError):
            self.publish()
        self.assertEqual(self.lock_bytes(), self.original)

    def test_source_race_during_pull_prevents_lock_installation(self):
        def change(fixture):
            if fixture == "ruby":
                (self.contexts["ruby"] / "new-input").write_text("changed source")
        self.after_pull = change
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.publish()
        self.assertEqual(self.lock_bytes(), self.original)
        self.assertEqual(len(self.transfers), 2)

    def test_concurrent_lock_edit_is_never_overwritten(self):
        def change(fixture):
            if fixture == "gin":
                (self.repo / publication.OCI_LOCK).write_text("concurrent edit\n")
        self.after_pull = change
        with self.assertRaisesRegex(ValueError, "locks changed concurrently"):
            self.publish()
        self.assertEqual((self.repo / publication.OCI_LOCK).read_text(), "concurrent edit\n")
        self.assertEqual((self.repo / publication.PAYLOAD_LOCK).read_bytes(), self.original[publication.PAYLOAD_LOCK])

    def test_malformed_lock_fails_before_registry_access(self):
        (self.repo / publication.OCI_LOCK).write_text("unexpected lock syntax\n")
        with self.assertRaisesRegex(ValueError, "OCI lock structure"):
            self.publish()
        self.assertEqual(self.transfers, [])

    def test_default_mode_does_not_promote_new_sdk_sources(self):
        (self.images / "gin.source-tree").write_text(self.trees["gin"] + "\n")
        with self.assertRaisesRegex(ValueError, "source SDK version differs"):
            self.publish(candidate=False)
        self.assertEqual(self.transfers, [])
        self.assertEqual(self.lock_bytes(), self.original)

    def test_default_mode_preserves_reviewed_payload_lock(self):
        reviewed = {key: {field: pin[field] for field in ("version", "rootfs")}
                    for key, pin in self.receipt["fixtures"].items()}
        path = self.repo / publication.PAYLOAD_LOCK
        path.write_text(json.dumps(reviewed))
        original = path.read_bytes()
        (self.images / "gin.source-tree").write_text(self.trees["gin"] + "\n")
        # Ordinary builds have no candidate SDK evidence. Reviewed rootfs
        # hashes, rather than a candidate receipt, authorize their bytes.
        (self.images / "candidate.json").unlink()
        (self.images / "ruby.identity.json").unlink()
        self.publish(candidate=False)
        self.assertEqual(len(self.transfers), 4)
        self.assertEqual(path.read_bytes(), original)

    def test_payload_lock_refuses_unknown_missing_and_malformed_pins(self):
        path = self.repo / publication.PAYLOAD_LOCK
        valid = json.loads(path.read_text())
        for bad in ({}, {**valid, "other": valid["ruby"]},
                    {**valid, "ruby": dict(valid["ruby"], rootfs="latest")},
                    {**valid, "gin": dict(valid["gin"], version="latest")},
                    {**valid, "ruby": dict(valid["ruby"], extra=True)}):
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                publication.payloads(path)

    def test_dirty_context_fails_without_registry_access(self):
        with patch.object(publication, "run", return_value=" M Gemfile"):
            with self.assertRaisesRegex(ValueError, "committed source"):
                publication.clean_contexts(self.repo)

    def test_stale_activation_prevents_every_registry_write(self):
        path = self.contexts["ruby"] / "activation.rb"
        path.write_bytes(activation("2.43.0"))
        with self.assertRaisesRegex(ValueError, "activation version differs"):
            self.publish()
        self.assertEqual(self.transfers, [])
        self.assertEqual(self.lock_bytes(), self.original)

    def test_activation_in_export_must_match_the_verified_source(self):
        path = self.contexts["ruby"] / "activation.rb"
        path.write_bytes(path.read_bytes() + b"# different bootstrap\n")
        with self.assertRaisesRegex(ValueError, "SDK identity differs"):
            self.publish()
        self.assertEqual(self.transfers, [])

    def test_development_shared_source_override_cannot_publish(self):
        with patch.dict(os.environ, {"RULES_STESTS_SOURCE_ROOT": str(self.shared)}):
            with self.assertRaisesRegex(ValueError, "development shared-source override"):
                self.publish()
        self.assertEqual(self.transfers, [])

    def test_second_lock_replacement_failure_rolls_back_the_first(self):
        actual = publication.os.replace
        failed = False
        def replace(source, target):
            nonlocal failed
            if Path(target) == self.repo / publication.PAYLOAD_LOCK and not failed:
                failed = True
                raise OSError("controlled replacement failure")
            return actual(source, target)
        with patch.object(publication.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.publish()
        self.assertTrue(failed)
        self.assertEqual(self.lock_bytes(), self.original)
        self.assertEqual(list((self.repo / "bazel").glob(".datadog-pins-*")), [])

    def test_second_replacement_interrupt_rolls_back_the_first(self):
        actual = publication.os.replace
        interrupted = False
        def replace(source, target):
            nonlocal interrupted
            if Path(target) == self.repo / publication.PAYLOAD_LOCK and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return actual(source, target)
        with patch.object(publication.os, "replace", side_effect=replace):
            with self.assertRaises(KeyboardInterrupt):
                self.publish()
        self.assertEqual(self.lock_bytes(), self.original)

    def test_staging_failure_changes_neither_original(self):
        with patch.object(publication.os, "fsync", side_effect=OSError("controlled staging failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.lock_bytes(), self.original)

    def test_failed_rollback_retains_original_copies_for_recovery(self):
        actual = publication.os.replace
        def replace(source, target):
            if Path(target) == self.repo / publication.PAYLOAD_LOCK:
                raise OSError("controlled persistent storage failure")
            return actual(source, target)
        with patch.object(publication.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.publish()
        staging, = (self.repo / "bazel").glob(".datadog-pins-*")
        for index, path in enumerate(self.original):
            self.assertEqual((staging / f"{index}.old").read_bytes(), self.original[path])


if __name__ == "__main__":
    unittest.main()
