"""Complete composed-context provenance and publication regressions."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from context_tree import context_tree
from hermetic_test_tools import HermeticGitTestCase
from materialize_shared_app import materialize

TOOLS = Path(__file__).parent


class ContextTreeTest(HermeticGitTestCase):
    def test_shared_changes_affect_tree_with_unchanged_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared, overlay = root / "main.go", root / "overlay"
            shared.write_text("shared v1")
            overlay.mkdir()
            (overlay / "Dockerfile").write_text("tracer image")
            materialize([(shared, Path("main.go"))], overlay, root / "first")
            original = context_tree(root / "first")
            materialize([(shared, Path("main.go"))], overlay, root / "same")
            self.assertEqual(context_tree(root / "same"), original)
            shared.write_text("shared v2")
            materialize([(shared, Path("main.go"))], overlay, root / "changed")
            self.assertNotEqual(context_tree(root / "changed"), original)
            (overlay / "Dockerfile").write_text("new tracer image")
            materialize([(shared, Path("main.go"))], overlay, root / "overlay-changed")
            self.assertNotEqual(context_tree(root / "overlay-changed"), context_tree(root / "changed"))

    def test_matches_git_raw_tree_modes_links_and_directory_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "context"
            context.mkdir()
            (context / "a").mkdir()
            (context / "a/file").write_bytes(b"raw\0bytes\r\n")
            (context / "a.c").write_text("sorting before directory")
            (context / "launcher").write_text("executable")
            (context / "launcher").chmod(0o755)
            (context / "group-executable").write_text("Git uses owner execute")
            (context / "group-executable").chmod(0o654)
            (context / "link").symlink_to("a/file")
            repository = root / "repository.git"
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run(["git", "init", "--bare", "--object-format=sha1", "-q", repository], check=True, env=env)
            git = ["git", "-c", "core.filemode=true", "-c", "core.autocrlf=false", f"--git-dir={repository}", f"--work-tree={context}"]
            subprocess.run([*git, "add", "--force", "--all"], check=True, env=env)
            expected = subprocess.check_output([*git, "write-tree"], text=True, env=env).strip()
            original = context_tree(context)
            self.assertEqual(original, expected)
            (context / "group-executable").chmod(0o644)
            self.assertEqual(context_tree(context), original)
            (context / "launcher").chmod(0o644)
            self.assertNotEqual(context_tree(context), original)
            (context / "launcher").chmod(0o755)
            (context / "link").unlink()
            (context / "link").symlink_to("a.c")
            self.assertNotEqual(context_tree(context), original)

    def test_named_empty_directory_is_part_of_context_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = context_tree(root)
            self.assertEqual(original, "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
            (root / "empty").mkdir()
            self.assertNotEqual(context_tree(root), original)


class PublicationTest(HermeticGitTestCase):
    def test_publication_uses_built_context_tree_and_rejects_missing_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools, images, binary = root / "tools", root / "images", root / "bin"
            for directory in [tools, images, binary, root / "bazel"]:
                directory.mkdir()
            for name in ["publish_datadog_fixtures.sh", "update_oci_lock.py"]:
                shutil.copyfile(TOOLS / name, tools / name)
            (tools / "local_oci_repository.py").write_text("import pathlib,sys; pathlib.Path(sys.argv[1]).mkdir(parents=True,exist_ok=True)\n")
            source_lock = TOOLS.parent / "bazel/oci_images.lock.bzl"
            shutil.copyfile(source_lock, root / "bazel/oci_images.lock.bzl")
            for fixture in ["ruby", "gin"]:
                image = images / fixture
                image.mkdir()
                (image / "index.json").write_text('{"manifests":[{"digest":"sha256:' + "1" * 64 + '"}]}')
            for path in ["fixtures/agents/datadog-ruby", "fixtures/apps/go/realworld-gin"]:
                context = root / path
                context.mkdir(parents=True)
                (context / "Dockerfile").write_text("FROM scratch\n")
            env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            subprocess.run(["git", "init", "-q", root], check=True, env=env)
            subprocess.run(["git", "add", "fixtures"], cwd=root, check=True, env=env)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixtures"], cwd=root, check=True, env=env)
            # The publisher CLI needs python3 even on an executor that only
            # provides Bazel's hermetic Python runtime.
            (binary / "python3").symlink_to(sys.executable)
            calls = root / "registry-calls"
            skopeo = binary / "skopeo"
            skopeo.write_text("#!/usr/bin/env python3\nimport pathlib,sys\n" + f"p=pathlib.Path({str(calls)!r});p.open('a').write(' '.join(sys.argv[1:])+'\\n')\n" + "if sys.argv[-1].startswith('oci:'): pathlib.Path(sys.argv[-1][4:]).mkdir(parents=True,exist_ok=True)\n")
            skopeo.chmod(0o755)
            script = ["bash", str(tools / "publish_datadog_fixtures.sh"), str(images)]
            for value in [None, "invalid-tree"]:
                if value is not None:
                    (images / "gin.source-tree").write_text(value)
                failed = subprocess.run(script, env=env, capture_output=True, text=True)
                self.assertNotEqual(failed.returncode, 0)
                self.assertFalse(calls.exists(), "invalid provenance reached a registry write")
            context = root / "composed"
            context.mkdir()
            (context / "main.go").write_text("shared application")
            shutil.copyfile(root / "fixtures/apps/go/realworld-gin/Dockerfile", context / "Dockerfile")
            tree = context_tree(context)
            overlay_tree = subprocess.check_output(["git", "rev-parse", "HEAD:fixtures/apps/go/realworld-gin"], cwd=root, text=True, env=env).strip()
            self.assertNotEqual(tree, overlay_tree)
            (images / "gin.source-tree").write_text(tree + "\n")
            published = subprocess.run(script, env=env, capture_output=True, text=True)
            self.assertEqual(published.returncode, 0, published.stderr)
            self.assertIn("gin-datadog-tree-" + tree, calls.read_text())
            self.assertIn("source-tree=" + tree, published.stdout)
            self.assertIn('tree = "' + tree + '"', (root / "bazel/oci_images.lock.bzl").read_text())


if __name__ == "__main__":
    unittest.main()
