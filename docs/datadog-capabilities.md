# Measuring upstream capability coverage

The comparison is pinned to [DataDog/system-tests at 098fe0967c587db8a16b74a1e711777d0a9d5867](https://github.com/DataDog/system-tests/tree/098fe0967c587db8a16b74a1e711777d0a9d5867). The checked-in [inventory](datadog-capabilities-inventory.json) records its feature definitions, source-file SHA-256 hashes, test-method references, and feature annotations inherited through test classes. It reads Python ASTs without importing upstream modules. Test-method counts are static source references, not pytest execution counts: parameter combinations, scenarios, language availability, and activation manifests are separate dimensions.

There are **302 referenced decorator names**, of which `not_reported` is a reporting control rather than a capability. The capability denominator is therefore **301 named features**. The **67-feature parametric subset** is retained as a separate selectable scope. A named API Security capability marked `NOT_REPORTED_ID` stays in the denominator. Names identify capabilities because upstream numeric IDs collide: `log_injection` and `structured_log_injection` share 5; `datastreams_monitoring_support_for_manual_checkpoints` and `ssi_service_tracking` share 327. The tool never combines them.

The [mapping](datadog-capabilities-mapping.json) ties each local capability assertion to its source symbol, Bazel target, required result names, and explicit assertion scope. `implemented` means those executable assertions exist; it does not establish runtime success. `partial` means the current assertions or evidence integration do not justify the complete local claim. Unmapped features are `missing`. The [generated report](datadog-capabilities-report.md) shows all 301 capabilities, including gaps and the hash of each supplied runtime receipt. Original-method labs run on both native wire versions; mappings select the versions supported by the actual assertions. Adding languages or frameworks does not improve this percentage by itself.

Capability coverage counts specific behaviors exercised by assertions. Full upstream test coverage also requires each original test method, parameter combination, scenario, activation setting, and relevant implementation to run and pass. This tool does not claim full upstream case parity, and its JSON output explicitly records `fullUpstreamCaseParity: false`.

The capability suite combines original-method and supplemental Python SDK labs, real Agent/backend assertions, Django AppSec/IAST/RASP/API Security profiles, logging and runtime metrics, telemetry and profiling, LLMObs/OpenAI/Anthropic/Google GenAI, GraphQL error reporting, DSM/AWS/AMQP messaging, native and OpenTelemetry MySQL, AI Guard, IPv6, stable configuration, SCA reachability, OTLP signals, feature flags, signed remote configuration, debugger symbols/replay/flare and native crash reports. Each mapping states its exercised subset and retained gaps. The runtime gate uses fresh receipts from these families together.

To regenerate the pinned inventory from a clean upstream checkout:

```sh
python3 tools/datadog_capabilities.py inventory \
  --upstream /tmp/datadog-system-tests-parity \
  --output docs/datadog-capabilities-inventory.json
```

To inspect source mappings and generate the broad report:

```sh
python3 tools/datadog_capabilities.py report \
  --inventory docs/datadog-capabilities-inventory.json \
  --mapping docs/datadog-capabilities-mapping.json \
  --local-root . --format markdown \
  --output docs/datadog-capabilities-report.md
```

The runtime gate requires retained result files. Each result supplies `name`, canonical `capabilityNames`, `capabilityInventoryRevision`, `status`, effective `configuration`, `captureSha256`, and a `captureFile` relative to that result file's directory. Existing `NAME.capture.json` captures can omit `captureFile`. Agent/tracer results additionally provide `artifacts: [{"file": "backend-capture.json", "sha256": "..."}]` for backend, intake-proxy, identity and Agent information evidence. Every listed artifact must exist and match its hash; missing or changed evidence invalidates the result. The gate hashes the retained capture bytes and requires every named case in the mapping to pass. Required cases may explicitly select a native wire version using `{"name": "CASE", "wire": "v0.5"}`; a plain case name requires every supplied occurrence across wires to pass. Wire-scoped mappings prove only their stated wire coverage. Failed or unsupported outcomes for a required case from another supplied profile cannot be erased by a passing duplicate. Other upstream methods outside the documented local assertion scope remain gaps and do not become passing cases. Missing captures, missing cases, partial mappings, and absent evidence do not count. Original method source revisions remain separate from the capability-inventory revision.

```sh
python3 tools/datadog_capabilities.py report \
  --inventory docs/datadog-capabilities-inventory.json \
  --mapping docs/datadog-capabilities-mapping.json \
  --local-root . --scope all \
  --evidence PATH/TO/datadog-upstream-results.json \
  --require-percent 75
```

Repeat `--evidence` to combine suites or independently retained runs. Use `--scope parametric` only when explicitly measuring that subset; the default denominator remains all capabilities. A report generated without evidence lists implemented assertions and reports **0 runtime verified**, so it cannot pass a 75% gate.

## Shared Ruby, Python, and Go assertions

`//fixtures:datadog_external_features_suite` runs the same HTTP workloads and
native span assertions against all seven pinned profiles: aiohttp and Django on
both wire versions, Rails and Falcon on v0.4, and Gin on v0.4. Fixture startup
and the Go spelling of the B3 configuration are adapted separately; assertion
predicates, case names, and required cases stay identical across languages.

The [shared mapping](datadog-shared-capabilities-mapping.json) records the
specific common assertion scopes: 19 capabilities exercised by 53 common cases
across seven SDK/framework/wire profiles. The remaining 282 capabilities need
shared assertions and adapters before the full-inventory gate can pass.
Following [rules_stests PR #57](https://github.com/pawelchcki/rules_stests/pull/57),
each contract/profile combination runs as its own Bazel service test: 371
independent targets. Each owns its baseline/control, two fresh SDK executions,
and a receipt containing exactly one case. A failed case cannot prevent the
other targets from producing their evidence.
The suite retains
`datadog-shared-results.json` alongside the existing `datadog-features.json`.
Each new receipt carries explicit `language`, `application`, `wire`, canonical
capability names, and the pinned inventory revision. Its capture, baseline,
partial-flush captures, and any intake rejection log are bound by SHA-256 and
rechecked from retained files. Failed cases retain their language and case
identity even when their capability claims are empty.

Following [rules_stests PR #56](https://github.com/pawelchcki/rules_stests/pull/56),
each case runs twice in fresh application/SDK processes. Both executions must
pass the same validators and produce the same normalized behavioral response.
Generated IDs, timing, ports, and process/runtime identities vary between
processes; validity and parentage are checked on each raw capture. Both raw
captures, responses, and partial-flush captures are retained and hash-bound.
Language coverage requires `repetitions: 2` and a verified separate repeated
capture and a hash-verified distinct baseline/control artifact. Omitting the
control declaration cannot leave a passing shared claim. The exact Go 2.10.1 manual-drop-under-keep-rule defect remains
unsupported with no passing claim; a different failure or unexpected pass
fails the suite and requires review of [issue #13](https://github.com/pawelchcki/rules_datadog_stests/issues/13).
The historical Python duplicate-origin waiver was removed after the upgraded
SDK passed that case on both wire versions.

Require independent evidence for each language with repeated
`--require-language` options:

```sh
evidence_args=()
while IFS= read -r -d '' receipt; do
  evidence_args+=(--evidence "$receipt")
done < <(find PATH/TO/features -name datadog-shared-results.json -print0)
python3 tools/datadog_capabilities.py report \
  --inventory docs/datadog-capabilities-inventory.json \
  --mapping docs/datadog-shared-capabilities-mapping.json --local-root . \
  --gap-issues docs/datadog-coverage-gaps.json \
  --require-language ruby --require-language python --require-language go \
  --require-independent-cases \
  "${evidence_args[@]}" \
  --require-all-implemented --format markdown
```

The combined verified count is the intersection of the three language results.
A Python pass cannot replace missing Ruby or Go evidence. Unlabelled legacy
receipts contribute no language proof. Every supplied occurrence of a required
case within a language must pass, including different frameworks and wire
versions. Missing languages, unsupported results, failed duplicates, and
tampered baselines prevent verification.

The input directory must contain every independent receipt from the complete
371-case suite. `--require-independent-cases`
rejects grouped or empty receipts, including stale pre-isolation outputs.

The parity driver retains JSON and Markdown matrices and requires every
implemented shared mapping to pass across all three languages. The existing
75% broad capability gate remains independent. All 301 features remain visible
in both reports, including features outside the shared suite's scope.

For the requested full-inventory target, add `--require-percent 100`, or set
`DATADOG_SHARED_CAPABILITY_MIN_PERCENT=100` when running the parity driver.
This gate fails until every inventory feature has a mapped executable assertion
and passing evidence in every required language. The shared native tracing
suite does not supply assertions for every AppSec, OTLP, injection, database,
or Kubernetes capability. Existing Python-only labs cannot establish Ruby or
Go support for those features. The capability inventory also does not establish
SDK support for all of its named features in all three languages; exclusions
never reduce the coverage denominator.

The [gap tracker](datadog-coverage-gaps.json) links every missing feature to an
issue and is checked by the shared report gate. Issues
[#4](https://github.com/pawelchcki/rules_datadog_stests/issues/4) through
[#11](https://github.com/pawelchcki/rules_datadog_stests/issues/11) cover the 73
missing broad-suite capabilities. [Issue #12](https://github.com/pawelchcki/rules_datadog_stests/issues/12)
tracks the 209 existing Python capability checks still needing shared Ruby/Go
adapters. Missing adapters are not labelled as SDK unsupported. The separate
[Runnerless request](https://github.com/pawelchcki/my-infra/issues/114) asks for
periodic dependency-update PRs similar to Renovate.
