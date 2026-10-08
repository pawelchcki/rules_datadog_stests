#!/usr/bin/env bash
# The evidence driver executes every excluded suite freshly and retains/gates
# its results. Keep other wildcard tests here without executing native cases
# twice when their binaries change.
set -euo pipefail

config="${DATADOG_BAZEL_CONFIG:-local}"
flags=(--config="$config" --build_tests_only)
if [[ "$config" == buildbuddy ]]; then
  flags+=(--spawn_strategy=remote,local)
else
  flags+=(--jobs=4 --local_test_jobs=4)
fi
flags+=("$@")
bazel test "${flags[@]}" -- \
  //... \
  -//fixtures:datadog_suite \
  -//fixtures:datadog_parallel_suite \
  -//fixtures:datadog_external_features_suite \
  -//fixtures:datadog_shared_sdk_suite \
  -//fixtures:datadog_capability_suite
