import copy
import unittest

from harness.datadog_telemetry import assertions as check

IDENTITY = {"runtime_id": "123456781234123412341234567890ab", "tracer_version": "4.15.5", "language_version": "3.11.15",
            "dependency": {"name": "aiohttp", "version": "3.13.2"}, "spans": [{"span_id": str(index)} for index in range(3)]}


def fixture(dependencies=True):
    configuration = [{"name": key, "value": value, "origin": "env_var"} for key, value in {
        "DD_ENV": "telemetry-env", "DD_SERVICE": "telemetry-lab", "DD_VERSION": "telemetry-version", "DD_TRACE_RATE_LIMIT": "7",
        "DD_TRACE_ENABLED": "true", "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true", "DD_TELEMETRY_HEARTBEAT_INTERVAL": "5.0",
        "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED": str(dependencies).lower()}.items()]
    configuration.append({"name": "DD_TRACE_RATE_LIMIT", "value": "100", "origin": "default"})
    dependency = dict(IDENTITY["dependency"])
    integration = {**dependency, "enabled": True}
    metrics = [{"namespace": "tracers", "metric": name, "tags": tags, "points": [[100, 3]], "type": "count", "common": True}
               for name, tags in [("spans_created", ["integration_name:datadog"]), ("spans_finished", ["integration_name:datadog"]),
                                  ("trace_chunks_sent", ["src_library:libdatadog"])]]
    batch = [{"request_type": "app-integrations-change", "payload": {"integrations": [integration]}},
             {"request_type": "generate-metrics", "payload": {"series": metrics}}]
    if dependencies:
        batch.append({"request_type": "app-dependencies-loaded", "payload": {"dependencies": [dependency]}})
    items = [("app-started", {"configuration": configuration, "products": {"tracer": {"enabled": True, "version": "4.15.5"}}}),
             ("message-batch", batch)]
    items += [("app-heartbeat", {})] * 6
    items += [("app-extended-heartbeat", {"configuration": configuration, "dependencies": [dependency] if dependencies else [],
                                        "integrations": [integration]})] * 2
    records = []
    for index, (kind, payload) in enumerate(items):
        document = {"api_version": "v2", "runtime_id": IDENTITY["runtime_id"], "seq_id": index + 1, "tracer_time": 100 + index,
                    "application": {"service_name": "telemetry-lab", "env": "telemetry-env", "service_version": "telemetry-version",
                        "language_name": "python", "language_version": "3.11.15", "tracer_version": "4.15.5",
                        "runtime_name": "CPython", "runtime_version": "3.11.15"},
                    "host": {"hostname": "local", "os": "Linux", "architecture": "x86_64", "kernel_name": "Linux"},
                    "request_type": kind, "payload": copy.deepcopy(payload)}
        records.append({"path": check.TELEMETRY_PATH, "status": 200, "raw_sha256": str(index), "raw_size": 100,
                        "headers": {"content-type": "application/json", "dd-telemetry-api-version": "v2",
                            "dd-telemetry-request-type": kind, "dd-client-library-language": "python", "dd-client-library-version": "4.15.5"},
                        "payload": {"document": document, "received_at": float(index * check.HEARTBEAT_INTERVAL)}})
    return records


class TelemetryAssertionsTest(unittest.TestCase):
    def test_controls_accept_all_observations(self):
        for dependencies in (True, False):
            records = fixture(dependencies)
            for function in (check.check_v2, check.check_startup, check.check_heartbeat, check.check_metrics, check.check_batch):
                function(records, IDENTITY)
            for function in (check.check_configuration, check.check_dependencies, check.check_extended):
                function(records, IDENTITY, dependencies)

    def test_envelope_and_startup_reject_wrong_identity_and_order(self):
        for field, value in [("runtime_id", "different"), ("seq_id", 99), ("api_version", "v1")]:
            records = fixture()
            records[0]["payload"]["document"][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                check.check_v2(records, IDENTITY)
        records = fixture()
        records[0]["payload"]["document"]["request_type"] = "app-heartbeat"
        with self.assertRaises(AssertionError):
            check.check_startup(records, IDENTITY)

    def test_config_requires_observed_origin_and_default_not_just_requested_value(self):
        for mutation in (lambda config: config[0].update(origin="default"), lambda config: config[0].update(value="wrong"),
                         lambda config: config.pop()):
            records = fixture()
            mutation(records[0]["payload"]["document"]["payload"]["configuration"])
            with self.assertRaises(AssertionError):
                check.check_configuration(records, IDENTITY, True)

    def test_metrics_reject_wrong_namespace_dimensions_and_counts(self):
        for key, value in [("namespace", "general"), ("tags", ["integration_name:other"]), ("points", [[100, 2]]),
                           ("type", "gauge"), ("common", False)]:
            records = fixture()
            series = records[1]["payload"]["document"]["payload"][1]["payload"]["series"]
            series[0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check.check_metrics(records, IDENTITY)

    def test_heartbeat_requires_timing_evidence(self):
        for interval in (0, 1, 10):
            records = fixture()
            for index, record in enumerate(records):
                record["payload"]["received_at"] = float(index * interval)
            with self.subTest(interval=interval), self.assertRaises(AssertionError):
                check.check_heartbeat(records, IDENTITY)

    def test_extended_and_dependency_controls_reject_omissions(self):
        records = fixture()
        for record in records[-2:]:
            record["payload"]["document"]["payload"]["configuration"] = []
        with self.assertRaises(AssertionError):
            check.check_extended(records, IDENTITY, True)
        with self.assertRaises(AssertionError):
            check.check_dependencies(fixture(), IDENTITY, False)
        with self.assertRaises(AssertionError):
            check.check_dependencies(fixture(False), IDENTITY, True)

    def test_forwarding_requires_real_backend_match_and_agent_headers(self):
        records = fixture()
        backend = []
        for original in records:
            forwarded = copy.deepcopy(original)
            forwarded["path"] = "/api/v2/apmtelemetry"
            forwarded["payload"] = forwarded["payload"]["document"]
            forwarded["headers"].update({"via": "trace-agent 7.83.1", "dd-agent-hostname": "telemetry-agent", "dd-agent-env": "telemetry-agent-env"})
            backend.append(forwarded)
        self.assertEqual(check.check_forwarding(records, backend, IDENTITY), len(records))
        with self.assertRaises(AssertionError):
            check.check_forwarding(records, [], IDENTITY)
        for mutation in (lambda row: row.update(raw_sha256="wrong"), lambda row: row.update(status=403),
                         lambda row: row["headers"].update(via="fake"), lambda row: row["payload"].update(api_version="v1")):
            bad = copy.deepcopy(backend)
            mutation(bad[0])
            with self.assertRaises(AssertionError):
                check.check_forwarding(records, bad, IDENTITY)


if __name__ == "__main__":
    unittest.main()
