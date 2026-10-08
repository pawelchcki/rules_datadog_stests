#!/usr/bin/env bash
# Runs the parity checks whose evidence must come from fresh, retained executions.
set -euo pipefail

archive_evidence() {
  images="$1"
  evidence="$2"
  artifacts="${BUILDBUDDY_ARTIFACTS_DIRECTORY:?BuildBuddy artifact directory is required}"
  mkdir -p "$evidence/logs" "$evidence/fixture-build-logs"
  if [[ "${DATADOG_PARITY_STAGE:-all}" == all && -d bazel-testlogs/fixtures ]]; then
    find -L bazel-testlogs/fixtures -name test.log -type f -exec cp -L --no-preserve=mode --parents -t "$evidence/logs/" '{}' +
    # Include outputs from failures too. The archiver stores identical captures
    # once while preserving both the suite-specific and diagnostic paths.
    find -L bazel-testlogs/fixtures -path '*/test.outputs/*' -type f -exec cp -L --no-preserve=mode --parents -t "$evidence/logs/" '{}' +
  fi
  for directory in bazel-testlogs/harness examples/plugin_agent/bazel-testlogs; do
    if [[ "${DATADOG_PARITY_STAGE:-all}" != all && "${DATADOG_PARITY_STAGE:-all}" != scenarios ]]; then continue; fi
    if [[ -d "$directory" ]]; then
      find -L "$directory" -name test.log -type f -exec cp -L --no-preserve=mode --parents -t "$evidence/logs/" '{}' +
    fi
  done
  if [[ -d "$images" ]]; then
    find "$images" -maxdepth 1 -name '*.build.log' -type f -exec cp -a -t "$evidence/fixture-build-logs/" '{}' +
  fi
  python3 tools/archive_evidence.py "$evidence" "$artifacts/datadog-evidence.tar.gz"
}

if [[ "${1:-}" == "--archive" ]]; then
  archive_evidence "${2:-}" "${3:?usage: run_datadog_parity.sh --archive IMAGE_DIRECTORY EVIDENCE_DIRECTORY}"
  exit
fi

images="${1:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
revision="${2:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
evidence="${3:?usage: run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY}"
mkdir -p "$evidence"
stage="${DATADOG_PARITY_STAGE:-all}"
ci_profile="${DATADOG_CI_PROFILE:-full}"
case "$ci_profile" in full|pr) ;; *) echo "Unknown CI profile: $ci_profile" >&2; exit 1 ;; esac
export DATADOG_CI_PROFILE="$ci_profile"
case "$stage" in all|scenarios|features|shared-sdk|capabilities|gate) ;; *) echo "Unknown parity stage: $stage" >&2; exit 1 ;; esac

mapfile -t image_flags < "$images/bazel.flags"
# Native services share executor ports and intake resources. Keep test worker
# budgets separate from compilation so cold builds can use the RBE fleet.
jobs="${DATADOG_PARITY_JOBS:-4}"
bazel_args=(--config="${DATADOG_BAZEL_CONFIG:-local}" --jobs="$jobs")
build_args=(--config="${DATADOG_BAZEL_CONFIG:-local}" --jobs="${DATADOG_PARITY_BUILD_JOBS:-$jobs}")
if [[ "${DATADOG_BAZEL_CONFIG:-local}" != buildbuddy ]]; then
  bazel_args+=(--local_test_jobs="$jobs")
  test_download_outputs=all
else
  bazel_args+=(--spawn_strategy=remote,local)
  build_args+=(--spawn_strategy=remote,local)
  test_download_outputs=minimal
fi
downloaded_evidence_regex='.*(\.validators|test\.outputs)($|/.*)'
# Remote tests expose test.log under minimal downloading; the explicit regex
# fetches the complete validator and undeclared-output trees used as evidence.
# Local cached OCI inputs need eager materialization to preserve directory aliases.
test_download_args=(
  --remote_download_outputs="$test_download_outputs"
  "--remote_download_regex=$downloaded_evidence_regex"
)

