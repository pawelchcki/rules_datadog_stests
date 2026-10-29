#!/usr/bin/env bash
# Builds only local images, verifies each exported OCI manifest, and writes
# flags usable with `bazel --config=local`. Nothing is pushed to a registry.
set -euo pipefail
out="${1:?usage: build_datadog_fixtures.sh OUTPUT_DIRECTORY}"
candidate=false
if [[ "${2:-}" == --candidate && "$#" == 2 ]]; then
  candidate=true
elif [[ "$#" != 1 ]]; then
  printf 'usage: build_datadog_fixtures.sh OUTPUT_DIRECTORY [--candidate]\n' >&2
  exit 2
fi
mkdir -p "$out"
out="$(realpath "$out")"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
container_tool="${CONTAINER_TOOL:-podman}"
container_build_network="${CONTAINER_BUILD_NETWORK:-}"
container_build_network_args=()
if [[ -n "$container_build_network" ]]; then
  container_build_network_args=(--network "$container_build_network")
fi
cd "$root"
: > "$out/bazel.flags"
# A failed rebuild must not retain provenance from a previous output.
rm -f -- "$out/gin.source-tree"
rm -f -- "$out/candidate.json"

cache_root="${DATADOG_FIXTURE_CACHE:-}"
# Candidates have not passed review; never mix them into the reviewed cache.
if "$candidate"; then
  cache_root=""
fi
if [[ -n "$cache_root" ]]; then
  mkdir -p "$cache_root"
  cache_root="$(realpath "$cache_root")"
fi

# Hash names, kinds, modes, symlink targets, and file bytes. Git's object ID is
# insufficient here because these contexts may contain generated or ignored
# inputs, and container builds observe executable bits and symlinks too.
fixture_cache_key() {
  context="$1"
  tool_identity="$("$container_tool" version 2>&1)"
  python3 - "$context" "$root/tools/build_datadog_fixtures.sh" \
    "$root/tools/local_oci_repository.py" "$container_tool" \
    "$container_build_network" "$tool_identity" <<'PY'
import hashlib
import os
from pathlib import Path
import stat
import sys

context, builder, validator, *values = sys.argv[1:]
h = hashlib.sha256()
for value in (builder, validator, *values):
    encoded = value.encode()
    h.update(len(encoded).to_bytes(8, "big"))
    h.update(encoded)
for helper in (Path(builder), Path(validator)):
    metadata = helper.lstat()
    h.update(oct(stat.S_IMODE(metadata.st_mode)).encode())
    h.update(helper.read_bytes())
root = Path(context)
for path in sorted([root, *root.rglob("*")], key=lambda p: os.fsencode(str(p.relative_to(root)))):
    relative = b"." if path == root else os.fsencode(str(path.relative_to(root)))
    metadata = path.lstat()
    kind = stat.S_IFMT(metadata.st_mode)
    for value in (relative, str(kind).encode(), oct(stat.S_IMODE(metadata.st_mode)).encode()):
        h.update(len(value).to_bytes(8, "big"))
        h.update(value)
    if path.is_symlink():
        value = os.fsencode(os.readlink(path))
        h.update(len(value).to_bytes(8, "big"))
        h.update(value)
    elif path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
print(h.hexdigest())
PY
}
# These are the uncompressed rootfs layer identities from the reviewed images
# declared by the Datadog implementation profiles. OCI envelope metadata and
# compression may vary across container-tool versions; the executed bytes may not.
for fixture in ruby gin; do
  if [[ "$fixture" == ruby ]]; then
    context=fixtures/agents/datadog-ruby
    repository=datadog_ruby_linux_amd64
    ruby_context="$context"
  else
    overlay=fixtures/apps/go/realworld-gin
    mkdir -p "$out/contexts"
    context="$(mktemp -d "$out/contexts/gin.XXXXXX")/app"
    python3 tools/materialize_shared_app.py --overlay "$overlay" --output "$context"
    source_tree="$(python3 tools/context_tree.py "$context")"
    repository=gin_datadog_realworld_linux_amd64
    gin_context="$context"
  fi

  if "$candidate"; then
    candidate_tree="$(python3 tools/context_tree.py "$context")"
    printf '%s\n' "$candidate_tree" > "$out/$fixture.source-tree"
    image="localhost/rules-stests-$fixture-candidate:source-$candidate_tree"
  else
    read -r sdk_version rootfs_digest < <(python3 - "$fixture" "$context" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "tools")
from datadog_fixture_publication import payloads
from datadog_fixture_candidate import sdk_version
pin = payloads(Path("bazel/datadog_fixture_payloads.json"))[sys.argv[1]]
if sdk_version(Path(sys.argv[2]), sys.argv[1]) != pin["version"]:
    raise ValueError("source SDK version differs from reviewed payload pin")
