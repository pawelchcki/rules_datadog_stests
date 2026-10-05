# Measuring upstream capability coverage

The comparison is pinned to [DataDog/system-tests at 098fe0967c587db8a16b74a1e711777d0a9d5867](https://github.com/DataDog/system-tests/tree/098fe0967c587db8a16b74a1e711777d0a9d5867). The checked-in [inventory](datadog-capabilities-inventory.json) records its feature definitions, source-file SHA-256 hashes, test-method references, and feature annotations inherited through test classes. It reads Python ASTs without importing upstream modules. Test-method counts are static source references, not pytest execution counts: parameter combinations, scenarios, language availability, and activation manifests are separate dimensions.

There are **302 referenced decorator names**, of which `not_reported` is a reporting control rather than a capability. The capability denominator is therefore **301 named features**. The **67-feature parametric subset** is retained as a separate selectable scope. A named API Security capability marked `NOT_REPORTED_ID` stays in the denominator. Names identify capabilities because upstream numeric IDs collide: `log_injection` and `structured_log_injection` share 5; `datastreams_monitoring_support_for_manual_checkpoints` and `ssi_service_tracking` share 327. The tool never combines them.

The [mapping](datadog-capabilities-mapping.json) ties each local capability assertion to its source symbol, Bazel target, required result names, and explicit assertion scope. `implemented` means those executable assertions exist; it does not establish runtime success. `partial` means the current assertions or evidence integration do not justify the complete local claim. Unmapped features are `missing`. The [generated report](datadog-capabilities-report.md) shows all 301 capabilities, including gaps and the hash of each supplied runtime receipt. Original-method labs run on both native wire versions; mappings select the versions supported by the actual assertions. Adding languages or frameworks does not improve this percentage by itself.

Capability coverage counts specific behaviors exercised by assertions. Full upstream test coverage also requires each original test method, parameter combination, scenario, activation setting, and relevant implementation to run and pass. This tool does not claim full upstream case parity, and its JSON output explicitly records `fullUpstreamCaseParity: false`.

The capability suite combines original-method and supplemental Python SDK labs, real Agent/backend assertions, Django AppSec/IAST/RASP/API Security profiles, logging and runtime metrics, telemetry and profiling, LLMObs/OpenAI, OTLP metrics/logs, feature flags, signed remote configuration and debugger probes. Each mapping states its exercised subset and retained gaps. The runtime gate uses fresh receipts from these families together.

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
  --evidence PATH/TO/datadog-upstream-lab-results.json \
  --require-percent 50
```

Repeat `--evidence` to combine suites or independently retained runs. Use `--scope parametric` only when explicitly measuring that subset; the default denominator remains all capabilities. A report generated without evidence lists implemented assertions and reports **0 runtime verified**, so it cannot pass a 50% gate.
