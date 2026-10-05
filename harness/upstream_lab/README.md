# Pinned upstream Python SDK lab

This lab executes selected original Datadog `system-tests` parametric test
methods against the real Python tracer and the native Datadog intake in
`rules_stests`. It reuses the existing Python application and rootfs.

Run both native wire configurations with:

```sh
bazel test --config=local //fixtures:datadog_upstream_lab_suite
```

The vendor manifest pins upstream revision
`098fe0967c587db8a16b74a1e711777d0a9d5867`, records every source hash and retains
the upstream license. The probe verifies those hashes before loading any test.
It also requires assertions to be enabled and checks the running tracer is
`ddtrace` 4.14.0. Updating either pin requires reviewing the vendor manifest,
the matching Python support manifest and the explicit exclusions.

The adapter replaces infrastructure imports and decorators with fixture routing
and lowers top-level Python 3.12 typing aliases for the Python 3.11 test runner.
It preserves original test method bodies, assertions and parameter values.
The fixture application calls actual Datadog and OpenTelemetry SDK APIs; it does
not synthesize spans or configuration. Configuration checks read SDK state.

The intake preserves native wire records. For upstream assertions that expect
logical traces, the adapter groups received chunks by their full trace identity.
Every successful case must also prove that each span identity returned by the
SDK reached the native intake with the requested wire version and valid timing.
Configuration-only cases emit a separate SDK export control span. Disabled
tracing cases require an empty intake and are reported as suppression evidence.

Each test's undeclared outputs contain `datadog-upstream-results.json`, raw
capture snapshots and SDK operation receipts. Results include the source pin,
configuration, capability names, status and SHA-256 hashes of both receipts.
The capability gate verifies retained capture hashes and counts only mapped,
passing assertions; parameter counts do not imply whole-feature parity.

Unsupported methods remain in the result inventory with a reason. Exclusions
include upstream Python gaps and facilities that need separate labs, such as
client statistics negotiation, tracer log directories and inaccessible Agent
endpoints. The native span-event configuration switches this tracer's encoder
to v0.4, so the original v0.5 span-event case is explicitly unsupported rather
than accepted under the wrong wire version. `sourceSelection` documents the
config-consistency file boundary for separate stable-config fixtures.

For focused runtime discovery, pass `--select=<case-name-substring>` to the
probe. These reduced receipts are diagnostic evidence and do not replace the
complete suite's acceptance results.
