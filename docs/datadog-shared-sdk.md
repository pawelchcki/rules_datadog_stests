# Shared Python and Go SDK assertions

The native SDK lab runs 339 independent cases against Go 2.10.1 and Python 4.15.5. It covers 39 declared capability scopes with identical assertions, primarily from the pinned upstream parametric tests. Framework-neutral HTTP cases extend the existing integration checks using actual aiohttp and Go net/http instrumentation. The existing seven runtime/wire profiles continue to cover their framework-specific HTTP workloads.

This is scoped coverage, not full upstream feature parity. The report keeps all 301 inventory features visible, including existing Python implementations still awaiting shared adapters. It distinguishes executed passes, executed xfails, upstream manifest exclusions without a local workload, and missing adapters. The shared mappings describe exactly what each assertion proves.

Run all shared SDK cases, or Go alone:

```sh
bazel test //fixtures:datadog_shared_sdk_suite --jobs=4
bazel test //fixtures:datadog_shared_sdk_go_suite --jobs=4
```

`//fixtures:datadog_go_capability_suite` additionally includes the existing Gin integration checks. Each SDK case has its own `//fixtures:datadog_shared_sdk_<language>_<index>_test` target; its name and index come from [cases.json](../harness/shared_sdk/cases.json).

Every case starts a healthy baseline process, then runs the workload twice in fresh processes. Receipts retain actual SDK operations, original assertion source hashes, effective configuration, and native intake captures with SHA-256 hashes. A disabled-tracing workload must prove suppression and retain its healthy baseline. Expected failures must also export a separate control workload. Startup, transport, and missing adapter API errors remain failures.

The baggage byte-limit check adapts upstream D017 locally because its exact item-count assertion depends on map iteration order. The shared check requires a nonempty header within 8,192 bytes and complete, unchanged, unique members from the original input. It exercises all 24 insertion orders and also verifies lossless propagation below the limit. Receipts identify the local assertion source and `adaptedFrom` upstream selector; the original vendored test stays pinned. The old manifest exclusion does not mask failures of the corrected byte-limit or value-integrity assertions.

The frozen manifests resolve the pinned upstream declarations against the installed SDK versions. Framework-specific declarations are kept separate. Broad exclusions apply as they do upstream, but passing cases are recorded as XPASS. Python's existing evidence-bound SDK differences and one newly observed baggage-tag defect use narrow matchers. An xfail permits the test matrix to complete and remains unsupported in the capability report. See [the executed xfails](datadog-shared-sdk-xfails.md) for the observed cases and reasons.

Generate the full capability report after a run:

```sh
python3 tools/datadog_shared_sdk_report.py \
  --evidence-dir bazel-testlogs/fixtures --require-complete-matrix \
  --output /tmp/datadog-shared-sdk-report.json
python3 tools/datadog_shared_sdk_report.py \
  --evidence-dir bazel-testlogs/fixtures --format markdown \
  --output /tmp/datadog-shared-sdk-report.md
```

The complete-matrix check requires a valid baseline, both capture hashes, expected SDK version, source method, capability assignments, and an accepted outcome for every registered case in both languages. It accepts properly recorded xfails; it does not turn them into verified capabilities. The parity driver runs this gate and archives its receipts and reports.

Case regeneration uses PyYAML and the pinned upstream checkout:

```sh
python3 tools/update_shared_sdk_cases.py --help
```

The generated [SDK mapping](datadog-shared-sdk-capabilities-mapping.json), case registry, and resolved manifests are checked in; runtime tests do not require PyYAML. Go dependencies stay pinned to SDK 2.10.1 in [go.mod](../harness/shared_sdk/go/go.mod).
