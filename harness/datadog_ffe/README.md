This lab runs Datadog's real 4.15.4 OpenFeature provider with the pinned
OpenFeature 0.8.3 and OpenTelemetry 1.44.0 wheels. It uses the existing Python
rootfs; no additional language or application framework is introduced.

The 217 evaluation vectors and UFC fixtures are copied unchanged from Datadog
system-tests revision 098fe0967c587db8a16b74a1e711777d0a9d5867. Their original
paths and hashes, plus the upstream license, are retained in `vendor/`.

The local HTTP backend delivers actual JSON:API configurations to the provider,
records conditional ETag polls, and changes a flag while the provider is alive.
It captures the SDK's original EVP HTTP request bytes and forwards trace and
OTLP requests unchanged to the native intake. This tests the SDK transport
boundary; actual Agent EVP forwarding is outside this lab's assertion scope.

Assertions compare every pinned vector with its real provider result. They
also check native root-only flag enrichment and hashed subject IDs, exposure
deduplication, aggregated evaluation counts and fallback errors, and native
protobuf metrics with an exemplar linked to the actual exported child span.
Raw targeting keys and evaluation attributes are retained in the EVP evidence;
the lab does not claim an additional privacy transformation.

Run `bazel test --config=local //fixtures:datadog_ffe_test`. Its undeclared
outputs contain the native intake, original backend requests and configuration
responses, SDK operation receipt, wheel locks, source manifest and result
hashes. All six feature receipts require the complete assertion set to pass.
