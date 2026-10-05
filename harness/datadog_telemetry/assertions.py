"""Checks over observed SDK/Agent telemetry; expected values are lab controls."""
from collections import Counter
import json
import math
import uuid

TELEMETRY_PATH = "/telemetry/proxy/api/v2/apmtelemetry"


def events(document):
    """Keep the outer sequence and inner batch position without inventing IDs."""
    items = document["payload"] if document["request_type"] == "message-batch" else [document]
    assert isinstance(items, list) and items, document
    result = []
    for index, item in enumerate(items):
        assert isinstance(item, dict) and isinstance(item.get("request_type"), str), item
        assert item["request_type"] != "message-batch", "nested telemetry batch"
        payload = item.get("payload", {})
        assert isinstance(payload, dict), item
        result.append({"seq_id": document["seq_id"], "batch_index": index,
                       "request_type": item["request_type"], "payload": payload})
    return result


def observations(records, identity):
    selected = [record for record in records if record["path"] == TELEMETRY_PATH]
    assert selected, "no tracer telemetry"
    for record in selected:
        assert record["status"] == 200 and not record.get("error"), record
        document = record["payload"]["document"]
        assert document["runtime_id"] == identity["runtime_id"], document
        assert isinstance(document["seq_id"], int) and document["seq_id"] > 0, document
    selected.sort(key=lambda record: record["payload"]["document"]["seq_id"])
    sequences = [record["payload"]["document"]["seq_id"] for record in selected]
    assert sequences == list(range(1, len(sequences) + 1)), sequences
    return selected


def check_v2(records, identity):
    selected = observations(records, identity)
    uuid.UUID(identity["runtime_id"])
    for record in selected:
        document = record["payload"]["document"]
        headers = record["headers"]
        assert document["api_version"] == headers["dd-telemetry-api-version"] == "v2", record
        assert document["request_type"] == headers["dd-telemetry-request-type"], record
        assert headers["content-type"].split(";")[0] == "application/json", headers
        assert headers["dd-client-library-language"] == "python", headers
        assert headers["dd-client-library-version"] == identity["tracer_version"], headers
        assert isinstance(document["tracer_time"], int) and document["tracer_time"] > 0, document
        application = document["application"]
        expected = {"service_name": "telemetry-lab", "env": "telemetry-env", "service_version": "telemetry-version",
                    "language_name": "python", "language_version": identity["language_version"],
                    "tracer_version": identity["tracer_version"], "runtime_name": "CPython",
                    "runtime_version": identity["language_version"]}
        assert all(application.get(key) == value for key, value in expected.items()), application
        host = document["host"]
        assert all(isinstance(host.get(key), str) and host[key] for key in ("hostname", "os", "architecture", "kernel_name")), host
        assert host["os"] == "Linux" and host["architecture"] == "x86_64", host
        events(document)


def all_events(records, identity):
    return [event for record in observations(records, identity) for event in events(record["payload"]["document"])]


def check_startup(records, identity):
    observed = all_events(records, identity)
    startups = [event for event in observed if event["request_type"] == "app-started"]
    assert len(startups) == 1, startups
    lifecycle = [event for event in observed if event["request_type"] not in {"sketches", "generate-metrics", "logs", "distributions"}]
    assert lifecycle[0]["request_type"] == "app-started", lifecycle[0]
    assert startups[0]["payload"]["products"]["tracer"]["enabled"] is True, startups[0]
    assert startups[0]["payload"]["products"]["tracer"]["version"] == identity["tracer_version"], startups[0]


def check_configuration(records, identity, dependencies):
    startup, = [event for event in all_events(records, identity) if event["request_type"] == "app-started"]
    configuration = startup["payload"]["configuration"]
    expected = {"DD_ENV": "telemetry-env", "DD_SERVICE": "telemetry-lab", "DD_VERSION": "telemetry-version",
                "DD_TRACE_RATE_LIMIT": "7", "DD_TRACE_ENABLED": "true", "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true",
                "DD_TELEMETRY_HEARTBEAT_INTERVAL": "1.0", "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED": str(dependencies).lower()}
    for name, value in expected.items():
        found = [item for item in configuration if item["name"] == name and item["origin"] == "env_var"]
        assert len(found) == 1 and found[0]["value"] == value, (name, value, found)
    defaults = [item for item in configuration if item["name"] == "DD_TRACE_RATE_LIMIT" and item["origin"] == "default"]
    assert len(defaults) == 1 and defaults[0]["value"] == "100", defaults


def check_heartbeat(records, identity):
    times = [record["payload"]["received_at"] for record in observations(records, identity)
             if any(event["request_type"] == "app-heartbeat" for event in events(record["payload"]["document"]))]
    assert len(times) >= 5, times
    delays = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(math.isfinite(delay) and delay >= 0 for delay in delays), delays
    average = sum(delays) / len(delays)
    assert 0.75 < average < 1.5, (average, delays)
    return {"count": len(times), "averageDelaySeconds": average}


