#!/usr/bin/env bash
# The native runner executes this against its exact final candidate commit.
# Full fresh PR evidence remains independently required before human merge.
set -euo pipefail
unset DATADOG_REGISTRY_TOKEN GITHUB_TOKEN GH_TOKEN
bazel test --config=local \
    //corpus:datadog_conformance_test \
    //harness:datadog_sink_test \
    //fixtures:datadog_suite \
    //fixtures:datadog_shared_sdk_go_0_test \
    //fixtures:datadog_shared_sdk_python_0_test \
    //tools:runnerless_update_datadog_test
