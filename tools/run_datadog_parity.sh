#!/usr/bin/env bash
# Runs the parity checks whose evidence must come from fresh, retained executions.
set -euo pipefail

archive_evidence() {
  images="$1"
  evidence="$2"
  artifacts="${BUILDBUDDY_ARTIFACTS_DIRECTORY:?BuildBuddy artifact directory is required}"
  mkdir -p "$evidence/logs" "$evidence/fixture-build-logs"
  if [[ -d bazel-testlogs/fixtures ]]; then
    find -L bazel-testlogs/fixtures -name test.log -type f -exec cp -L --no-preserve=mode --parents '{}' "$evidence/logs/" \;
    find -L bazel-testlogs/fixtures -path '*/test.outputs/*' -type f -exec cp -L --no-preserve=mode --parents '{}' "$evidence/logs/" \;
  fi
  for directory in bazel-testlogs/harness examples/plugin_agent/bazel-testlogs; do
    if [[ -d "$directory" ]]; then
      find -L "$directory" -name test.log -type f -exec cp -L --no-preserve=mode --parents '{}' "$evidence/logs/" \;
    fi
  done
  if [[ -d "$images" ]]; then
    find "$images" -maxdepth 1 -name '*.build.log' -type f -exec cp -a '{}' "$evidence/fixture-build-logs/" \;
  fi
  tar -C "$(dirname "$evidence")" -czf "$artifacts/datadog-evidence.tar.gz" "$(basename "$evidence")"
}

if [[ "${1:-}" == "--archive" ]]; then
  archive_evidence "${2:-}" "${3:?usage: run_datadog_parity.sh --archive IMAGE_DIRECTORY EVIDENCE_DIRECTORY}"
  exit
fi

images="${1:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
revision="${2:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
evidence="${3:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
mkdir -p "$evidence"

mapfile -t image_flags < "$images/bazel.flags"
bazel_args=(--config="${DATADOG_BAZEL_CONFIG:-local}")
if [[ "${DATADOG_BAZEL_CONFIG:-local}" == local ]]; then
  bazel_args+=(--jobs=4 --local_test_jobs=4)
  test_download_outputs=all
else
  bazel_args+=(--spawn_strategy=remote,local)
  test_download_outputs=minimal
fi
profiles=(
  //corpus:python-aiohttp-datadog-v4-14-0-v04
  //corpus:python-aiohttp-datadog-v4-14-0-v05
  //corpus:python-django-datadog-v4-14-0-v04
  //corpus:python-django-datadog-v4-14-0-v05
  //corpus:ruby-rails-datadog-v2-42-0-v04
  //corpus:ruby-falcon-datadog-v2-42-0-v04
  //corpus:go-gin-datadog-v2-10-1-v04
)
# DefaultInfo for each profile carries its manifest and validator runfiles.
# Fetch the complete tree: the coverage gate hashes every scenario bytecode.
downloaded_evidence_regex='.*(\.validators|test\.outputs)($|/.*)'
bazel build "${bazel_args[@]}" --remote_download_outputs=toplevel \
  "--remote_download_regex=$downloaded_evidence_regex" \
  "${image_flags[@]}" //tools/datadog_coverage:datadog_coverage "${profiles[@]}"

# Remote tests expose test.log under minimal downloading; the explicit regex
# fetches the complete validator and undeclared-output trees used as evidence.
# Local cached OCI inputs need eager materialization to preserve directory aliases.
test_download_args=(
  --remote_download_outputs="$test_download_outputs"
  "--remote_download_regex=$downloaded_evidence_regex"
)

for execution in 1 2; do
  bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
    --nocache_test_results \
    --test_env="TELEMETRY_TEST_REVISION=$revision" \
    "${image_flags[@]}" \
    //fixtures:datadog_suite
  tools/retain_datadog_evidence.py \
    --revision "$revision" \
    --output "$evidence/execution-$execution" \
    --gate bazel-bin/tools/datadog_coverage/datadog_coverage_/datadog_coverage
done

python3 tools/datadog_report.py \
  --revision "$revision" \
  --execution "$evidence/execution-1" \
  --execution "$evidence/execution-2" \
  --gate bazel-bin/tools/datadog_coverage/datadog_coverage_/datadog_coverage \
  --output "$evidence/datadog-report.html"

bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --nocache_test_results \
  "${image_flags[@]}" \
  //fixtures:datadog_parallel_suite \
  --test_arg=--scenario-concurrency=4 \
  --test_arg=--scenario-repetitions=1
mkdir -p "$evidence/parallel"
find -L bazel-testlogs/fixtures -path '*/test.outputs/stress.*.json' -exec cp -L --no-preserve=mode --parents '{}' "$evidence/parallel/" \;

# This suite includes manual Rails and Gin feature probes, whose individual
# tests are intentionally absent from the wildcard full-suite expansion.
bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --nocache_test_results \
  "${image_flags[@]}" \
  //fixtures:datadog_external_features_suite
mkdir -p "$evidence/features"
find -L bazel-testlogs/fixtures -path '*datadog_external_features*/test.outputs/*' -type f -exec cp -L --no-preserve=mode --parents '{}' "$evidence/features/" \;

# Capability suites reuse existing Python frameworks and include the real Agent
# and local backend. Retain every raw capture beside its receipt before gating.
capability_test_status=0
bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --nocache_test_results \
  "${image_flags[@]}" \
  //fixtures:datadog_capability_suite || capability_test_status=$?
mkdir -p "$evidence/capabilities"
for family in lab upstream_lab agent security signals telemetry profiling llmobs openai otlp ffe remote_config debugger sdk_extra messaging anthropic genai graphql datasets dsm dbm otel_mysql; do
  for directory in bazel-testlogs/fixtures/datadog_"$family"*_test/test.outputs; do
    if [[ -d "$directory" ]]; then
      find -L "$directory" -type f -exec cp -L --no-preserve=mode --parents '{}' "$evidence/capabilities/" \;
    fi
  done
done
if (( capability_test_status != 0 )); then
  exit "$capability_test_status"
fi
capability_evidence=()
while IFS= read -r -d '' receipt; do
  capability_evidence+=(--evidence "$receipt")
done < <(find "$evidence/capabilities" -name 'datadog-*-results.json' -type f -print0)
if [[ ${#capability_evidence[@]} -eq 0 ]]; then
  echo 'No retained Datadog capability receipts' >&2
  exit 1
fi
capability_report=(
  report --inventory docs/datadog-capabilities-inventory.json
  --mapping docs/datadog-capabilities-mapping.json --local-root "$PWD"
  --scope all "${capability_evidence[@]}"
)
python3 tools/datadog_capabilities.py "${capability_report[@]}" \
  --output "$evidence/datadog-capabilities-report.json"
python3 tools/datadog_capabilities.py "${capability_report[@]}" \
  --format markdown --output "$evidence/datadog-capabilities-report.md" \
  --require-percent "${DATADOG_CAPABILITY_MIN_PERCENT:-75}"