print(pin["version"], pin["rootfs"])
PY
)
    test -n "$sdk_version" && test -n "$rootfs_digest"
    if [[ "$fixture" == ruby ]]; then
      image="localhost/rules-stests-datadog-ruby:$sdk_version"
    else
      image="localhost/rules-stests-gin-datadog:$sdk_version"
    fi
  fi

  export_dir="$out/$fixture"
  cache_entry=""
  if [[ -n "$cache_root" ]]; then
    cache_key="$(fixture_cache_key "$context")"
    cache_entry="$cache_root/$fixture-$cache_key"
    if [[ -d "$cache_entry" ]]; then
      if flag="$(python3 tools/local_oci_repository.py \
          "$cache_entry" "$repository" --rootfs-digest "$rootfs_digest" 2>"$out/$fixture.build.log")"; then
        printf 'validated fixture cache hit: %s\n' "$cache_entry" > "$out/$fixture.build.log"
        printf 'Datadog fixture cache hit: %s (reviewed payload verified)\n' "$fixture"
        printf '%s\n' "$flag" >> "$out/bazel.flags"
        if [[ "$fixture" == gin ]]; then
          printf '%s\n' "$source_tree" > "$out/gin.source-tree"
        fi
        continue
      fi
      rejected="$cache_root/.rejected-$fixture-$cache_key-$$"
      if ! mv "$cache_entry" "$rejected"; then
        printf 'fixture cache entry failed validation and could not be quarantined: %s\n' "$cache_entry" >&2
        exit 1
      fi
      printf 'fixture cache entry failed validation; rebuilding (quarantined at %s)\n' "$rejected" >> "$out/$fixture.build.log"
    fi
    staging="$(mktemp -d "$cache_root/.building-$fixture-$cache_key.XXXXXX")"
    export_dir="$staging/export"
    printf 'Datadog fixture cache miss: %s; building reviewed payload\n' "$fixture"
  fi

  if "$container_tool" build --timestamp 0 "${container_build_network_args[@]}" -t "$image" "$context" >> "$out/$fixture.build.log" 2>&1; then
    :
  else
    status=$?
    cat "$out/$fixture.build.log" >&2
    exit "$status"
  fi
  "$container_tool" save --format oci-dir -o "$export_dir" "$image"
  if "$candidate"; then
    # Inspect the cached builder stage without changing the final payload. Bind
    # runtime observations to immutable builder/image bytes, never a mutable tag.
    "$container_tool" build --target builder --timestamp 0 "${container_build_network_args[@]}" \
      -t "$image-builder" "$context" >> "$out/$fixture.build.log" 2>&1
    builder_id="$("$container_tool" image inspect --format '{{.Id}}' "$image-builder")"
    if [[ ! "$builder_id" =~ ^(sha256:)?[0-9a-f]{64}$ ]]; then
      printf 'candidate builder did not identify its immutable image\n' >&2
      exit 1
    fi
    if [[ "$fixture" == ruby ]]; then
      "$container_tool" run --rm --network=none --workdir /build \
        --env DD_TRACE_ENABLED=false --env DD_INSTRUMENTATION_TELEMETRY_ENABLED=false \
        --entrypoint ruby "$builder_id" -rjson -rdigest -rbundler/setup -rdatadog -e '
          spec = Gem.loaded_specs.fetch("datadog")
          paths = ["datadog-ruby/specifications/" + File.basename(spec.loaded_from),
                   "datadog-ruby/gems/" + spec.full_name + "/lib/datadog/version.rb"]
          files = paths.map { |path| {path: path, sha256: Digest::SHA256.file("/rootfs/" + path).hexdigest} }
          puts JSON.generate(sdk: "datadog", version: spec.version.to_s, files: files)
        ' > "$out/ruby.identity.json"
    else
      "$container_tool" run --rm --network=none --entrypoint go "$builder_id" \
        version -m /rootfs/opt/app/bin/realworld-gin-datadog > "$out/gin.build-info.txt"
      "$container_tool" run --rm --network=none --entrypoint sha256sum "$builder_id" \
        /rootfs/opt/app/bin/realworld-gin-datadog > "$out/gin.binary-sha256"
    fi
    test "$candidate_tree" = "$(python3 tools/context_tree.py "$context")"
    # No reviewed pin or Bazel override is emitted for an unreviewed build.
    continue
  fi
  flag="$(python3 tools/local_oci_repository.py \
    "$export_dir" "$repository" --rootfs-digest "$rootfs_digest")"
  if [[ -n "$cache_entry" ]]; then
    if ! mv -T "$export_dir" "$cache_entry" 2>/dev/null; then
      # A concurrent builder may have won. Trust its entry only after applying
      # the same payload validation as any other hit.
      flag="$(python3 tools/local_oci_repository.py \
        "$cache_entry" "$repository" --rootfs-digest "$rootfs_digest")" || {
          printf 'concurrent fixture cache entry failed validation: %s\n' "$cache_entry" >&2
          exit 1
        }
    else
      flag="$(python3 tools/local_oci_repository.py \
        "$cache_entry" "$repository" --rootfs-digest "$rootfs_digest")"
    fi
    # Also discard our unused export if another builder published first.
    rm -rf -- "$staging"
  fi
  printf '%s\n' "$flag" >> "$out/bazel.flags"
  if [[ "$fixture" == gin ]]; then
    printf '%s\n' "$source_tree" > "$out/gin.source-tree"
  fi
done

if "$candidate"; then
  python3 tools/datadog_fixture_candidate.py --images "$out" \
    --ruby-context "$ruby_context" --ruby-tree "$(cat "$out/ruby.source-tree")" \
    --gin-context "$gin_context" --gin-tree "$(cat "$out/gin.source-tree")"
fi
