"""Regression checks for composition of shared and tracer-owned fixture sources."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import materialize_shared_app as app


class SharedAppTest(unittest.TestCase):
    def test_overlay_owns_tracer_code_and_shared_source_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / app.APP
            (shared / "datadog").mkdir(parents=True)
            (shared / "hello.go").write_text("shared app")
            (shared / "datadog/obsolete.go").write_text("old tracer")
            (shared / "BUILD.bazel").write_text("build metadata")
            executable = shared / "server"
            executable.write_text("application launcher")
            executable.chmod(0o755)
            overlay = root / "overlay"
            (overlay / "datadog").mkdir(parents=True)
            (overlay / "Dockerfile").write_text("Datadog image recipe")
            (overlay / "datadog/current.go").write_text("owned tracer")
            with patch.dict(os.environ, {"RULES_STESTS_SOURCE_ROOT": str(root)}):
                app.materialize(app.shared_files(), overlay, root / "output")
            self.assertEqual((root / "output/hello.go").read_text(), "shared app")
            self.assertTrue((root / "output/server").stat().st_mode & 0o111)
            self.assertFalse((root / "output/datadog/obsolete.go").exists())
            self.assertFalse((root / "output/BUILD.bazel").exists())
            self.assertEqual((root / "output/datadog/current.go").read_text(), "owned tracer")
            self.assertTrue((shared / "datadog/obsolete.go").exists())

    def test_composition_preserves_links_and_overlay_replaces_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / app.APP
            shared.mkdir(parents=True)
            (shared / "main.go").write_text("shared app")
            (shared / "link.go").symlink_to("main.go")
            (shared / "missing.go").symlink_to("absent.go")
            (shared / "docs").mkdir()
            (shared / "docs/guide").write_text("shared docs")
            (shared / "docs-link").symlink_to("docs")
            (shared / "override.go").write_text("shared original")
            (shared / "pointer.go").symlink_to("main.go")
            overlay = root / "overlay"
            overlay.mkdir()
            (overlay / "Dockerfile").write_text("Datadog image recipe")
            (overlay / "override.go").symlink_to("link.go")
            (overlay / "pointer.go").write_text("owned overlay")
            (overlay / "overlay-docs").symlink_to("docs")
            (overlay / "overlay-missing").symlink_to("absent")
            output = root / "output"
            with patch.dict(os.environ, {"RULES_STESTS_SOURCE_ROOT": str(root)}):
                app.materialize(app.shared_files(), overlay, output)
            for name, target in [("link.go", "main.go"), ("missing.go", "absent.go"),
                                 ("docs-link", "docs"), ("override.go", "link.go"),
                                 ("overlay-docs", "docs"), ("overlay-missing", "absent")]:
                self.assertTrue((output / name).is_symlink(), name)
                self.assertEqual(os.readlink(output / name), target)
            self.assertFalse((output / "pointer.go").is_symlink())
            self.assertEqual((output / "pointer.go").read_text(), "owned overlay")
            self.assertEqual((output / "main.go").read_text(), "shared app")
            self.assertEqual((shared / "main.go").read_text(), "shared app")

    def test_cquery_reads_sources_from_the_fetched_dependency(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app.subprocess, "check_output") as command:
            command.side_effect = ["external/rules_stests+/fixtures/apps/go/realworld-gin/hello.go\n", "/tmp/output-base\n"]
            files = app.shared_files()
            self.assertEqual(files, [(Path("/tmp/output-base/external/rules_stests+/fixtures/apps/go/realworld-gin/hello.go"), Path("hello.go"))])
            self.assertIn(app.LABEL, command.call_args_list[0].args[0])

    def test_existing_context_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.assertRaisesRegex(ValueError, "already exists"):
                app.materialize([], output, output)


if __name__ == "__main__":
    unittest.main()
