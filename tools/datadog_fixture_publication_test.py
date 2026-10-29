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
from datadog_fixture_candidate_test import contexts, image
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
        self.enterContext(patch.dict(os.environ, {"RULES_STESTS_SOURCE_ROOT": str(self.shared)}))
        self.actual_run = publication.run
        self.enterContext(patch.object(publication, "run", side_effect=self.command))

    def lock_bytes(self):
        return {path: (self.repo / path).read_bytes() for path in (publication.OCI_LOCK, publication.PAYLOAD_LOCK)}

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


if __name__ == "__main__":
    unittest.main()