def check_metrics(records, identity):
    series = [series for event in all_events(records, identity) if event["request_type"] == "generate-metrics"
              for series in event["payload"]["series"]]
    counts = {}
    expected_dimensions = {"spans_created": {"integration_name:datadog"},
                           "spans_finished": {"integration_name:datadog"},
                           "trace_chunks_sent": {"src_library:libdatadog"}}
    for metric, tags in expected_dimensions.items():
        matching = [item for item in series if item["metric"] == metric and item["namespace"] == "tracers"]
        assert matching, metric
        for item in matching:
            assert item["common"] is True and item["type"] == "count", item
            assert set(item["tags"]) == tags and len(item["tags"]) == len(tags), item
            assert item["points"], item
            for point in item["points"]:
                assert len(point) == 2 and isinstance(point[0], int) and point[0] > 0, point
                assert isinstance(point[1], (int, float)) and math.isfinite(point[1]) and point[1] >= 0, point
        total = sum(point[1] for item in matching for point in item["points"])
        assert total == len(identity["spans"]) == 3, (metric, total)
        counts[metric] = total
    return counts


def check_batch(records, identity):
    batches = [record["payload"]["document"] for record in observations(records, identity)
               if record["payload"]["document"]["request_type"] == "message-batch"]
    assert batches and any(len(batch["payload"]) >= 2 for batch in batches), batches
    for batch in batches:
        flattened = events(batch)
        for index, (item, event) in enumerate(zip(batch["payload"], flattened)):
            assert event["seq_id"] == batch["seq_id"] and event["batch_index"] == index, event
            assert event["request_type"] == item["request_type"] and event["payload"] == item.get("payload", {}), event
    assert any(event["request_type"] == "app-integrations-change" for batch in batches for event in events(batch)), batches


def check_dependencies(records, identity, enabled):
    observed = all_events(records, identity)
    loaded = [event for event in observed if event["request_type"] == "app-dependencies-loaded"]
    if enabled:
        dependencies = [dependency for event in loaded for dependency in event["payload"]["dependencies"]]
        wanted = identity["dependency"]
        matches = [dependency for dependency in dependencies if dependency["name"] == wanted["name"]]
        assert len(matches) == 1 and matches[0]["version"] == wanted["version"], matches
        counts = Counter(dependency["name"] for dependency in dependencies)
        assert all(count == 1 for count in counts.values()), counts
    else:
        assert not loaded, loaded
        assert all(not event["payload"].get("dependencies") for event in observed), observed


def check_extended(records, identity, dependencies):
    observed = all_events(records, identity)
    extended = [event for event in observed if event["request_type"] == "app-extended-heartbeat"]
    assert len(extended) >= 2, extended
    configuration = [config for event in observed if event["request_type"] in ("app-started", "app-client-configuration-change")
                     for config in event["payload"].get("configuration", [])]
    expected = {json.dumps({key: config[key] for key in ("name", "value", "origin")}, sort_keys=True) for config in configuration}
    actual = {json.dumps({key: config[key] for key in ("name", "value", "origin")}, sort_keys=True)
              for event in extended for config in event["payload"]["configuration"]}
    assert expected and expected <= actual, expected - actual
    for event in extended:
        payload = event["payload"]
        assert all(key in payload for key in ("configuration", "dependencies", "integrations")), payload
        integration = [item for item in payload["integrations"] if item["name"] == "aiohttp"]
        assert len(integration) == 1 and integration[0]["enabled"] is True, integration
        assert integration[0]["version"] == identity["dependency"]["version"], integration
        matches = [item for item in payload["dependencies"] if item["name"] == "aiohttp"]
        if dependencies:
            assert len(matches) == 1 and matches[0]["version"] == identity["dependency"]["version"], matches
        else:
            assert payload["dependencies"] == [], payload


def check_forwarding(records, backend_records, identity):
    tracer = observations(records, identity)
    forwarded = [record for record in backend_records if record["path"] == "/api/v2/apmtelemetry"
                 and (record.get("payload") or {}).get("runtime_id") == identity["runtime_id"]]
    by_sequence = {record["payload"]["seq_id"]: record for record in forwarded}
    assert len(by_sequence) == len(forwarded) == len(tracer), (len(by_sequence), len(forwarded), len(tracer))
    for original in tracer:
        document = original["payload"]["document"]
        actual = by_sequence[document["seq_id"]]
        assert actual["status"] == 200 and not actual.get("error"), actual
        assert actual["payload"] == document, (document["seq_id"], actual["payload"])
        assert actual["raw_sha256"] == original["raw_sha256"] and actual["raw_size"] == original["raw_size"], actual
        headers = actual["headers"]
        assert headers["via"] == "trace-agent 7.83.1", headers
        assert headers["dd-agent-hostname"] == "telemetry-agent", headers
        assert headers["dd-agent-env"] == "telemetry-agent-env", headers
        assert "via" not in original["headers"] and "dd-agent-hostname" not in original["headers"], original
        for key in ("dd-telemetry-request-type", "dd-telemetry-api-version", "dd-client-library-language", "dd-client-library-version"):
            assert headers[key] == original["headers"][key], (key, headers)
    return len(forwarded)
