# rules_datadog_stests

Datadog tracing assertions, reviewed shapes, pinned tracer profiles, SDK
experiments and independently gated evidence. This repository depends on
`rules_stests` for process launchers, the telemetry sink, Scheme compilation
and Bazel service tests. There is no reverse dependency.

```sh
bazel test --config=local //corpus:datadog_conformance_test //harness:datadog_sink_test
bazel test --config=local //fixtures:datadog_suite
bazel test --config=local //fixtures:datadog_external_features_suite //fixtures:datadog_lab_suite
```

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
receipts and publication workflows stay here. OCI payload digests remain pinned
at their previously reviewed values. The Go fixture builder imports the pinned
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

Run `tools/run_datadog_parity.sh IMAGE_DIRECTORY REVISION EVIDENCE_DIRECTORY`
to produce two fresh gated executions and the Datadog report. The image directory
contains `bazel.flags`, which can be empty when using published fixtures.

Pull requests run the full assertion suite, an external consumer build and two
fresh gated parity executions. A separate `source-checks` job validates GitHub
Actions workflows and tracked Python, shell and JSON syntax. Run
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
the report produced by the existing main-branch publisher.
