"""Required-check invariants for sharded, retained RBE evidence."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import buildbuddy_ci as ci


class BuildBuddyCITest(unittest.TestCase):
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
        manifest = {"schemaVersion": 1, "stage": "Datadog features 1/8", "revision": "head",
                    "suite": "features", "shard": "0/8", "targets": expected}
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
