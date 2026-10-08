#!/usr/bin/env bash
# Build every rule, including manual fixtures, twice and compare output digests.
set -euo pipefail
targets="$(mktemp "${TMPDIR:-/tmp}/datadog-determinism-targets.XXXXXX")"
trap 'rm -f "$targets"' EXIT
bazel query 'kind(".* rule", //...)' --output=label > "$targets"
[[ -s "$targets" ]]
bb detect nondeterminism --bazel_command="build --config=${DATADOG_BAZEL_CONFIG:-local} --jobs=${DATADOG_PARITY_BUILD_JOBS:-16} --target_pattern_file=$targets"
