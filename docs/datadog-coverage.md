# Datadog tracing coverage

The original comparison reference is [DataDog/system-tests at ea8a5976064509df0a5232e314b22e7e90ca4d40](https://github.com/DataDog/system-tests/tree/ea8a5976064509df0a5232e314b22e7e90ca4d40/tests). The additional configuration and lab cases use [DataDog/system-tests at 255dc57d719c4d33a1c45a1b41cd517c5cae5878](https://github.com/DataDog/system-tests/tree/255dc57d719c4d33a1c45a1b41cd517c5cae5878/tests/parametric). Native feature validation combines the original upstream D001 and D002 Datadog-header test methods with local checks for the remaining behaviors. This does not establish complete upstream parity.

The [verification record](datadog-verification.md) records the current SDK upgrade, shared-language coverage, and 112-case verification separately from historical acceptance and benchmark results.

## Exact RealWorld profiles

| Application | Tracer | Intake | Scenarios |
| --- | --- | --- | ---: |
| aiohttp | Python 4.15.5 | v0.4 and v0.5 | 16 each |
| Django | Python 4.15.5 | v0.4 and v0.5 | 16 each |
| Rails | Ruby 2.43.0, Ruby ABI 3.3 | v0.4 | 16 |
| Sinatra on Falcon (async) | Ruby 2.43.0, Ruby ABI 3.3 | v0.4 | 16 |
| Gin | Go 2.10.1, Orchestrion 1.13.0 | v0.4 | 16 |

The 112 combinations retain exact native span shapes, including multiplicities, service identity, routes/resources, HTTP status/error classification, database operations and ancestry, exception metadata, and the reviewed field policy. Ruby Rack/controller/ActiveRecord, Rack/Sinatra/Sequel, and Go Gin/Gorm/database/sql layers remain distinct. The original candidate review is retained in [datadog-shape-review-historical.json](datadog-shape-review-historical.json). Its 64 entries, old Python profile names, and capture hashes belong to the pre-upgrade Python 4.14.0/Ruby 2.42.0/Gin review; they are not current capture receipts. The current Python shapes omit the upgraded SDK’s absent raw `traceparent` tag while retaining native trace identity and parentage checks. Two fresh 112-case executions verify the active profiles; their revision-bound receipts and captures are linked in the verification record. Candidate generation does not produce a passing receipt.

The checked-in shapes under `corpus/datadog/realworld/shape/<profile>/<scenario>.scm` are written with the builders in `corpus/datadog/shape/`: one library per application's integrations, and `tracers.scm` for what each tracer adds on its own. They evaluate to the canonical datum the sink reports, and `corpus/datadog/trace-shape/match.scm` compares the two exactly. It ignores only the order of traces, siblings, tags, metrics, and native field names, and it reports the first difference with its span path. The readable form replaced the compact snapshot of the 96 files reviewed at `d6d6b5a86d8d47c52916e3ec5feab42df6a13414`; the 16 Falcon shapes were reviewed from their first candidates in the same form. Each rendered file was checked in the sink's Scheme VM to evaluate to exactly its reviewed datum. `corpus/datadog/README.md` explains how to read them.

## The async Ruby application

The shared `@rules_stests//harness:falcon_rootfs` application implements the RealWorld API with Sinatra and Sequel on Falcon. Four threads each run an Async reactor over one bound socket; every request runs in its own fiber, and Sequel checks out connections per fiber. The application binds every SQL value, so Sequel span resources keep their placeholders. dd-trace-rb records three spans per request (`rack.request`, `sinatra.request`, `sinatra.route`). It puts a before filter's queries under `sinatra.request` and records the application's rejection exceptions on the route span. The parallel suite exercises cross-thread and cross-fiber context isolation with overlapping requests. The native feature suite runs the same 53 checks as the other six profiles, including the five configuration cases below.

## Contract features

Every profile claims each feature its tracer satisfies. It claims the propagation features in the propagation scenarios and all other features in every scenario:

- **Intake:**
  - library identity headers (Datadog-Meta-Lang, -Lang-Interpreter, -Lang-Version, -Tracer-Version)
  - trace counts
  - chunk coherence
- **Trace structure:** 128-bit trace ids (`_dd.p.tid` format and agreement, timestamped generated ids).
- **Service identity:**
  - `_dd.base_service`
  - unified service tags
  - process identity (`language`, `runtime-id`, `process_id`)
- **Sampling:**
  - trace-root priorities, with rates only on roots
  - the `_dd.p.dm` decision maker
  - the configured rule keeping every locally started trace
- **HTTP server spans:**
  - `span.kind`, component, method, status, route, URL, and user agent
  - the route template matching the request path
- **Errors:** every error span is explained.

All seven profiles claim W3C and Datadog propagation, including keeping the caller's sampling priority. Each check follows the corresponding [system-tests](https://github.com/DataDog/system-tests/tree/ea8a5976064509df0a5232e314b22e7e90ca4d40/tests) assertion where one exists; the predicate names it. Four features are not claimed everywhere because the pinned tracers differ from the upstream expectation. The profiles record why:

- `version` appears on SQLAlchemy, Active Record, and Sequel spans reported under `sqlite`.
- Rack's `http.url` is a path (upstream bug APMAPI-922), for Rails and Falcon alike.
- GORM operation spans carry no `span.kind`.
- `db.system` is missing from some database spans:
  - aiohttp and Rails name the database in other tags.
  - Gin's GORM operation spans lack it, though its database/sql spans carry `db.system`.

Run `//fixtures:datadog_suite`. BuildBuddy's Full test suite runs the parity checks on the remote executor fleet, retaining and gating each of two uncached independent executions before running the next. `tools/retain_datadog_evidence.py` copies each manifest, compiled validator, receipt, capture, timing artifact, and test log. The gate requires complete scenario/profile coverage and matching revision and validator hashes. The same BuildBuddy workflow checks concurrent isolation, native features, shared OpenTelemetry regressions, and external consumers; its artifacts retain the Datadog evidence.

## Upstream-derived feature checks

The original `Test_Headers_Datadog` methods D001 (valid extraction) and D002 (invalid zero-ID extraction) execute from a commit- and SHA-256-pinned copy of upstream `test_headers_datadog.py`. The adapter presents each marked native server span through upstream's test-agent interface and checks that the upstream method's requested headers match the configuration actually exercised. The original method bodies supply the propagation assertions; local checks retain request ownership and native HTTP metadata validation. D002 uses default sampling rules so a new trace's sampling decision is independent of the invalid incoming priority.

This direct reuse covers D001 and D002 only. D003–D005 and the other upstream modules in the table below are references for local checks, not imported test methods. The adapter does not run upstream's container orchestration or claim to implement its complete parametric client API.

Every feature result contains its exact upstream file URL, configuration, baseline capture hash, configured capture hash, and status. Directly reused cases also record the original method name and pinned test-source hash. Held-parent cases retain the early capture hash. The files are under each external-feature test's `test.outputs` directory.

| Check | Reference under pinned `tests/parametric/` | Evidence |
| --- | --- | --- |
| Datadog, W3C, B3 single/multiple extraction, disabled extraction, precedence | `test_headers_datadog.py`, `test_headers_tracecontext.py`, `test_headers_b3.py`, `test_headers_b3multi.py`, `test_headers_none.py`, `test_headers_precedence.py` | Full trace and parent identity for every marked request |
| Malformed nonnumeric, zero, overflowing IDs; 64/128-bit generation and extraction | `test_headers_datadog.py`, `test_128_bit_traceids.py` | Invalid context starts a new trace; high bits checked separately |
| Sampling priority and origin propagation | `test_headers_datadog.py` | Native sampling metrics and origin metadata |
| Service/environment/version, configured header tags, method, status, user agent, selective query redaction | `test_tracer.py` | Four marked requests; query control must contain the dummy secret before configured redaction |
| Sampling rules at zero/one and first-match precedence | `test_trace_sampling.py` | Exported sampling decisions; transport omission never substitutes for a drop decision |
| Manual keep/drop and disabled tracing | `test_sampling_manual.py`, `test_tracer.py` | Native priorities versus a normal baseline; disabled tracing requires an empty capture |
| Nested spans, controlled exception, outbound client/server ancestry and restored context | `test_tracer.py` | Exact probe multiplicities, parents, exception fields, sibling after outbound call |
| Partial flush thresholds 1, 2, 1000 and disabled control | `test_partial_flushing.py` | Completed children while HTTP parent is held, chunk metadata, and unchanged reconstruction after release |

Run `//fixtures:datadog_external_features_suite`. Python 4.15.4 origin propagation passes on both intake versions; the former Python 4.14.0 duplicate-key waiver has been removed. Go 2.10.1 also reports the combined manual-drop/keep-rule case as **unsupported** only when the complete native graph shows dropped children but a root overwritten by the keep rule. Standalone manual keep/drop uses no sampling rules and is checked separately. Ruby uses its supported configuration API for partial flushing and Rack query quantization. Partial-chunk metadata is checked at the pinned SDK’s position: last span for Ruby, first span for Python/Go, with duplicate metadata rejected. Go's B3 single-header spelling is adapted explicitly in retained configuration.

## Shared Ruby, Python, and Go coverage

The external-feature suite registers each of the same 53 cases as an independent Bazel service test across all seven profiles: **371 targets**. Each target owns its startup, baseline/control capture, result, and receipt, so one case failure cannot prevent another case from running. This follows [rules_stests #57](https://github.com/pawelchcki/rules_stests/pull/57), in addition to the repeated-run standard from [#56](https://github.com/pawelchcki/rules_stests/pull/56). The original seven profile target names remain suites of their 53 independent cases. Each case runs twice in fresh application/SDK processes, validates both captures with the same predicates, compares normalized behavior, and retains hashes for primary/control captures and responses. The 19 added cases cover runtime/process identity, Unicode global tags, service-glob sampling, configured HTTP error ranges, preferred client-IP headers, malformed W3C/B3 contexts, and outbound propagation styles. Adapter differences are recorded in effective configuration. Unexpected success or unrelated failure of the precisely matched Go manual-drop defect fails the suite.

The [shared capability mapping](datadog-shared-capabilities-mapping.json) verifies **19/301 capabilities in each of Ruby, Python, and Go**. The remaining **282** are missing shared implementations and tracked in the [gap inventory](datadog-coverage-gaps.json). This differs from the broader Python-oriented 228/301 capability mapping; neither count proves complete upstream test-case parity. See [shared capability measurement](datadog-capabilities.md#shared-ruby-python-and-go-assertions) for the per-language evidence gate.

## Additional configuration and controlled-span coverage

The external-feature suite adds five cases for each of its seven Python, Ruby, and Go profiles, including Falcon: `DD_TAGS` comma separation, space separation, a value containing a colon, precedence of explicit `DD_SERVICE`/`DD_ENV`/`DD_VERSION` over the same keys in `DD_TAGS`, and `DD_TRACE_AGENT_URL` precedence over host and port. The last case checks where a trace is delivered, rather than relying only on a tracer configuration string. These are local native-trace checks based on [`test_config_consistency.py` at 255dc57d](https://github.com/DataDog/system-tests/blob/255dc57d719c4d33a1c45a1b41cd517c5cae5878/tests/parametric/test_config_consistency.py); its original method bodies are not imported. Each result retains the effective configuration and capture evidence. The fixed `ea8a597…` reference above remains the pin for the earlier feature cases.

`//fixtures:datadog_lab_suite` starts a dedicated Python 4.15.5 SDK fixture with a controlled root and child span. Its two wire-version tests each run seven propagation-injection configurations and eleven deterministic span-sampling configurations, for 36 cases total. Injection styles include `datadog`, `tracecontext`, `b3`, `b3multi`, `none`, and multiple styles. The SDK's `HTTPPropagator` writes into a retained carrier that the tests inspect. `none` is also checked alongside `datadog`. Span-sampling checks include rule matches and misses, service/name globs, rates zero and one, first-match precedence, and kept-trace controls. Native intake is checked at both v0.4 and v0.5. These local assertions are informed by the newer upstream header modules and [`test_span_sampling.py`](https://github.com/DataDog/system-tests/blob/255dc57d719c4d33a1c45a1b41cd517c5cae5878/tests/parametric/test_span_sampling.py). Results retain the effective Datadog environment, upstream source-file hash and method name, workload hash, carrier, and native capture hash. The lab is Python-only, and zero/one decisions do not establish statistical behavior at intermediate rates.

`tools/run_datadog_parity.sh` runs the lab suite uncached in BuildBuddy after the existing shape, stress, and external-feature checks, and retains its test outputs under `lab/` in the Datadog evidence archive. The Full test suite also includes the lab in its `//...` expansion. See the verification record for execution status of the current changes.

Configuration precedence is proved by trace delivery and span fields. The lab checks the exact two-span native graph and parses the full `x-datadog-tags` value where Datadog injection applies, including its trace-id high bits. Regression mutations reject missing, embedded, or extra tag fields.

## Concurrent request isolation

`//fixtures:datadog_parallel_suite` defaults to 32 workers and three repetitions of every scenario. Each profile runs one shared application and sink, with sequential Hurl requests inside each scenario. CI uses four workers and one repetition.

The proxy ledger records execution, sequence, request ID, method/path, response, incoming context, independent application SQL marker/count, and timing. Full 128-bit identities separate deliberate equal-low-bit callers. Every request requires one server span; every span requires an owner. Database hooks mark SQL before instrumentation, including cached Rails query events and both Go database and Gorm events. Go transaction lifecycle methods have no SQL text, so their request markers are attached through the SDK context before instrumentation and their calls are counted separately. Its bounded pool is established before startup reset. Counts include instrumented SQL operations/events, not only physical database round trips. Exact serial shapes remain unchanged; stress validation checks ownership and independently counted SQL events because shared contents affect query multiplicity.

Stress evidence contains the ledger, whole capture, hashes, configuration, assertion results, and observed proxy/native overlap. A serialized run cannot pass. Capture overflow fails explicitly (64 MiB aggregate, 8 MiB native intake request, bounded records and workload size). Only the coordinator resets once and drains once.

Targeted mutations cover moved SQL spans, wrong parents, duplicate/missing requests and spans, merged high bits, lost SQL, incorrect counts, missing markers, wrong service, and absent overlap.

## Validator execution and compatibility

Bazel compiles every effective Datadog Scheme validator to a cached bytecode artifact. The conformance and sink probes compile their fixed programs the same way, in parallel shards (`harness/scheme_bytecode.bzl`). Each compilation recompiles the VM prelude, which costs seconds per program. Runtime execution stays in the bounded VM. Source validation remains available for diagnostic probes and profiles without a compiled artifact. Hand-written Scheme captures without indexed ancestry use a bounded parent walk that rejects ambiguous parents and crossed high bits; an explicit indexed rejection remains authoritative. Manifests and receipts bind source, compiler, and bytecode SHA-256 digests; mismatches fail. The compiler identity is the Bazel-produced sink executable, within the same trusted build boundary as the manifest.

Native topology uses an index of full trace identity and span ID plus child adjacency. Untagged partial chunks inherit high bits only through unambiguous parent relationships. Intake counters are incremental; ingestion appends only new records while complete dumps and failure artifacts remain available. Snapshot/quiescence safeguards are preserved.

Driver timing artifacts separate startup drain, workload, drain/validation, compilation, validation execution, and total driver time. Application startup is recorded by the launcher. Build compilation cost is recorded in action logs. `tools/benchmark_datadog.py` measures five uncached executions of the original 34 combinations per variant with warmed builds, alternating order on the same executor at the same concurrency; acceptance requires at least 40% lower median test window. Build time is reported separately.

## Deliberate boundaries and remaining gaps

Legacy v0.3, Ruby/Go v0.5, profiling, AppSec, IAST, dynamic instrumentation, remote configuration, and other non-tracing products are outside this seven-profile tracing matrix. The separate broader Python capability suite covers portions of the other products; those checks still need shared Ruby/Go adapters before they contribute to shared coverage. Nonempty span links and structured span-event metadata are not accepted by the exact native schema. Propagation checks do not cover every upstream malformed-header permutation or every injection-style combination, and the Python lab supplies additional injection combinations beyond the four shared outbound styles. Sampling checks do not establish statistical accuracy at intermediate rates or rate-limit endurance. The lab does not exercise remote configuration or agent policy. HTTP probes do not exercise every method/status permutation. Partial-flush checks use deterministic small traces rather than production-size endurance workloads.

Ruby and Gin payloads are published at the digest and source-tree identities in `bazel/oci_images.lock.bzl`; publication verified anonymous pulls and rehashed the rootfs layers. Falcon uses the shared rootfs from the pinned `rules_stests` dependency with Ruby injection. BuildBuddy and Pages consume these locks without local image overrides. `tools/build_datadog_fixtures.sh` remains available for rebuilding the reviewed payloads, and `tools/publish_datadog_fixtures.sh` verifies the locally built payloads before publishing, then verifies anonymous pulls before updating locks. The publication workflow proposes the resulting lock changes in a PR. Optional local build caching via `DATADOG_FIXTURE_CACHE` verifies every cache hit against the reviewed payload digest. The OCI envelope may vary with the container tool's compression and history serialization, which do not affect the extracted rootfs the harness executes. The Ruby 2.43.0 payload was rebuilt from the updated, frozen application Gemfiles and lockfiles; runtime activation leaves those dependency files unchanged. Bootstrap checks cover frozen Bundler, repeated/early activation, incompatible or absent dependencies, ABI mismatch, and missing native extensions.

The standalone Datadog HTML report is generated from the retained exact-shape executions, independently rechecking each execution with the coverage gate. It shows profile and scenario coverage, authored feature assertions, and field-policy counts. External-feature unsupported results and the deliberate product boundaries above never become verified capabilities. BuildBuddy retains the report and raw evidence; Pages publishes `datadog-report.html` after two fresh gated executions.

To reproduce the comparison, export the original revision to a separate directory and keep its Bazel output base separate. For this change the original 34-case revision is `02bbcbba786bb9462beda4c27a0c73e20109a4fd`. Run the benchmark after other builds/tests finish:

```sh
tools/benchmark_datadog.py --original /path/to/original-checkout \
  --original-revision 02bbcbba786bb9462beda4c27a0c73e20109a4fd \
  --original-output-base /tmp/datadog-original-bazel \
  --output /tmp/datadog-benchmark --jobs 4 \
  --cold-output-root /tmp/datadog-cold-builds
```

The optional cold-build run uses fresh Bazel action caches for both variants. Downloaded dependencies may still come from the shared repository cache; those timings are reported separately from the warmed test medians.
