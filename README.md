# rules_datadog_stests

Datadog tracing assertions, reviewed shapes, pinned tracer profiles, SDK
experiments and independently gated evidence. This repository depends on
`rules_stests` for process launchers, the telemetry sink, Scheme compilation
and Bazel service tests. There is no reverse dependency.

```sh
bazel test --config=local //corpus:datadog_conformance_test //harness:datadog_sink_test
bazel test --config=local //fixtures:datadog_suite
bazel test --config=local //fixtures:datadog_external_features_suite //fixtures:datadog_lab_suite
bazel test --config=local //fixtures:datadog_capability_suite
```

The seven reviewed RealWorld profiles and their Scheme sources were moved
without changing their contents. Datadog configuration and profile macros are
exported from `//rules:defs.bzl`. Shared service rules are loaded from
`@rules_stests//rules:defs.bzl`.

`MODULE.bazel` pins the shared infrastructure from GitHub. A small compatibility
patch exposes the existing process runtime and launcher sources in that pinned
revision; remove it when a post-split infrastructure revision is available.
A second patch carries native metadata fixes proposed in
[rules_stests PR #50](https://github.com/pawelchcki/rules_stests/pull/50).
For coordinated local development, pass
`--override_module=rules_stests=/absolute/path/to/rules_stests` to Bazel.

The shared Scheme contract error and application scenario libraries are imported
by external labels. Datadog assertion catalogs, native SDK checks, review records,
receipts and publication workflows stay here. OCI payload digests remain pinned
at their previously reviewed values. The Go fixture build context retains its
historical three-binary image recipe to preserve those payload identities.

See [corpus documentation](corpus/datadog/README.md),
[coverage](docs/datadog-coverage.md) and
[verification](docs/datadog-verification.md).

The capability suite compares against a pinned inventory of **301 named
DataDog/system-tests features**. It expands the existing Python labs with
original upstream assertions, AppSec/IAST/RASP, profiling, telemetry, logs,
runtime metrics, OpenTelemetry signals, LLM observability, feature flags, and
signed Remote Config, and remotely installed debugger probes. The digest-pinned
Datadog Agent runs against a local
backend that decodes its native trace, statistics, metric, and event payloads.
The proxy retains both tracer intake and backend requests for assertions across
the two boundaries. No additional language or web framework is required.

The [capability report](docs/datadog-capabilities-report.md) records verified
coverage (**154/301, 51.2%**) and remaining gaps. The [capability gate](docs/datadog-capabilities.md)
counts only passing assertions with intact capture hashes; it measures feature
capabilities separately from full upstream test-case parity.

Run `tools/run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY`
to produce two fresh gated executions and the Datadog report. The image directory
contains `bazel.flags`, which can be empty when using published fixtures.
The driver also retains capability captures and requires at least 50% of the
full feature inventory; `DATADOG_CAPABILITY_MIN_PERCENT` can raise that threshold.
