# Capability expansion priorities

The broad target is at least **226 of 301 named capabilities (75%)**. Current acceptance verifies **228/301 (75.7%)**. The tables below preserve the initial implementation plan of **49 candidates**, using the existing Python SDK and Django/aiohttp fixtures. Several batches have since implemented these and additional features. The [generated report](datadog-capabilities-report.md) is authoritative for current verified coverage; inclusion in this initial plan does not mean a feature remains missing or has passed.

Backend capture supports telemetry and UDP metrics. Genuine OpenTelemetry configuration observations must come from SDK state or emitted telemetry; copying requested environment variables into an asserted dictionary supplies no evidence. The pinned Python manifest excluded four OTEL_BLRP capabilities when this plan was written. Subsequent native OTLP experiments prove batch-size and scheduling behavior; unsupported timeout behavior remains explicit. Manifest exclusions do not override scoped proof from the actual SDK. `app_client_configuration_change_event` is referenced only by an upstream infrastructure mock, so it was omitted from this initial plan; the newer signed RC lab verifies real native configuration-change telemetry.

Upstream owner groups (a source organization, not a guarantee of mutually exclusive product areas):

| Upstream owner | Named capabilities |
| --- | ---: |
| `asm` | 118 |
| `sdk_capabilities` | 81 |
| `idm` | 36 |
| `ml_observability` | 18 |
| `injection_platform` | 17 |
| `debugger` | 10 |
| `language_platform` | 9 |
| `ffe` | 6 |
| `profiler` | 2 |
| `agent_apm` | 1 |
| `djm` | 1 |
| `remote_config` | 1 |
| `apm_serverless` | 1 |