retain_test_outputs() {
  local destination="$1" target directory
  shift
  local -a outputs=() logs=()
  mkdir -p "$destination"
  for target in "$@"; do
    directory="bazel-testlogs/fixtures/${target##*:}"
    if [[ -d "$directory/test.outputs" ]]; then outputs+=("$directory/test.outputs"); fi
    if [[ -f "$directory/test.log" ]]; then logs+=("$directory/test.log"); fi
  done
  # Batch across targets as well as files. A full feature/SDK stage otherwise
  # starts a find and up to two cp processes for each independent test.
  if (( ${#outputs[@]} )); then
    find -L "${outputs[@]}" -type f -exec cp -L --no-preserve=mode --parents -t "$destination/" '{}' +
  fi
  if (( ${#logs[@]} )); then
    cp -L --no-preserve=mode --parents -t "$destination/" "${logs[@]}"
  fi
}

if [[ "$stage" == all || "$stage" == scenarios ]]; then
profiles=(
  //corpus:python-aiohttp-datadog-v4-15-5-v04
  //corpus:python-aiohttp-datadog-v4-15-5-v05
  //corpus:python-django-datadog-v4-15-5-v04
  //corpus:python-django-datadog-v4-15-5-v05
  //corpus:ruby-rails-datadog-v2-43-0-v04
  //corpus:ruby-falcon-datadog-v2-43-0-v04
  //corpus:go-gin-datadog-v2-10-1-v04
)
bazel build "${build_args[@]}" --remote_download_outputs=toplevel "${image_flags[@]}" //harness:datadog_ruby_compatibility
mapfile -t ruby_profiles < <(python3 -c 'import json,os,sys; sys.path.insert(0,"tools"); from ci_profile import ruby_runtimes; d=json.load(open("bazel-bin/harness/datadog_ruby_compatibility.json")); print("\n".join("//corpus:ruby-sinatra-" + r["series"].replace(".", "-") + "-datadog-v2-43-0-v04" for r in ruby_runtimes(d,os.environ["DATADOG_CI_PROFILE"])))')
[[ ${#ruby_profiles[@]} -gt 0 ]] || { echo 'No compatible Ruby profiles selected' >&2; exit 1; }
profiles+=("${ruby_profiles[@]}")
# DefaultInfo for each profile carries its manifest and validator runfiles.
# Fetch the complete tree: the coverage gate hashes every scenario bytecode.
bazel build "${build_args[@]}" --remote_download_outputs=toplevel \
  "--remote_download_regex=$downloaded_evidence_regex" \
  "${image_flags[@]}" //tools/datadog_coverage:datadog_coverage "${profiles[@]}"
mapfile -t scenario_targets < <(python3 tools/buildbuddy_ci.py targets --suite //fixtures:datadog_suite)
mapfile -t parallel_targets < <(python3 tools/buildbuddy_ci.py targets --suite //fixtures:datadog_parallel_suite)
[[ ${#scenario_targets[@]} -gt 0 ]] || { echo 'No scenario targets selected' >&2; exit 1; }
[[ ${#parallel_targets[@]} -gt 0 ]] || { echo 'No parallel targets selected' >&2; exit 1; }
bazel build "${build_args[@]}" "${test_download_args[@]}" "${image_flags[@]}" "${scenario_targets[@]}" "${parallel_targets[@]}"

for execution in 1 2; do
  suite_test_status=0
  bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
    --nocache_test_results \
    --test_env="TELEMETRY_TEST_REVISION=$revision" \
    "${image_flags[@]}" \
    "${scenario_targets[@]}" || suite_test_status=$?
  if (( suite_test_status != 0 )); then
    # Retain native captures even when the initial workload suite fails before
    # producing a gated execution receipt. These are diagnostics, not proof.
    failed_evidence="$evidence/execution-$execution-failed"
    mkdir -p "$failed_evidence"
    find -L bazel-testlogs/fixtures -path '*datadog*hurl_test*/test.outputs/*' -type f -exec cp -L --no-preserve=mode --parents -t "$failed_evidence/" '{}' +
    find -L bazel-testlogs/fixtures -path '*datadog*hurl_test*/test.log' -type f -exec cp -L --no-preserve=mode --parents -t "$failed_evidence/" '{}' +
    exit "$suite_test_status"
  fi
  tools/retain_datadog_evidence.py \
    --revision "$revision" \
    --ci-profile "$ci_profile" \
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
  "${parallel_targets[@]}" \
  --test_arg=--scenario-concurrency=4 \
  --test_arg=--scenario-repetitions=1
mkdir -p "$evidence/parallel"
for target in "${parallel_targets[@]}"; do
  directory="bazel-testlogs/fixtures/${target##*:}/test.outputs"
  find -L "$directory" -name 'stress.*.json' -type f -exec cp -L --no-preserve=mode --parents -t "$evidence/parallel/" '{}' +
done

fi

if [[ "$stage" == all || "$stage" == features ]]; then
# This suite includes manual Rails and Gin feature probes, whose individual
# tests are intentionally absent from the wildcard full-suite expansion.
# Bound native app startup to the same fleet concurrency as the SDK lab.
mapfile -t feature_targets < <(python3 tools/buildbuddy_ci.py targets --suite //fixtures:datadog_external_features_suite --shard "${DATADOG_PARITY_SHARD:-0/1}")
[[ ${#feature_targets[@]} -gt 0 ]] || { echo 'No native feature targets selected' >&2; exit 1; }
bazel build "${build_args[@]}" "${test_download_args[@]}" "${image_flags[@]}" "${feature_targets[@]}"
shared_test_status=0
bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --jobs="$jobs" --nocache_test_results \
  "${image_flags[@]}" \
  "${feature_targets[@]}" || shared_test_status=$?
retain_test_outputs "$evidence/features" "${feature_targets[@]}"
if (( shared_test_status != 0 )); then
  exit "$shared_test_status"
fi

fi

if [[ "$stage" == all || "$stage" == gate ]]; then
# Shared assertions require independent evidence from all three SDK languages.
# Keep the entire 301-feature denominator and publish every missing cell.
shared_evidence=()
while IFS= read -r -d '' receipt; do
  shared_evidence+=(--evidence "$receipt")
done < <(find "$evidence/features" -name 'datadog-shared-results.json' -type f -print0)
if [[ ${#shared_evidence[@]} -eq 0 ]]; then
  echo 'No retained shared Datadog capability receipts' >&2
  exit 1
fi
shared_report=(
  report --inventory docs/datadog-capabilities-inventory.json
  --mapping docs/datadog-shared-capabilities-mapping.json --local-root "$PWD"
  --gap-issues docs/datadog-coverage-gaps.json
  --require-language python --require-language ruby --require-language go
  --require-independent-cases
  "${shared_evidence[@]}"
)
python3 tools/datadog_capabilities.py "${shared_report[@]}" \
  --output "$evidence/datadog-shared-capabilities-report.json" \
  --markdown-output "$evidence/datadog-shared-capabilities-report.md" \
  --require-all-implemented \
  --require-percent "${DATADOG_SHARED_CAPABILITY_MIN_PERCENT:-0}"

fi

if [[ "$stage" == all || "$stage" == shared-sdk ]]; then
# The native SDK lab runs identical independent cases in Python and Go. Pinned
# upstream manifest exclusions are executed xfails, never capability passes.
mapfile -t sdk_targets < <(python3 tools/buildbuddy_ci.py targets --suite //fixtures:datadog_shared_sdk_suite --shard "${DATADOG_PARITY_SHARD:-0/1}")
[[ ${#sdk_targets[@]} -gt 0 ]] || { echo 'No shared SDK targets selected' >&2; exit 1; }
bazel build "${build_args[@]}" "${test_download_args[@]}" "${image_flags[@]}" "${sdk_targets[@]}"
shared_sdk_status=0
bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --jobs="$jobs" --nocache_test_results "${image_flags[@]}" \
  "${sdk_targets[@]}" || shared_sdk_status=$?
retain_test_outputs "$evidence/shared-sdk" "${sdk_targets[@]}"
if (( shared_sdk_status != 0 )); then exit "$shared_sdk_status"; fi
fi

if [[ "$stage" == all || "$stage" == gate ]]; then
sdk_matrix_gate=--require-complete-matrix
if [[ "$ci_profile" == pr ]]; then sdk_matrix_gate=--require-selected-matrix; fi
python3 tools/datadog_shared_sdk_report.py \
  --evidence-dir "$evidence/shared-sdk" --evidence-dir "$evidence/features" \
  --ci-profile "$ci_profile" \
  --output "$evidence/datadog-shared-sdk-report.json" \
  --markdown-output "$evidence/datadog-shared-sdk-report.md" "$sdk_matrix_gate"

fi

if [[ "$stage" == all || "$stage" == capabilities ]]; then
# Capability suites reuse existing Python frameworks and include the real Agent
# and local backend. Retain every raw capture beside its receipt before gating.
capability_test_status=0
mapfile -t capability_targets < <(python3 tools/buildbuddy_ci.py targets --suite //fixtures:datadog_capability_suite)
[[ ${#capability_targets[@]} -gt 0 ]] || { echo 'No capability targets selected' >&2; exit 1; }
bazel build "${build_args[@]}" "${test_download_args[@]}" "${image_flags[@]}" "${capability_targets[@]}"
# Native SDK/Agent fixtures share the executor fleet. Respect the stage
# worker budget, including their application startup.
bazel test "${bazel_args[@]}" "${test_download_args[@]}" \
  --jobs="$jobs" \
  --nocache_test_results \
  "${image_flags[@]}" \
  //fixtures:datadog_capability_suite || capability_test_status=$?
mkdir -p "$evidence/capabilities"
for family in lab upstream_lab agent security signals telemetry profiling llmobs openai otlp ffe remote_config debugger sdk_extra messaging anthropic genai graphql datasets dsm dbm otel_mysql; do
  for directory in bazel-testlogs/fixtures/datadog_"$family"*_test/test.outputs; do
    if [[ -d "$directory" ]]; then
      find -L "$directory" -type f -exec cp -L --no-preserve=mode --parents -t "$evidence/capabilities/" '{}' +
    fi
  done
done
if (( capability_test_status != 0 )); then
  exit "$capability_test_status"
fi
fi

if [[ "$stage" == all || "$stage" == gate ]]; then
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
  --output "$evidence/datadog-capabilities-report.json" \
  --markdown-output "$evidence/datadog-capabilities-report.md" \
  --require-percent "${DATADOG_CAPABILITY_MIN_PERCENT:-75}"
fi
