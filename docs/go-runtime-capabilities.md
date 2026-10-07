# Go runtime capability matrix

The capability suite builds one pinned patch release for **every Go minor from
1.4 through 1.27**. Linux amd64 is the default. Linux arm64 is configurable and
requires a native arm64 builder; releases without an official arm64 archive
produce `unsupported-toolchain` receipts. The lock includes toolchain URLs,
SHA-256 checksums, release notes, and the reason for retaining each minor.

Testing every minor covers intervening internal layout changes as well as the
larger boundaries:

| Boundary | Runtime change |
|---|---|
| 1.4 → 1.5 | C runtime baseline → runtime written in Go and concurrent GC |
| 1.7 | amd64 frame pointers |
| 1.14 | asynchronous preemption |
| 1.16 | new pclntab format |
| 1.17 | register calling convention on amd64 |
| 1.18 | register calling convention on arm64, generics, new pclntab format |
| 1.20 | new pclntab format |
| 1.24 | Swiss Table maps |
| 1.25 → 1.26 | experimental → default Green Tea GC |
| 1.27 | size-specialized allocation entry points |

References: [Go release notes](https://go.dev/doc/devel/release),
[pclntab format versions](https://go.dev/src/debug/gosym/pclntab.go).
Go versions before 1.4 are outside this initial matrix. Patch releases are
explicitly pinned in `harness/go_runtime/versions.lock.json`; update that lock
and `versions.bzl` together when extending the matrix.

The app has no tracer calls. It exercises mixed integer/floating-point arguments
and multiple returns, recursive stack growth, goroutines, GC, and a Go→C→Go
callback while making real HTTP requests. CGO and external linking produce ELF
binaries with a dynamic interpreter so `LD_PRELOAD` can load a library. The
manifest records the actual compiler, ELF architecture, pclntab magic, source
fingerprint, dependency manifests, and executable checksum.

Three default backends run the same workload:

* `plain`: verifies workload correctness and absence of exported traces.
* `orchestrion`: Datadog Orchestrion 1.13.0 with Go SDK **2.10.1**.
* `alibaba`: Alibaba LoongSuite Go 1.15.0 (`otel go build`), including pinned
  amd64 and arm64 instrumentor binaries.

Both pinned instrumentors require Go 1.25. Older rows report
`unsupported-build` with the pinned requirement. This is a build restriction,
not an executed feature xfail or a capability pass. Plain binaries on these
older releases remain available to replacement instrumentation. The existing
shared SDK feature tests and their upstream xfail manifests are unchanged.

## Run and retain the default matrix

Prerequisites: Linux, Python 3.12+, GCC, Bazel, and access to the pinned download
URLs and Go module proxy. Preparation happens before the test actions.

```sh
tools/run_go_runtime_capabilities.sh /tmp/go-runtime-apps /tmp/go-runtime-evidence
```

This builds all 24 runtimes and three backends, runs 72 fresh Bazel tests, retains
captures and logs, and verifies the report against application and artifact
checksums. Each executable test repeats in two fresh app processes. Automatic
tracing must export a server→client→server tree with the known external parent,
matching propagated headers, status codes, service names, positive durations,
and HTTP 503 error semantics. Datadog's HTTP client marks transport errors;
OpenTelemetry also marks received 5xx responses. Both mark the 503 server span.
Each repetition first runs a tracer-free control
compiled with the same Go version. An exporter crash or missing native span
fails the test.

To prepare or run subsets:

```sh
python3 tools/build_go_runtime_matrix.py --output /tmp/go-runtime-apps \
  --version go1.17.13 --version go1.25.14
bazel test --config=local --nocache_test_results \
  --override_repository=go_runtime_apps=/tmp/go-runtime-apps \
  //fixtures:go_runtime_orchestrion_suite_go1_25_14_test
```

`//fixtures:go_runtime_capability_suite` selects the full matrix;
`//fixtures:datadog_go_capability_suite` also includes it alongside shared SDK
cases and Gin integration checks. Explicit suite selection is required because
the runtime targets are tagged `manual` and require prepared binaries.

On a native arm64 machine:

```sh
tools/run_go_runtime_capabilities.sh /tmp/go-runtime-apps-arm64 \
  /tmp/go-runtime-evidence-arm64 arm64
```

## Replace instrumentation from a downstream module

Load the public macro from `@rules_datadog_stests//rules:defs.bzl`. Prepare plain
apps with the builder and override the module extension repository:

Expose the prepared repository in the downstream `MODULE.bazel` so the override
flag resolves there:

```starlark
go_runtime = use_extension("@rules_datadog_stests//bazel:go_runtime_deps.bzl", "go_runtime_deps")
use_repo(go_runtime, "go_runtime_apps")
```

```sh
python3 tools/build_go_runtime_matrix.py --output /tmp/my-go-apps --backend plain
bazel test --override_repository=go_runtime_apps=/tmp/my-go-apps //:preload
```

For startup loading:

```starlark
load("@rules_datadog_stests//rules:defs.bzl", "go_runtime_capability_tests")

go_runtime_capability_tests(
    name = "preload",
    backend = "custom",
    library = "//instrumentation:libtrace.so",
    environment = {"MY_TRACE_ENDPOINT": "{sink}"},
)
```

The launcher sets `LD_PRELOAD` only on the instrumented process. The same plain
binary runs without that library or custom environment as its negative control.
Environment values support `{app}`, `{library}`, `{sink}`, `{output}`, and
`{phase}` placeholders. Export native Datadog v0.4/v0.5 or OTLP HTTP/protobuf
traces to the supplied sink. Configure exporter options through `environment`
when your library needs different settings.

For activation after startup:

```starlark
go_runtime_capability_tests(
    name = "attach",
    backend = "custom",
    mode = "attach",
    library = "//instrumentation:libtrace.so",
    attacher = "//instrumentation:attach",
    attach_args = ["--pid", "{pid}", "--library", "{library}", "--endpoint", "{sink}"],
)
```

The app first becomes healthy and completes a request with no native spans. The
launcher then executes your controller with the existing PID, verifies that the
same app is still running, and sends the traced success/error workloads.
Controller argv additionally supports `{pid}` and `{url}`. The controller must
return successfully only once instrumentation is ready. A fresh process repeats
the entire sequence. The harness supplies the activation contract; the caller
supplies the library and the actual attachment mechanism.

Set `architecture = "arm64"` on downstream targets for that architecture. Use
`versions = ["go1.17.13", "go1.25.14"]` to select releases. A downstream
`applications` dictionary can replace each version with
`struct(app = label, manifest = label, data = [...])`; automatic backends can
also override `controls` with matching tracer-free descriptors. Manifests must
follow the builder's schema and match the executable checksum and observed
runtime/architecture. Custom backends require a `plain` app manifest so a
compiled-in tracer cannot satisfy the replacement test.

These tests exercise capability, independent of corpus profiles and existing
application runtime profiles. They do not claim that a downstream library
supports a Go version until its real native traces pass the checks.
