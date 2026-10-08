"""Run provenance regressions against declared Git, with isolated configuration."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


class HermeticGitTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        if "TEST_SRCDIR" in os.environ:
            git = Path(os.environ["TEST_SRCDIR"]) / os.environ["GIT_TEST_BINARY"]
        else:
            # Standalone unittest invocation, outside Bazel's declared runfiles.
            git = Path(shutil.which("git") or "")
        git = git.resolve(strict=True)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "git").symlink_to(git)
        templates = root / "templates"
        templates.mkdir()
        self.enterContext(patch.dict(os.environ, {
            "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TEMPLATE_DIR": str(templates),
        }))
