# Executed shared SDK xfails

Snapshot from the local native v0.4 run on 2026-10-08. Every case uses a healthy baseline and two fresh SDK processes; an xfail is recorded only after an assertion fails and a subsequent control workload exports successfully.

Go SDK **2.10.1**: **314 passes, 25 xfails** out of 339 cases. Python SDK **4.15.5**: **333 passes, 6 xfails** out of the same 339 cases.

The [pinned upstream Go manifest](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/manifests/golang.yml) supplies the Go exclusions below. Reasons retain upstream wording: a manifest exclusion may describe an unsupported feature, a known bug, an irrelevant assertion, or a flaky check. Manifested cases that pass are reported as XPASS and count as executed passes. Unported feature adapters are reported separately and never called xfails.

Counts can vary for manifested flaky cases; this list records observed outcomes, not every manifest declaration.

## Go

| Executed test | Expected failure reason |
| --- | --- |
| [`local_cases.Test_Lab_Http.test_referrer_hostname[0]`](../harness/upstream_lab/local_cases.py) | missing_feature |
| [`test_config_consistency.Test_Config_RateLimit.test_setting_trace_rate_limit_strict[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_config_consistency.py) | bug (APMAPI-1030) |
| [`test_headers_baggage.Test_Headers_Baggage.test_baggage_inject_header_D004[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_headers_baggage.Test_Headers_Baggage_Span_Tags.test_baggage_span_tags_all[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_headers_baggage.Test_Headers_Baggage_Span_Tags.test_baggage_span_tags_config_with_empty_keys[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_headers_baggage.Test_Headers_Baggage_Span_Tags.test_baggage_span_tags_default[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_headers_baggage.Test_Headers_Baggage_Span_Tags.test_baggage_span_tags_key_with_asterisk[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_headers_baggage.Test_Headers_Baggage_Span_Tags.test_baggage_span_tags_specific_keys[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_headers_baggage.py) | missing_feature |
| [`test_otel_resource_attributes.Test_OTEL_RESOURCE_ATTRIBUTES.test_invalid_value_is_discarded[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_resource_attributes.py) | bug (APMAPI-2432) |
| [`test_otel_sdk_disabled.Test_OTEL_SDK_DISABLED.test_datadog_configuration_takes_precedence[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_sdk_disabled.py) | missing_feature |
| [`test_otel_sdk_disabled.Test_OTEL_SDK_DISABLED.test_stable_false[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_sdk_disabled.py) | missing_feature |
| [`test_otel_sdk_disabled.Test_OTEL_SDK_DISABLED.test_stable_true[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_sdk_disabled.py) | missing_feature |
| [`test_otel_span_methods.Test_Otel_Span_Methods.test_otel_record_exception_attributes_serialization[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_otel_span_methods.py) | missing_feature (Newer agents/testagents enabled native span event serialization by default) |
| [`test_otel_span_methods.Test_Otel_Span_Methods.test_otel_record_exception_meta_serialization[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_otel_span_methods.py) | missing_feature (Newer agents/testagents enabled native span event serialization by default) |
| [`test_otel_span_methods.Test_Otel_Span_Methods.test_otel_record_exception_sets_all_error_tracking_tags[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_otel_span_methods.py) | irrelevant (new error tracking tagging introduced in v2.7.0) |
| [`test_otel_span_methods.Test_Otel_Span_Methods.test_otel_set_attribute_remapping_httpstatuscode[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_otel_span_methods.py) | irrelevant (Does not support automatic status code remapping to meta) |
| [`test_otel_span_methods.Test_Otel_Span_Methods.test_otel_span_link_attribute_handling[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_otel_span_methods.py) | missing_feature (Not implemented) |
| [`test_otel_traces_sampler_arg.Test_OTEL_TRACES_SAMPLER_ARG.test_nonnumeric_is_ignored[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_traces_sampler_arg.py) | missing_feature (Nonnumeric sampler arguments are not ignored) |
| [`test_partial_flushing.Test_Partial_Flushing.test_partial_flushing_one_span_default[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_partial_flushing.py) | missing_feature (partial flushing not enabled by default) |
| [`test_sampling_span_tags.Test_Sampling_Span_Tags.test_tags_defaults_rate_1_and_rule_0_sst006[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_sampling_span_tags.py) | bug (APMAPI-737) |
| [`test_span_events.Test_Span_Events.test_span_with_event_v04[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_span_events.py) | missing_feature |
| [`test_span_sampling.Test_Span_Sampling.test_single_rule_always_keep_span_sampling_sss011[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_span_sampling.py) | missing_feature (The Go tracer does not have a way to modulate trace sampling once started) |
| [`test_trace_sampling.Test_Trace_Sampling_Tags_Feb2024_Revision.test_metric_existence[4]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_trace_sampling.py) | flaky (APMAPI-932) |
| [`test_trace_sampling.Test_Trace_Sampling_Tags_Feb2024_Revision.test_metric_existence[5]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_trace_sampling.py) | flaky (APMAPI-932) |
| [`test_trace_sampling.Test_Trace_Sampling_With_W3C.test_distributed_headers_synthetics_sampling_decision[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_trace_sampling.py) | bug (APMAPI-1563) |

The four malformed-header checks D012–D015 now pass after correcting adapter context reuse. The injection check D004 exposes an SDK space-encoding defect (`+` instead of `%20`). Five span-tag checks exercise manual `tracer.StartSpan` calls; Go 2.10.1 applies baggage tags through HTTP request instrumentation instead. The separate framework-neutral HTTP baggage-tag case passes in Go, so these manual API failures do not mean Go lacks HTTP baggage tags. D017 now uses a [local order-independent assertion](../harness/shared_sdk/baggage_cases.py) in both languages and passes. It checks all 24 insertion orders, the 8,192-byte bound, complete unchanged members, and lossless propagation below the limit. [W3C permits the choice and order of dropped items](https://www.w3.org/TR/baggage/#limits); the original exact-count assertion remains in the pinned vendor source, but its manifest exclusion does not apply to the corrected local assertion. [W3C requires percent encoding of spaces](https://www.w3.org/TR/baggage/#value), so D004 is a genuine encoding defect despite its upstream `missing_feature` label.

## Python

| Executed test | Expected failure reason |
| --- | --- |
| [`local_cases.Test_Lab_Http.test_independent_client_status_ranges[0]`](../harness/upstream_lab/local_cases.py) | The aiohttp client uses the server error range; status 200 is erroneous on both spans. |
| [`local_cases.Test_Lab_Http.test_query_redaction_empty[0]`](../harness/upstream_lab/local_cases.py) | An empty obfuscation regex preserves the server query but removes the aiohttp client query. |
| [`portable_cases.HTTP.baggage_tags[0]`](../harness/shared_sdk/portable_cases.py) | Python 4.15.5 aiohttp propagates baggage but omits configured baggage.* tags on the server span. |
| [`test_partial_flushing.Test_Partial_Flushing.test_partial_flushing_propagation_tags[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_partial_flushing.py) | flaky (APMAPI-734) |
| [`test_span_events.Test_Span_Events.test_span_with_invalid_event_attributes[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_span_events.py) | missing_feature |
| [`test_tracer.Test_TracerServiceNameSource.test_tracer_no_srv_src_when_service_not_manually_set[0]`](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_tracer.py) | irrelevant |

Python's positive baggage-tag xfail is a local, evidence-bound exception, not an alteration of the upstream manifest. The retained SDK observations prove that baggage arrives at the aiohttp server, while the native server span omits its configured tags. The matcher requires this exact failure, SDK version, assertion source hash, native span identities, parentage, and baggage observations. See [the matcher](../harness/shared_sdk/sdk_expected_failures.py).

Generate current results from retained Bazel outputs:

```sh
python3 tools/datadog_shared_sdk_report.py \
  --evidence-dir bazel-testlogs/fixtures --require-complete-matrix \
  --output /tmp/datadog-shared-sdk-report.json
```
