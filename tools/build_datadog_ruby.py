"""Assemble and load an ABI-specific tracing payload with the pinned Ruby."""

from pathlib import Path
import tempfile
import shutil
import subprocess
import sys


def main(arguments):
    output, runtime, app, launcher, bootstrap, msgpack, activation, probes, paths, *files = arguments
    output = Path(output).resolve()
    root = output / "datadog-ruby"
    archives = []
    for filename in files:
        path = Path(filename)
        if path.name == "source.gem":
            archives.append(str(path.resolve()))
            continue
        relative = path.as_posix().split("/data/", 1)[1]
        # Source paths identify repositories, not gem versions. Resolve the
        # package identity from its downloaded archive using the target Ruby.
        repository = next(part for part in path.parts if "datadog_gem_" in part)
        gem = repository.split("datadog_gem_", 1)[1]
        target = root / "packages" / gem / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(activation, root / "activation.rb")
    shutil.copyfile(probes, root / "probes.rb")
    shutil.copyfile(paths, root / "paths.rb")
    # The preparer uses RubyGems itself to retain exact package names/platforms.
    environment = {"LANG": "C.UTF-8", "DD_TRACE_ENABLED": "false", "DD_TRACE_STARTUP_LOGS": "false", "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false", "DD_REMOTE_CONFIGURATION_ENABLED": "false", "DD_PROFILING_ENABLED": "false", "DD_APPSEC_ENABLED": "false", "DD_CRASHTRACKING_ENABLED": "false", "RULES_STESTS_PROBES": "true"}
    with tempfile.TemporaryDirectory(prefix="datadog-ruby-build-") as state:
        environment["APP_STATE_DIR"] = state
        subprocess.run([
            str(Path(launcher).resolve()), "--runtime=ruby", "--rootfs=" + str(Path(app).resolve()),
            "--ruby-rootfs=" + str(Path(runtime).resolve()), "--instance=datadog-build", "--",
            str(Path(bootstrap).resolve()), str(root), str(Path(msgpack).resolve()), *archives,
        ], env=environment, check=True)
    shutil.rmtree(root / "packages")


if __name__ == "__main__":
    main(sys.argv[1:])