## Telemetry and backend capture

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `telemetry_app_started_event` / 79 | Require exactly one startup event, correct app/service/runtime identity, and startup ordering. | [test_library_settings](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_telemetry.py#L87) |
| `telemetry_configurations_collected` / 69 | Set DD_* controls and validate reported effective values and origins against the configuration. | [test_library_settings](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_telemetry.py#L174) |
| `telemetry_heart_beat_collected` / 70 | Shorten the heartbeat interval; assert subsequent heartbeat sequence and delay bounds. | [test_app_heartbeats_delays](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L339) |
| `telemetry_metrics_collected` / 73 | Generate controlled spans and validate named created/finished/enqueued counters with exact dimensions. | [test_metric_generation_disabled](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L970) |
| `telemetry_api_v2_implemented` / 74 | Validate api_version=v2, application/host envelopes, request headers, and event schemas. | [test_app_started_product_info](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L791) |
| `app_extended_heartbeat_event` / 77 | Trigger extended heartbeat; validate dependencies, configurations, and integrations in its payload. | [test_extended_heartbeat_config_matches](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L1095) |
| `telemetry_message_batch` / 78 | Flatten an actual message-batch and assert the preserved inner event types, payloads, and sequence IDs. | [test_message_batch_enabled](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L937) |
| `dd_telemetry_dependency_collection_enabled_supported` / 80 | Compare enabled/disabled dependency collection controls and validate actual loaded dependency payloads. | [test_app_dependency_loaded_not_sent_dependency_collection_disabled](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L920) |
| `telemetry_instrumentation` / 229 | Compare tracer-to-Agent and Agent-to-backend telemetry, including Agent header enrichment. | [test_api_still_v1](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_telemetry.py#L446) |

## Logging and UDP metrics

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `log_injection` / 5 | Emit stdlib logging records within/outside an active trace; compare injected trace/span IDs with native capture. | [test_log_injection_enabled](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L482) |
| `structured_log_injection` / 5 | Use a JSON formatter with injected service/env/version and full trace/span correlation fields. | [test_log_injection_enabled](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L482) |
| `unstructured_log_injection` / 477 | Use a text formatter; assert exact correlation fields and their absence/default outside the trace. | [test_test_log_injection_default](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L536) |
| `log_injection_128bit_traceid` / 387 | Continue a known 128-bit incoming trace and compare its emitted log trace ID with the full native identity. | [test_incoming_128bit_traceid](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L589) |
| `log_tracer_status_at_startup` / 10 | Retain the enabled startup status log and assert configured values; disabled control must omit it. | [test_startup_logs_default](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_startup_logs.py#L70) |
| `runtime_metrics` / 32 | Capture DogStatsD UDP datagrams; assert genuine Python runtime metrics with expected service/env/version tags. | [test_main](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L662) |
| `dogstatsd_agent_connection` / 17 | Capture UDP at the selected endpoint and prove URL precedence over host/port using delivery controls. | [test_dogstatsd_custom_hostname](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_config_consistency.py#L390) |

## Existing HTTP fixtures and deterministic SDK controls

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `trace_http_server_error_statuses` / 379 | Return controlled 4xx/5xx responses from existing Django/aiohttp; compare custom ranges against defaults. | [test_status_code_400](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L38) |
| `trace_http_client_error_statuses` / 381 | Call a local HTTP peer with controlled status; compare native client span error values before/after custom ranges. | [test_status_code_400](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L177) |
| `trace_http_client_tag_query_string` / 382 | Call a local peer with safe and secret query controls; assert configurable client query tagging. | [test_query_string_redaction_unset](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L249) |
| `integration_enablement` / 385 | Compare native Django/aiohttp/requests integration spans with the specific integration disabled and enabled. | [test_integration_enabled_false](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L425) |
| `referrer_hostname` / 396 | Send controlled Referer values and compare the canonical hostname tag while excluding credentials/path/query. | [test_referrer_hostname](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_standard_tags.py#L392) |
| `trace_client_ip_header` / 88 | Send conflicting forwarding/client-IP headers and assert explicit override and precedence on native spans. | [test_ip_headers_sent_in_one_request](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_config_consistency.py#L281) |
| `trace_rate_limiting` / 377 | Use an SDK-controlled clock/workload and native keep/drop decisions; prove configured rate-limit exhaustion. | [test_default_trace_rate_limit](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_config_consistency.py#L224) |
| `decisionless_extraction` / 261 | Extract valid trace identity without a sampling decision, create a child, and assert delegated decision behavior. | [test_sampling_delegation_extract_neither_decision_nor_delegation](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/test_sampling_delegation.py#L39) |

## Python 4.14 OpenTelemetry compatibility configuration

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `otel_service_name` / 566 | Set OTEL_SERVICE_NAME and DD_SERVICE independently/together; observe effective native service and config precedence. | [test_datadog_configuration_takes_precedence](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_service_name.py#L144) |
| `otel_exporter_otlp_endpoint` / 585 | Set the generic OTLP endpoint and compare effective per-signal configuration through actual SDK state/telemetry. | [test_empty_endpoint_falls_back_to_default](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_exporter_otlp_endpoint.py#L90) |
| `otel_exporter_otlp_logs_endpoint` / 592 | Set global and logs endpoints; validate the logs endpoint override through genuine SDK configuration. | [test_empty_falls_back_to_global_endpoint](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_exporter_otlp_logs_endpoint.py#L81) |
| `otel_exporter_otlp_metrics_endpoint` / 601 | Set global and metrics endpoints; validate the metrics endpoint override through genuine SDK configuration. | [test_empty_falls_back_to_global_endpoint](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_exporter_otlp_metrics_endpoint.py#L92) |
| `otel_exporter_otlp_traces_endpoint` / 614 | Set global and traces endpoints; validate the traces endpoint override through genuine SDK configuration. | [test_empty_falls_back_to_global_endpoint](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_exporter_otlp_traces_endpoint.py#L81) |
| `otel_metric_export_timeout` / 632 | Validate supported timeout translation using genuine SDK configuration, with valid and documented boundary cases. | [test_invalid_values](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_metric_export_timeout.py#L67) |
| `otel_resource_attributes` / 634 | Validate service/env/version/resource tags and precedence through actual native spans and SDK configuration. | [test_default_matches_specification](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_resource_attributes.py#L133) |
| `otel_sdk_disabled` / 635 | Set the compatibility disable switch; require empty capture versus a nonempty enabled control. | [test_datadog_configuration_takes_precedence](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_sdk_disabled.py#L88) |
| `otel_traces_exporter` / 640 | Exercise supported exporter values and observe genuine config/transport effects; unsupported exporters remain gaps. | [test_console_exporter](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_traces_exporter.py#L206) |
| `otel_traces_sampler` / 641 | Exercise supported always-on/off and parent-based sampler mappings; inspect native sampling priorities. | [test_dd_trace_sample_ignore_parent_false](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_traces_sampler.py#L174) |
| `otel_traces_sampler_arg` / 642 | Exercise supported deterministic 0/1 arguments and parent controls; inspect native decisions and config. | [test_above_range_is_ignored](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/parametric/otel_env_vars/test_otel_traces_sampler_arg.py#L154) |
| `otel_api` / 84 | Create OpenTelemetry API spans using the ddtrace provider and assert native parentage, attributes, status and full IDs. | [test_datadog_otel_span](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/apm_tracing_e2e/test_otel.py#L24) |
| `f_otel_interoperability` / 289 | Nest Datadog and OpenTelemetry spans in both directions and assert one coherent native trace/context. | [test_otel_drop_in_span_metrics](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/integrations/test_otel_drop_in.py#L32) |
| `otel_propagators_api` / 361 | Use the default OpenTelemetry propagator with incoming context; assert actual extract/inject carrier identities. | [test_propagation_extract](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/otel/test_context_propagation.py#L20) |

## Existing native capture predicates and SDK baggage

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `runtime_id_in_span_metadata_for_service_entry_spans` / 22 | Emit service-entry roots and validate runtime-id format/stability; wire existing process-identity predicate into feature receipts. | [test_meta_component_tag](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_semantic_conventions.py#L284) |
| `trace_data_integrity` / 266 | Run controlled IDs/start/duration/headers; reuse exact native span and intake predicates with feature receipts. | [test_trace_ids](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_data_integrity.py#L18) |
| `semantic_core_validations` / 262 | Reuse controlled native HTTP/database spans and validate required semantic fields against upstream assertions. | [test_client_address](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_otel_http_semantics.py#L478) |
| `library_scrubbing` / 267 | Exercise URL credentials, query secrets and sensitive database resources; compare unredacted controls and scrubbed native fields. | [test_no_ip](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_no_ip_is_reported.py#L27) |
| `datadog_baggage_headers` / 389 | Use real SDK baggage APIs and propagators; validate malformed/valid headers, percent encoding, allow-list controls and native carrier. | [test_basic](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_baggage.py#L66) |

## Small AppSec extensions on existing Django

| Capability / ID | Concrete assertion | Pinned upstream reference |
| --- | --- | --- |
| `user_monitoring` / 141 | Set a controlled user through the supported SDK API and assert usr.id and configured user collection behavior. | [test_login_pii_success_basic](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/appsec/test_automated_login_events.py#L105) |
| `custom_business_logic_events` / 161 | Invoke supported login/signup/custom-event APIs and validate native event tags plus event type. | [test_custom_event_event](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/appsec/test_event_tracking.py#L186) |
| `propagation_of_user_id_rfc` / 146 | Set propagated user ID and prove carrier encoding plus receiving-span user identity using a local HTTP peer. | [test_identify_tags](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/test_identify.py#L59) |
| `event_tracking_sdk_v2` / 372 | Exercise v2 login/signup API with deterministic users; compare normalized event metadata and consent/config controls. | [test_user_login_success_event_deep_metadata](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/appsec/test_event_tracking_v2.py#L170) |
| `appsec_standard_tags_client_ip` / 233 | Enable AppSec on Django and assert correct client-IP tags for conflicting incoming headers and overrides. | [test_not_reported](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/appsec/test_client_ip.py#L22) |
| `security_events_metadata` / 124 | Send a standard WAF attack to a dedicated Django route and assert genuine trigger-rule metadata on the retained trace. | [test_basic](https://github.com/DataDog/system-tests/blob/098fe0967c587db8a16b74a1e711777d0a9d5867/tests/appsec/test_reports.py#L16) |

## Security expansion and remaining gaps

The implemented [security lab](datadog-capabilities-security.md) exercises controlled Django IAST sources/sinks, WAF rules and blocking, RASP, API schemas and standalone billing without another application framework. It requires genuine native vulnerability reports, correlated source evidence and locations, and absence on independent safe controls. URI/multipart origins, extended custom-header collection and structured v0.5 findings remain explicit gaps. Further Kafka/LDAP/MongoDB sources/sinks require extra client/server dependencies.

Further WAF/security expansion includes rate limits, sensitive-data obfuscation and truncation, additional authentication schemas and user/event controls. Reuse existing fixtures and verify native WAF/IAST behavior, actual backend protocols, rule distribution and response controls before assigning mappings. The separate remote configuration, debugger and profiling labs exercise their real protocols; additional cases in those areas still require corresponding runtime evidence.

The denominator still includes unsupported SDK features, installation/SSI, cloud services, messaging integrations, ML observability and other upstream capabilities. Retain uncovered behavior as gaps. Continue adding scoped runtime assertions without narrowing the denominator or counting case multiplicity as capability breadth.
