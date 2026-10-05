# Repository extraction

Extracted from `pawelchcki/rules_stests` at
`11b5e4768eb63f3b7328b742278e90fcaa30b8a6`.

Datadog corpus, reviewed shapes, SDK assertions, receipt/report tools, tracer
fixture locks and publication belong to this repository. Common application
sources and runtimes remain in rules_stests, including Falcon. Shared
infrastructure is a one-way dependency pinned in `MODULE.bazel`. The current pin
consumes the split infrastructure APIs and cache-safe OCI directory materializer
directly; no compatibility patch or duplicated engine source is required.

Historical evidence in `docs/` refers to executions before extraction. Those
records retain their original revisions and links; they do not claim that the
new repository has produced fresh acceptance evidence.

The infrastructure pin and external-consumer example both reference
`db94b7ae98d1c8765b2ec236f6732683bbbfb54d`, which includes the
[infrastructure removal](https://github.com/pawelchcki/rules_stests/pull/47)
and [native intake fixes](https://github.com/pawelchcki/rules_stests/pull/50).

Validated after extraction:

- Both Bazel target graphs load; rules_stests has no Datadog assertion test targets.
- Eight Datadog unit and evidence-tool targets pass against the GitHub-pinned infrastructure.
- Scheme conformance, sink validation, and reviewed-shape tests pass.
- An aiohttp scenario, both wire-version SDK labs, and Ruby bootstrap pass.
- The external consumer-owned profile compiles with its provider/API checks.
- Four focused shared-infrastructure regression targets pass.
- 171 moved assertion, shape and review files retain their original bytes.
- Shared-app import and fixture-cache tests, Falcon smoke, Ruby bootstrap, and
  external-feature regression tests pass with the pinned dependency.
- All 29 assembled Gin context files match the previously reviewed context.
- All Datadog suite graphs analyze against the infrastructure removal branch.

Full fresh 112-scenario parity evidence has not been regenerated for this extraction.
