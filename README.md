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

The [Go runtime capability matrix](docs/go-runtime-capabilities.md) covers every
minor from Go 1.4 through 1.27 on Linux amd64, with configurable arm64. It retains
Orchestrion and Alibaba automatic tracing and exposes replacement instrumentation
through startup `LD_PRELOAD` or a controller attached to the running app's PID.

The Ruby runtime matrix adds Sinatra on WEBrick for Ruby **2.5, 2.6, 2.7,
3.0, 3.1, 3.2, 3.3, 3.4, and 4.0**, using the interpreter and application
bundles from the pinned `rules_stests` matrix. Each runtime runs all 16
RealWorld scenarios, the same 53 independent native SDK contracts, and the
parallel isolation workload with Datadog 2.43.0 on v0.4. Together with the
existing frameworks, this registers 16 profiles, 256 RealWorld scenario
combinations, and 848 independent SDK contract tests.

```sh
bazel test --config=local //fixtures:datadog_ruby_matrix_suite
bazel test --config=local //fixtures:datadog_ruby_external_features_suite
bazel test --config=local //fixtures:datadog_ruby_parallel_suite
bazel build --config=local //harness:datadog_ruby_compatibility
```

For retained Ruby-only evidence, run the matrix with
`--nocache_test_results --test_env=TELEMETRY_TEST_REVISION=$(git rev-parse HEAD)`,
then build `//harness:datadog_ruby_compatibility` and
`//tools/datadog_coverage:datadog_coverage`. Pass `--ruby-matrix-only` to
`tools/retain_datadog_evidence.py` with the revision, a new output directory,
and the built gate executable.

The compatibility manifest records every upstream runtime. Ruby 1.9.3 and
2.0–2.4 are explicitly incompatible with Datadog 2.43.0's declared Ruby
requirement (`>= 2.5.0, < 4.1`); they do not count as passing Datadog tests.
The versioned payloads use the Ruby tracing transport and compile native
MessagePack against each runtime's headers, without linking the executor's
libc. Optional profiling, crashtracking, and AppSec extensions are outside
this matrix. The build checks actual tracer loading, Ruby requirements,
ABI identity, native MessagePack serialization, and the opt-in probes.
Dependencies and source/native gem archives are locked by SHA-256.

The nine added profiles validate native telemetry contracts and retain their
captures and receipts. They have no reviewed exact shapes yet. Retention
explicitly authorizes those profile IDs as contract evidence; the gate still
requires complete proofs, intact captures and validator bytecode, matching
revision and identities, and classified fields. Existing profiles continue
to require their reviewed shapes. Reports label each scenario's validation
mode so the two evidence scopes remain visible.

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

Pull requests run a deterministic representative matrix, external consumer
build and fresh gated evidence in BuildBuddy. Fork pull requests run these in
GitHub Actions; BuildBuddy only reports the delegation status. Pushes to main
run both systems. Actions use the BuildBuddy cache through the
`BUILDBUDDY_API_KEY` repository secret, with remote uploads disabled. Forks
receive no secret and use the GitHub disk and repository caches. Fresh native
tests still execute with `--nocache_test_results` on both systems.
The required BuildBuddy `Full test suite` starts **five stages on PRs**:
scenarios and stress checks across every framework and both intake formats,
including Ruby **2.5, 3.3 and 4.0**; two disjoint shards covering all native framework feature cases;
the first and last pinned SDK case in each upstream or adapter class in both
Go and Python (**97 cases per language**, covering all 51 classes); and all
60 capability checks. Other versioned Ruby feature probes and SDK parameters
run on main. The native Go workflow builds/tests **four of 24 versions** on
PRs: oldest control 1.4, downstream legacy control 1.17, minimum instrumented
version 1.25, and latest version 1.27, with all three backends.
This selects **967 fresh test executions instead of 2,150** in the evidence
driver (55% fewer), and **12 instead of 72** Go runtime/backend combinations.

