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

The shared external-feature suite runs each contract as an independent Bazel
service test and applies identical native assertions to Ruby,
Python, and Go. Its retained language matrix requires independent passing
evidence in each SDK and includes all 301 upstream capabilities, with missing
and unsupported coverage kept explicit. See
[shared capability measurement](docs/datadog-capabilities.md#shared-ruby-python-and-go-assertions)
for the full-inventory 100% gate and the current scope.

The seven reviewed RealWorld profiles and their Scheme sources were moved
without changing their contents. Datadog configuration and profile macros are
exported from `//rules:defs.bzl`. Shared service rules are loaded from
`@rules_stests//rules:defs.bzl`.

`MODULE.bazel` pins the shared infrastructure from GitHub, including its public
process runtime, launcher library, shared Gin sources and OCI materializer.
The materializer preserves empty image directories across Bazel cache restores,
so Rails starts against the same read-only application rootfs on warm CI runs.
For coordinated local development, pass
`--override_module=rules_stests=/absolute/path/to/rules_stests` to Bazel.

The shared Scheme contract error and application scenario libraries are imported
by external labels. Datadog assertion catalogs, native SDK checks, review records,
receipts and publication workflows stay here. OCI payload digests are pinned to reviewed payloads; the current SDK
upgrade includes a rebuilt and anonymously verified Ruby 2.43.0 payload. The Go fixture builder imports the pinned
Gin sources from
`@rules_stests//fixtures/apps/go/realworld-gin:sources` and overlays only the
Datadog tracer graph and image recipe. The fixture builder records the complete
composed Gin context as a canonical Git tree in `gin.source-tree`, including
shared files, executable modes and symlink targets. Publication requires this
metadata and uses it for image tags and the lock source tree. Rebuilding the
unchanged historical context retains its reviewed tree identity. Falcon also uses the shared
`@rules_stests//harness:falcon_rootfs`. Common application sources, launchers,
runtimes, sink engine, and validator compiler remain in `rules_stests`.

See [corpus documentation](corpus/datadog/README.md),
[coverage](docs/datadog-coverage.md) and
[verification](docs/datadog-verification.md).

The capability suite compares against a pinned inventory of **301 named
DataDog/system-tests features**. It expands the existing Python labs with
original upstream assertions, AppSec/IAST/RASP, profiling, telemetry, logs,
runtime metrics, OpenTelemetry signals, LLM observability, feature flags, and
signed Remote Config, debugger probes/replay/symbols, native crash reports, AI Guard,
SCA reachability, AWS/AMQP messaging, GraphQL, MySQL, Anthropic and Google GenAI. The digest-pinned
Datadog Agent runs against a local
backend that decodes its native trace, statistics, metric, and event payloads.
The proxy retains both tracer intake and backend requests for assertions across
the two boundaries. No additional language or web framework is required.

The [capability report](docs/datadog-capabilities-report.md) records verified
coverage (**228/301, 75.7%**) and remaining gaps. The [capability gate](docs/datadog-capabilities.md)
counts only passing assertions with intact capture hashes; it measures feature
capabilities separately from full upstream test-case parity.

Run `tools/run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY`
to produce two fresh gated executions and the Datadog report. The image directory
contains `bazel.flags`, which can be empty when using published fixtures.
The driver also retains capability captures and requires at least 75% of the
full feature inventory; `DATADOG_CAPABILITY_MIN_PERCENT` can override that threshold.

Same-repository pull requests run the full assertion suite, external consumer
build and fresh gated evidence in BuildBuddy. Fork pull requests run these in
GitHub Actions; BuildBuddy only reports the delegation status. Pushes to main
run both systems. Actions use the BuildBuddy cache through the
`BUILDBUDDY_API_KEY` repository secret, with remote uploads disabled. Forks
receive no secret and use the GitHub disk and repository caches. Fresh native
tests still execute with `--nocache_test_results` on both systems.
Local execution eagerly materializes cached
outputs so OCI directory symlink aliases remain intact; remote BuildBuddy
execution retains minimal downloads with explicit evidence trees. A separate
`source-checks` job validates GitHub Actions workflows and tracked Python, shell and JSON syntax. Run
`python3 tools/check_sources.py` and
`go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12` locally for those checks.
The runnerless CI tool kit GitHub App provides `codex/review-gate` without a
GitHub Actions runner or repository-stored Codex credentials.

Runnerless report publication is declared in [.ci-toolkit.yml](.ci-toolkit.yml).
After this configuration reaches the trusted default branch, successful
BuildBuddy `Full test suite` runs publish a commit-addressed HTML proof page
to the App's R2-backed artifact service. The source pins the status producer,
invocation host and exact head, and declares only `datadog-report.html`.
The `untrusted-html` security profile isolates report HTML. Failed or incomplete workflows do not
publish. Reports remain available for 90 days, with five default-branch sets
retained; PR comments link reports after publication. Complete evidence stays
in existing GitHub Actions and BuildBuddy CI artifacts. GitHub Pages also hosts
the report downloaded from the successful main-branch Actions build; publication
does not rerun the test suite. Manual publication selects a successful main-push
`Datadog assertions` run by ID.
