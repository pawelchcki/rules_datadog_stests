"""Assertions over real Agent-forwarded debugger snapshots and diagnostics."""
import math
import uuid

PROBES = {"method-probe", "line-probe", "expression-probe", "budget-probe"}
SECRET = "local-password-do-not-capture"


def snapshots(events, identities):
    found = {}
    control = next(i for i in identities if i["stage"] == 2)
    for event in events:
        snapshot = event.get("debugger", {}).get("snapshot")
        if snapshot is None:
            continue
        probe = snapshot["probe"]
        assert probe["id"] in PROBES and probe["version"] == 1, snapshot
        uuid.UUID(snapshot["id"])
        assert snapshot["language"] == "python" and snapshot["timestamp"] > 0, snapshot
        assert snapshot["evaluationErrors"] == [], snapshot
        assert event["dd"] == {"trace_id": format(int(control["trace_id"]), "032x"), "span_id": control["span_id"]}, event
        assert event["service"] == "rc-lab" and event["ddsource"] == "dd_debugger", event
        found.setdefault(probe["id"], []).append(event)
    assert set(found) == PROBES, found
    return found


def check_method(events, identities):
    rows = snapshots(events, identities)["method-probe"]
    assert len(rows) == 2, rows
    values = set()
    for row in rows:
        snapshot = row["debugger"]["snapshot"]
        assert snapshot["probe"]["location"] == {"type": "probe_target", "method": "calculate"}, snapshot
        context = snapshot["captures"]["return"]
        value = int(context["arguments"]["value"]["value"])
        values.add(value)
        assert context["locals"]["doubled"] == {"type": "int", "value": str(value * 2)}, context
        assert context["locals"]["@return"] == {"type": "str", "value": repr("result:" + str(value * 2))}, context
    assert values == {1, 3}, rows


def check_line(events, identities):
    rows = snapshots(events, identities)["line-probe"]
    assert len(rows) == 2, rows
    values = set()
    for row in rows:
        snapshot = row["debugger"]["snapshot"]
        location = snapshot["probe"]["location"]
        assert location["file"].endswith("/probe_target.py") and location["lines"] == ["5"], location
        context = snapshot["captures"]["lines"]["5"]
        value = int(context["arguments"]["value"]["value"])
        values.add(value)
        assert context["locals"]["doubled"] == {"type": "int", "value": str(value * 2)}, context
        assert snapshot["stack"][0]["function"] == "calculate" and snapshot["stack"][0]["lineNumber"] == 5, snapshot
    assert values == {1, 3}, rows


def check_expressions(events, identities):
    rows = snapshots(events, identities)["expression-probe"]
    assert len(rows) == 1 and rows[0]["message"] == "value=3", rows
    assert rows[0]["debugger"]["snapshot"]["captures"] == {}, rows
    assert all(i["result"] == ["result:2", "result:6"] for i in identities), identities


def check_redaction(events, identities):
    found = snapshots(events, identities)
    for name in ("method-probe", "line-probe"):
        for event in found[name]:
            captures = event["debugger"]["snapshot"]["captures"]
            context = captures["return"] if name == "method-probe" else captures["lines"]["5"]
            assert context["arguments"]["password"] == {"type": "str", "notCapturedReason": "redactedIdent"}, context


def check_budgets(events, identities):
    rows = snapshots(events, identities)["budget-probe"]
    control = next(i for i in identities if i["stage"] == 2)
    assert control["budget_calls"] == 150, identities
    elapsed = control["budget_elapsed_seconds"]
    assert math.isfinite(elapsed) and elapsed >= 0, control
    # The pinned SDK RateLimitMixin starts with one token; its jittered refill
    # rate is strictly below 1.5 * snapshotsPerSecond (configured as one).
    assert 1 <= len(rows) <= 1 + math.floor(1.5 * elapsed), (len(rows), elapsed)
    assert len(rows) < control["budget_calls"], (len(rows), control)
    for row in rows:
        value = int(row["debugger"]["snapshot"]["captures"]["lines"]["10"]["arguments"]["value"]["value"])
        assert 0 <= value < 150, row


CHECKS = [
    ("debugger_method_probe", check_method, "test_debugger_probe_snapshot.py"),
    ("debugger_line_probe", check_line, "test_debugger_probe_snapshot.py"),
    ("debugger_expression_language", check_expressions, "test_debugger_expression_language.py"),
    ("debugger_pii_redaction", check_redaction, "test_debugger_pii.py"),
    ("debugger_probe_budgets", check_budgets, "test_debugger_probe_budgets.py"),
]
