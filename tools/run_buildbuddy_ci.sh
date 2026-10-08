#!/usr/bin/env bash
# Keep the required BuildBuddy status while executing short RBE child workflows.
set -euo pipefail

if [[ "$(git config --get extensions.partialClone || true)" == fork ]]; then
  echo 'Fork PR: full tests and fresh evidence run in GitHub Actions.'
  exit 0
fi
revision="$(git rev-parse HEAD)"
if [[ "$(git show -s --format=%ce HEAD)" == ci-runner@buildbuddy.io ]]; then
  revision="$(git rev-parse HEAD^1)"
fi
git checkout --detach "$revision"
images="$(mktemp -d "${TMPDIR:-/tmp}/datadog-images.XXXXXX")"
evidence="$(mktemp -d "${TMPDIR:-/tmp}/datadog-evidence.XXXXXX")"
: > "$images/bazel.flags"
export DATADOG_PARITY_STAGE=gate
if [[ "$1" == stage ]]; then
  case "$2" in
    'Datadog scenarios'|'Datadog PR scenarios') export DATADOG_PARITY_STAGE=scenarios ;;
    'Datadog features '*|'Datadog PR features '*) export DATADOG_PARITY_STAGE=features ;;
    'Datadog shared SDK '*|'Datadog PR shared-sdk') export DATADOG_PARITY_STAGE=shared-sdk ;;
    'Datadog capabilities'|'Datadog PR capabilities') export DATADOG_PARITY_STAGE=capabilities ;;
    *) echo "Unknown CI stage: $2" >&2; exit 1 ;;
  esac
fi
archive_evidence() {
  status=$?
  trap - EXIT
  set +e
  tools/run_datadog_parity.sh --archive "$images" "$evidence"
  archive_status=$?
  if (( status == 0 && archive_status != 0 )); then exit "$archive_status"; fi
  exit "$status"
}
trap archive_evidence EXIT
if [[ "$1" == stage ]]; then
  python3 tools/buildbuddy_ci.py stage --name "$2" --revision "$revision" --images "$images" --evidence "$evidence"
else
  python3 tools/buildbuddy_ci.py aggregate --revision "$revision" --images "$images" --evidence "$evidence"
fi