Pushes to **main run all variants**: twelve BuildBuddy stages with all nine
Ruby runtimes, eight feature shards, two complete SDK shards, and capabilities;
the Go workflow covers all 24 versions. Manual/local runs default to full.
Set `DATADOG_CI_PROFILE=pr` to reproduce the representative selection locally.
Reports identify representative PR coverage, keep missing cells unverified,
and never claim the full SDK parameter matrix passed. Every selected scenario
still runs twice, and capture/receipt validation, the shared-language gate,
the 301-feature denominator and the 75% capability floor remain unchanged.
All test actions use the configured self-hosted RBE platform. The parent requires
every selected stage to pass, verifies archive checksums and exact-head,
profile-bound target manifests, and gates the combined captures. Main requires
the complete SDK matrix; a PR manifest cannot satisfy that gate.
Native feature and shared SDK tests reserve two CPU cores per test action,
with four cores for Rails boot. Scenario and stress tests carry the same CPU
reservations. Shared SDK shards run sixteen workers each; feature and capability
shards run four, and the scenario stage and each PR feature shard run eight.
Set `DATADOG_PARITY_JOBS` or `DATADOG_PARITY_BUILD_JOBS` to override the stage
budgets for available fleet capacity. Compilation uses a separate 32-action budget before fresh tests start,
so cold builds do not inherit the smaller test budget. Stage archives retain
elapsed times and worker budgets in `ci-timings/` for comparisons on the same
executor fleet. The parent polls and downloads up to four stages concurrently,
then merges evidence in a fixed order and rejects overlaps.
The parent reserves the final ten minutes of BuildBuddy's one-hour runner
budget for aggregation and strict reports. PR features use two concurrent
shards so each runner has half the inventory while every selected case still runs.
The preliminary wildcard pass excludes the five suites that the evidence driver
runs freshly afterward, so native tests execute once per required evidence run.
The pinned `rules_stests` sink has a compatibility patch recognizing the nine
versioned Ruby application names as Rack workloads. Its runtime also has a
small compatibility patch recognizing
Django's proven startup bind-conflict message within its existing three-attempt
port-allocation budget. Unknown startup failures remain immediate failures.
Local execution eagerly materializes cached
outputs so OCI directory symlink aliases remain intact; remote BuildBuddy
execution retains minimal downloads with explicit evidence trees and creates
local runfiles trees only when a local action needs them. Archives normalize
paths and metadata and store identical files as hardlinks, preserving every
capture byte and evidence path. Ruby matrix payloads retain the verified gem
specifications and glibc runtime libraries; native build sources, headers,
documentation and musl libraries stay out of the deployed payloads.
Evidence collection batches file copies to avoid starting a process for every
capture or test; the complete archived proof remains byte-identical. JSON and
Markdown reports share one evidence-validation pass and retain both formats
before enforcing the existing gates.

Run `tools/check_determinism.sh` to build every target, including manual
fixtures, twice with BuildBuddy's uncached determinism diagnostic. Set
`DATADOG_BAZEL_CONFIG=buildbuddy` to use the configured RBE platform. The check
compares build outputs; fresh runtime captures and receipts are checked by the
existing evidence gates. Go and local OCI input materialization use declared,
pinned tools and support sandboxed and remote execution.
Provenance regressions use a pinned, development-only Git dependency and
isolated configuration. The Rust bootstrap wrapper also expands remapping
placeholders inside compiler response files, preventing executor paths in
library metadata.

Measured locally on the same 339-test shared SDK shard, fresh test execution
fell from 593.0 seconds with two workers to 280.3 seconds with four (52.7%).
The Ruby 3.3 runtime payload fell from 39,108,725 to 18,583,175 bytes (52.5%);
all nine supported Ruby payloads pass their native tracing bootstrap checks.
Archiving the same retained evidence fell from 7.55 to 3.43 seconds, with the
compressed archive shrinking from 4,809,811 to 4,748,182 bytes. These component
measurements do not establish a 50% reduction for the complete distributed CI
run; retained per-stage timings make that comparison possible on the RBE fleet.

On the loaded RBE fleet, a fixed sample of 66 fresh Go/Python SDK tests passed
with both four and sixteen workers. Bazel's server-side elapsed time fell from
355.8 to 133.7 seconds (62.4%); client lock waiting is excluded. Batched retention
of 678 tests fell from 17.74 to 0.72 seconds, preserving all 9,496 file paths and
hashes. The SDK report's median time fell from 8.91 to 2.73 seconds across three
comparisons, with byte-identical JSON and Markdown and the complete-matrix gate
passing. These are component measurements; see [recorded results](docs/runtime-performance.json)
for the RBE invocations and measurement scope.

A separate `source-checks` job validates GitHub Actions workflows and tracked Python, shell and JSON syntax. Run
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
