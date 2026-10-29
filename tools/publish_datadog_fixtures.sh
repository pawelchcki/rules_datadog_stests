#!/usr/bin/env bash
# Existing host container authentication remains outside the repository.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$root/tools/datadog_fixture_publication.py" "$@"
