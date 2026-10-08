#!/usr/bin/env bash
# Prepare pinned native binaries, execute fresh capability tests, and gate evidence.
set -euo pipefail
apps="${1:?usage: run_go_runtime_capabilities.sh APPLICATIONS EVIDENCE [amd64|arm64]}"
evidence="${2:?provide an evidence directory}"
arch="${3:-amd64}"
ci_profile="${DATADOG_CI_PROFILE:-full}"
case "$ci_profile" in full|pr) ;; *) echo "Unknown CI profile: $ci_profile" >&2; exit 1 ;; esac
version_flags=()
if [[ "$ci_profile" == pr ]]; then
  mapfile -t versions < <(python3 -c 'import sys; sys.path.insert(0,"tools"); from ci_profile import PR_GO_VERSIONS; print("\n".join(PR_GO_VERSIONS))')
  [[ ${#versions[@]} -gt 0 ]] || { echo 'No representative Go versions selected' >&2; exit 1; }
  for version in "${versions[@]}"; do version_flags+=(--version "$version"); done
fi
mkdir -p "$apps" "$evidence"
apps="$(realpath "$apps")"
evidence="$(realpath "$evidence")"
python3 tools/build_go_runtime_matrix.py --output "$apps" --arch "$arch" "${version_flags[@]}"
mapfile -t app_flags < "$apps/bazel.flags"
flags=(--config="${DATADOG_BAZEL_CONFIG:-local}" --jobs=4
  "${app_flags[@]}" --nocache_test_results)
if [[ "${DATADOG_BAZEL_CONFIG:-local}" == buildbuddy ]]; then
  # Materialization, compilation and tests can all use the configured RBE pool.
  # Download captures before retaining and gating them.
  flags+=(--spawn_strategy=remote,local '--remote_download_regex=.*test\.outputs($|/.*)')
else
  flags+=(--local_test_jobs=4)
fi
# The repository fixtures select amd64; a generated package reuses the same
# public macro for native arm64 without changing the checked-in defaults.
targets=(//fixtures:go_runtime_capability_suite)
package=fixtures
if [[ "$arch" == arm64 ]]; then
  package="$(mktemp -d "go_runtime_arm64.XXXXXX")"
  trap 'rm -rf "$package"' EXIT
  cat > "$package/BUILD.bazel" <<'EOF'
load("//rules:go_runtime.bzl", "go_runtime_capability_tests")
go_runtime_capability_tests(name="plain", backend="plain", architecture="arm64")
go_runtime_capability_tests(name="orchestrion", backend="orchestrion", architecture="arm64")
go_runtime_capability_tests(name="alibaba", backend="alibaba", architecture="arm64")
test_suite(name="matrix", tests=[":plain", ":orchestrion", ":alibaba"])
EOF
  targets=("//$package:matrix")
fi
if [[ "$ci_profile" == pr ]]; then
  targets=()
  for backend in plain orchestrion alibaba; do
    for version in "${versions[@]}"; do
      prefix="go_runtime_${backend}_suite"
      if [[ "$arch" == arm64 ]]; then prefix="$backend"; fi
      targets+=("//$package:${prefix}_${version//./_}_test")
    done
  done
fi
result=0
bazel test "${flags[@]}" "${targets[@]}" || result=$?
# The log directory does not depend on the execution platform. `bazel info`
# cannot resolve the RBE platform's external label before package analysis.
logs="$(bazel info bazel-testlogs)"
python3 tools/retain_go_runtime_evidence.py --logs "$logs" --applications "$apps" \
  --output "$evidence" --package "$package" "${version_flags[@]}"
if (( result )); then exit "$result"; fi
python3 tools/go_runtime_report.py --receipts "$evidence/tests" --applications "$evidence/applications" \
  --arch "$arch" "${version_flags[@]}" --output "$evidence/go-runtime-report.json"
