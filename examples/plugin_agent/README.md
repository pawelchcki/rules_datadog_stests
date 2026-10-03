# Consumer-owned Datadog profile example

Run `bazel build //:telemetry_api_check` to compile a consumer-owned profile
against an external reviewed reference and verify provider ownership, wire
identity and default injection labels. Run `bazel test //:example_datadog_hurl_test`
for aiohttp, or `//:example_django_datadog_hurl_test` for Django.
The local module override is for development; published consumers select an
immutable `rules_datadog_stests` revision.
