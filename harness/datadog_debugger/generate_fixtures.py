"""Sign debugger probes with the upstream public RC test trust root."""
from pathlib import Path
from harness.datadog_remote_config.generate_fixtures import generate

ROOT = Path(__file__).parent
source = (ROOT / "probe_target.py").read_text().splitlines()
line = lambda marker: str(next(i for i, text in enumerate(source, 1) if marker in text))


def probe(name, where, **extra):
    result = {"id": name, "version": 1, "language": "python", "type": "LOG_PROBE", "where": where,
              "captureSnapshot": True, "sampling": {"snapshotsPerSecond": 1000}, "tags": ["fixture:local"],
              "capture": {"maxReferenceDepth": 3, "maxCollectionSize": 20, "maxLength": 255, "maxFieldCount": 20}}
    result.update(extra)
    return result


probes = [
    probe("method-probe", {"typeName": "probe_target", "methodName": "calculate"}, evaluateAt="EXIT"),
    probe("line-probe", {"sourceFile": "probe_target.py", "lines": [line("LINE_PROBE")]}),
    probe("expression-probe", {"typeName": "probe_target", "methodName": "calculate"}, evaluateAt="ENTRY", captureSnapshot=False,
          template="value={value}", segments=[{"str": "value="}, {"dsl": "value", "json": {"ref": "value"}}],
          when={"dsl": "value > 2", "json": {"gt": [{"ref": "value"}, 2]}}),
    probe("budget-probe", {"sourceFile": "probe_target.py", "lines": [line("BUDGET_PROBE")]}, sampling={"snapshotsPerSecond": 1}),
]
if __name__ == "__main__":
    generate(ROOT / "signed-fixtures.json", [{}, {"datadog/2/LIVE_DEBUGGING/" + p["id"] + "/config": p for p in probes}, {}])
