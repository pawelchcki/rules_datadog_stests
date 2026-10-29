"""Exercise candidate integrity, built SDK identity, and two-image admission."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from context_tree import context_tree
from datadog_fixture_candidate import go_modules, inspect_image, prepare

TOOLS = Path(__file__).parent
CHECKSUM = "h1:" + "A" * 43 + "="
MODULES = {"github.com/DataDog/dd-trace-go/v2": CHECKSUM,
           **{"github.com/DataDog/dd-trace-go/contrib/" + name: "h1:" + "B" * 43 + "="
              for name in ("database/sql/v2", "gin-gonic/gin/v2", "gorm.io/gorm.v1/v2", "net/http/v2")}}


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def activation(version):
    return ('raise LoadError, "unexpected SDK" unless tracer.version.to_s == "' + version + '"\n').encode()


def image(directory, fixture, version, *, compressed=True, identity=None, diff_id=None):
    directory.mkdir(parents=True, exist_ok=True)
    blobs = directory / "blobs/sha256"
    blobs.mkdir(parents=True, exist_ok=True)

    def add(value, media):
        result = {"digest": digest(value), "size": len(value), "mediaType": media}
        (blobs / result["digest"][7:]).write_bytes(value)
        return result

    if fixture == "ruby":
        files = {f"datadog-ruby/specifications/datadog-{version}.gemspec": ("spec " + version).encode(),
                 f"datadog-ruby/gems/datadog-{version}/lib/datadog/version.rb": ("version " + version).encode()}
        record = {"sdk": "datadog", "version": version,
                  "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()]}
        files["datadog-ruby/activation.rb"] = activation(version)
        (directory.parent / "ruby.identity.json").write_bytes(identity if identity is not None else json.dumps(record).encode())
    else:
        binary = ("native-go-sdk-" + version).encode()
        files = {"opt/app/bin/realworld-gin-datadog": binary}
        info = ("/rootfs/opt/app/bin/realworld-gin-datadog: go1.25\n" + "".join(
            f"\tdep\t{module}\tv{version}\t{checksum}\n" for module, checksum in MODULES.items())).encode()
        (directory.parent / "gin.build-info.txt").write_bytes(identity if identity is not None else info)
        (directory.parent / "gin.binary-sha256").write_text(hashlib.sha256(binary).hexdigest() + "  /rootfs/opt/app/bin/realworld-gin-datadog\n")
    contents = io.BytesIO()
    with tarfile.open(fileobj=contents, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    payload = contents.getvalue()
    layer = add(gzip.compress(payload, mtime=0) if compressed else payload,
                "application/vnd.oci.image.layer.v1.tar" + ("+gzip" if compressed else ""))
    config = add(json.dumps({"architecture": "amd64", "os": "linux", "rootfs": {
        "type": "layers", "diff_ids": [diff_id or digest(payload)]}}).encode(),
        "application/vnd.oci.image.config.v1+json")
    manifest = add(json.dumps({"schemaVersion": 2, "config": config, "layers": [layer]}).encode(),
                   "application/vnd.oci.image.manifest.v1+json")
    (directory / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [manifest]}))
    return layer, digest(payload)


def contexts(root):
    ruby, gin = root / "ruby", root / "gin"
    ruby.mkdir(parents=True)
    (ruby / "Gemfile").write_text('gem "datadog", "2.44.0"\n')
    (ruby / "Gemfile.lock").write_text("    datadog (2.44.0)\n")
    (ruby / "activation.rb").write_bytes(activation("2.44.0"))
    (gin / "datadog").mkdir(parents=True)
    (gin / "datadog/go.mod").write_text("require (\n" + "".join("\t" + module + " v2.11.0\n" for module in MODULES) + ")\n")
    (gin / "datadog/go.sum").write_text("".join(module + " v2.11.0 " + checksum + "\n" for module, checksum in MODULES.items()))
    return {"ruby": ruby, "gin": gin}


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_derives_verified_pins_for_both_layer_encodings(self):
        for compressed in (False, True):
            directory = self.root / str(compressed)
            _, rootfs = image(directory, "ruby", "2.44.0", compressed=compressed)
            result = inspect_image(directory, "ruby", "2.44.0")
            self.assertEqual(result["rootfs"], rootfs)
            self.assertEqual(result["version"], "2.44.0")

    def test_rejects_corrupt_blob_before_accepting_record(self):
        directory = self.root / "image"
        layer, _ = image(directory, "ruby", "2.44.0")
        (directory / "blobs/sha256" / layer["digest"][7:]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "blob (size|content) mismatch"):
            inspect_image(directory, "ruby", "2.44.0")

    def test_does_not_trust_config_diff_id(self):
        directory = self.root / "image"
        image(directory, "ruby", "2.44.0", diff_id="sha256:" + "a" * 64)
        with self.assertRaisesRegex(ValueError, "rootfs content mismatch"):
            inspect_image(directory, "ruby", "2.44.0")

    def test_old_sdk_cannot_be_relabelled(self):
        for fixture, old, new in (("ruby", "2.43.0", "2.44.0"), ("gin", "2.10.1", "2.11.0")):
            directory = self.root / fixture
            image(directory, fixture, old)
            with self.assertRaisesRegex(ValueError, "built (Ruby|Go) SDK differs"):
                inspect_image(directory, fixture, new, MODULES)

    def test_compiled_go_checksum_must_match_locked_source(self):
        directory = self.root / "image"
        image(directory, "gin", "2.11.0")
        with self.assertRaisesRegex(ValueError, "built Go SDK differs"):
            inspect_image(directory, "gin", "2.11.0", dict(MODULES, **{"github.com/DataDog/dd-trace-go/v2": "h1:" + "C" * 43 + "="}))

    def test_go_replacement_is_not_original_registry_module(self):
        directory = self.root / "image"
        identity = f"\tdep\tgithub.com/DataDog/dd-trace-go/v2\tv2.11.0\t{CHECKSUM}\n\t=>\t../replacement\n".encode()
        image(directory, "gin", "2.11.0", identity=identity)
        with self.assertRaisesRegex(ValueError, "built Go SDK differs"):
            inspect_image(directory, "gin", "2.11.0", MODULES)

    def test_stale_locked_contrib_module_rejects_mixed_graph(self):
        source = contexts(self.root / "contexts")["gin"]
        path = source / "datadog/go.mod"
        path.write_text(path.read_text().replace("contrib/gin-gonic/gin/v2 v2.11.0", "contrib/gin-gonic/gin/v2 v2.10.1"))
        with self.assertRaisesRegex(ValueError, "tracer and contrib versions"):
            go_modules(source, "2.11.0")

    def test_every_compiled_contrib_version_and_checksum_is_checked(self):
        directory = self.root / "image"
        image(directory, "gin", "2.11.0")
        path = directory.parent / "gin.build-info.txt"
        original = path.read_bytes()
        contrib = b"github.com/DataDog/dd-trace-go/contrib/gin-gonic/gin/v2"
        for stale in (contrib + b"\tv2.10.1\t" + MODULES[contrib.decode()].encode(),
                      contrib + b"\tv2.11.0\th1:" + b"C" * 43 + b"=",
                      contrib + b"\tv2.11.0\t"):
            path.write_bytes(original.replace(contrib + b"\tv2.11.0\t" + MODULES[contrib.decode()].encode(), stale))
            with self.assertRaisesRegex(ValueError, "built Go SDK differs"):
                inspect_image(directory, "gin", "2.11.0", MODULES)

    def test_runtime_identity_is_bound_to_exported_payload_bytes(self):
        directory = self.root / "image"
        image(directory, "gin", "2.11.0")
        (directory.parent / "gin.binary-sha256").write_text("a" * 64 + "  /rootfs/opt/app/bin/realworld-gin-datadog\n")
        with self.assertRaisesRegex(ValueError, "identity differs from the final payload bytes"):
            inspect_image(directory, "gin", "2.11.0", MODULES)

    def test_records_exact_contexts_only_after_both_verify(self):
        sources = contexts(self.root / "contexts")
        images = self.root / "images"
        image(images / "ruby", "ruby", "2.44.0")
        image(images / "gin", "gin", "2.11.0")
        trees = {fixture: context_tree(path) for fixture, path in sources.items()}
        result = prepare(images, sources, trees)
        self.assertEqual(result["fixtures"]["gin"]["sourceTree"], trees["gin"])
        self.assertEqual(result, prepare(images, sources, trees))
        (sources["ruby"] / "Gemfile.lock").write_text("    datadog (2.43.0)\n")
        with self.assertRaisesRegex(ValueError, "source differs"):
            prepare(images, sources, trees)
        trees["ruby"] = context_tree(sources["ruby"])
        with self.assertRaisesRegex(ValueError, "gem lock"):
            prepare(images, sources, trees)

    def test_payload_limit_applies_while_streaming(self):
        directory = self.root / "image"
        image(directory, "ruby", "2.44.0")
        with patch("datadog_fixture_candidate.LAYER_LIMIT", 3000):
            with self.assertRaisesRegex(ValueError, "uncompressed candidate payload"):
                inspect_image(directory, "ruby", "2.44.0")

    def test_oversized_identity_evidence_is_not_buffered(self):
        directory = self.root / "image"
        image(directory, "ruby", "2.44.0", identity=b"x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "SDK identity evidence exceeds"):
            inspect_image(directory, "ruby", "2.44.0")

    def test_failed_verification_removes_stale_complete_receipt(self):
        sources = contexts(self.root / "contexts")
        images = self.root / "images"
        image(images / "ruby", "ruby", "2.44.0")
        image(images / "gin", "gin", "2.10.1")
        (images / "candidate.json").write_text('{"stale":true}\n')
        arguments = [sys.executable, str(TOOLS / "datadog_fixture_candidate.py"), "--images", str(images)]
        for fixture, source in sources.items():
            arguments += ["--" + fixture + "-context", str(source), "--" + fixture + "-tree", context_tree(source)]
        result = subprocess.run(arguments, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("built Go SDK differs", result.stderr)
        self.assertFalse((images / "candidate.json").exists())

    def test_builder_candidates_bypass_reviewed_cache_and_pin_admission(self):
        root = self.root / "repo"
        tools = root / "tools"
        tools.mkdir(parents=True)
        for name in ("build_datadog_fixtures.sh", "datadog_fixture_candidate.py", "context_tree.py", "materialize_shared_app.py"):
            shutil.copyfile(TOOLS / name, tools / name)
        sources = contexts(self.root / "inputs")
        shutil.copytree(sources["ruby"], root / "fixtures/agents/datadog-ruby")
        shutil.copytree(sources["gin"], root / "fixtures/apps/go/realworld-gin")
        (root / "fixtures/apps/go/realworld-gin/Dockerfile").write_text("FROM scratch\n")
        shared = self.root / "shared"
        (shared / "fixtures/apps/go/realworld-gin").mkdir(parents=True)
        image(self.root / "ruby-image", "ruby", "2.44.0")
        image(self.root / "gin-image", "gin", "2.11.0")
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "python3").symlink_to(sys.executable)
        container = binary / "container"
        container.write_text("#!" + sys.executable + "\n" + '''
import os, pathlib, shutil, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_IMAGES'])
if args[0] == 'build':
    assert '--build-arg' not in args
elif args[0] == 'save':
    fixture = 'ruby' if 'ruby-candidate' in args[-1] else 'gin'
    shutil.copytree(root / (fixture + '-image'), args[args.index('-o') + 1])
elif args[0] == 'image':
    print(('a' if 'ruby-candidate' in args[-1] else 'b') * 64)
elif args[0] == 'run':
    assert '--network=none' in args
    entrypoint = args[args.index('--entrypoint') + 1]
    builder_id = args[args.index('--entrypoint') + 2]
    assert builder_id in ('a' * 64, 'b' * 64)
    name = {'ruby':'ruby.identity.json', 'go':'gin.build-info.txt', 'sha256sum':'gin.binary-sha256'}[entrypoint]
    sys.stdout.write((root / name).read_text())
else:
    raise SystemExit('unexpected container operation')
''')
        container.chmod(0o755)
        output = self.root / "output"
        cache = self.root / "reviewed-cache"
        result = subprocess.run(["bash", str(tools / "build_datadog_fixtures.sh"), str(output), "--candidate"],
                                capture_output=True, text=True, env={**os.environ,
                                    "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
                                    "CONTAINER_TOOL": str(container), "FAKE_IMAGES": str(self.root),
                                    "RULES_STESTS_SOURCE_ROOT": str(shared), "DATADOG_FIXTURE_CACHE": str(cache)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((output / "bazel.flags").read_text(), "")
        self.assertFalse(cache.exists())
        receipt = json.loads((output / "candidate.json").read_text())
        self.assertEqual(receipt["fixtures"]["ruby"]["version"], "2.44.0")
        self.assertEqual(receipt["fixtures"]["gin"]["version"], "2.11.0")


if __name__ == "__main__":
    unittest.main()
