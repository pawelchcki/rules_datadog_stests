"""Assert runtime effects and protocol state from independently retained evidence."""
import re


def check_client(requests, ready):
    assert requests, "no native RC polls"
    ids = set()
    for request in requests:
        client = request["client"]
        tracer = client["client_tracer"]
        assert client["is_tracer"], client
        if client["state"]["targets_version"] >= 2:
            assert "APM_TRACING" in client["products"], client
        assert tracer["runtime_id"] == ready["runtime_id"] and tracer["language"] == "python", tracer
        assert tracer["service"] == "rc-lab" and tracer["env"] == "rc-env" and tracer["app_version"] == "rc-version", tracer
        ids.add(client["id"])
    assert len(ids) == 1


def check_version(requests, ready):
    check_client(requests, ready)
    for request in requests:
        value = request["client"]["client_tracer"]["tracer_version"]
        assert value == ready["tracer_version"] == "4.14.0", value
        assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", value), value


def check_protocol(requests, ready, backend):
    check_client(requests, ready)
    for version in (2, 3):
        acknowledged = [r["client"]["state"] for r in requests if r["client"]["state"]["targets_version"] == version]
        assert acknowledged, version
        assert any(c == {"id": "lab-dynamic", "version": version, "product": "APM_TRACING", "apply_state": 2, "apply_error": ""}
                   for state in acknowledged for c in state["config_states"]), acknowledged
        assert all(not state["has_error"] and not state["error"] for state in acknowledged), acknowledged
    capabilities = int.from_bytes(bytes(requests[-1]["client"]["capabilities"]), "big")
    assert all(capabilities & (1 << bit) for bit in (12, 13, 14, 15)), capabilities
    polls = [r["payload"]["request"] for r in backend if r["path"] == "/api/v0.1/configurations"]
    assert polls and all(p["agentVersion"] == "7.83.1" and p["hostname"] == "rc-agent" and not p.get("has_error") for p in polls), polls
    clients = [c for p in polls for c in p.get("active_clients", []) if c.get("is_tracer")]
    assert any(c["client_tracer"]["runtime_id"] == ready["runtime_id"] and c["state"].get("targets_version") == 3 for c in clients), clients


def roots(spans, identities):
    result = {}
    for identity in identities:
        stage = identity["stage"]
        matches = [s for s in spans if s["span_id"] == int(identity["span_id"])]
        assert len(matches) == 1, (identity, matches)
        span = matches[0]
        assert span["trace_id"] == int(identity["trace_id"]) & ((1 << 64) - 1), span
        assert span["name"] == "rc.lab.stage" and span["resource"] == "stage-" + str(stage), span
        assert span["start"] > 0 and span["duration"] > 0, span
        result[stage] = span
    assert set(result) == {1, 2, 3}
    return result


def check_dynamic(spans, identities):
    by_stage = roots(spans, identities)
    for stage, root in by_stage.items():
        traces = [s for s in spans if s["trace_id"] == root["trace_id"]]
        for span in traces:
            meta = span["meta"]
            if stage == 2:
                assert meta["rc_tag"] == "remote-value" and "local_tag" not in meta, span
            else:
                assert meta["local_tag"] == "local-value" and "rc_tag" not in meta, span
        http = [s for s in traces if s["meta"].get("component") == "aiohttp_client"]
        assert len(http) == 1, traces
        if stage == 2:
            assert http[0]["meta"].get("rc.header") == "header-value", http
        else:
            assert "rc.header" not in http[0]["meta"], http
        identity = next(i for i in identities if i["stage"] == stage)
        if stage == 2:
            assert identity["log"] == {"dd.trace_id": format(int(identity["trace_id"]), "032x"), "dd.span_id": identity["span_id"],
                                       "dd.service": "rc-lab", "dd.env": "rc-env", "dd.version": "rc-version"}, identity
        else:
            assert identity["log"] == {}, identity


def check_sampling(spans, identities, agent_spans):
    by_stage = roots(spans, identities)
    for stage, root in by_stage.items():
        native = [s for s in spans if s["trace_id"] == root["trace_id"]]
        exported = [s for s in agent_spans if s["trace_id"] == root["trace_id"]]
        if stage == 2:
            assert root["metrics"]["_sampling_priority_v1"] == -1 and root["metrics"]["_dd.rule_psr"] == 0, root
            assert not exported, exported
        else:
            assert root["metrics"]["_sampling_priority_v1"] > 0, root
            assert {s["span_id"] for s in exported} == {s["span_id"] for s in native}, (exported, native)
            for actual in exported:
                original = next(s for s in native if s["span_id"] == actual["span_id"])
                for key in ("trace_id", "parent_id", "resource", "start", "duration"):
                    assert actual.get(key, 0) == original.get(key, 0), (key, actual, original)
                assert actual["meta"]["local_tag"] == "local-value" and "rc_tag" not in actual["meta"], actual
