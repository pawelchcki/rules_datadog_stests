"""Regenerate public signed RC stages; OpenSSL is build-time only."""
from pathlib import Path
from harness.datadog_remote_config.generate_fixtures import generate
root = Path(__file__).parent
apm_path = "datadog/2/APM_TRACING/extra/config"
span = {"id": "extra-span", "version": 1, "language": "python", "type": "SPAN_PROBE", "where": {"typeName": "target", "methodName": "calculate"}}
decoration = {"id": "extra-decoration", "version": 1, "language": "python", "type": "SPAN_DECORATION_PROBE", "where": {"typeName": "target", "methodName": "calculate"}, "targetSpan": "ROOT", "decorations": [{"tags": [{"name": "extra.decoration", "value": {"segments": [{"str": "signed-rc"}]}}]}]}
log = {"id": "extra-log", "version": 1, "language": "python", "type": "LOG_PROBE", "where": {"typeName": "target", "methodName": "calculate"}, "captureSnapshot": True, "sampling": {"snapshotsPerSecond": 100}}
probes = {"datadog/2/LIVE_DEBUGGING/" + p["id"] + "/config": p for p in (span, decoration, log)}
def stage(version, settings, extras=None):
    return {**probes, apm_path: {"action": "enable", "revision": version + 1, "service_target": {"service": "rc-lab", "env": "rc-env"}, "lib_config": settings}, **(extras or {})}
sym = {"datadog/2/LIVE_DEBUGGING_SYMBOL_DB/extra/config": {"upload_symbols": True}}
flare = {"datadog/2/AGENT_TASK/extra/config": {"uuid": "ea0d5d20d11d4adeb394f7bf37a1aafd", "task_type": "tracer_flare", "args": {"case_id": "12345", "hostname": "rc-agent", "user_handle": "fixture@example.test"}}}
configurations = [{}, stage(1, {"dynamic_instrumentation_enabled": False, "exception_replay_enabled": False, "code_origin_enabled": False}), stage(2, {"dynamic_instrumentation_enabled": True, "exception_replay_enabled": True, "code_origin_enabled": True, "tracing_sampling_rate": 1.0}, sym), stage(3, {}, sym), stage(4, {"dynamic_instrumentation_enabled": False, "exception_replay_enabled": False, "code_origin_enabled": False}, {"datadog/2/LIVE_DEBUGGING_SYMBOL_DB/extra/config": {"upload_symbols": False}}), stage(5, {"dynamic_instrumentation_enabled": False, "code_origin_enabled": False}, flare)]
import copy
configurations.insert(3, copy.deepcopy(configurations[2]))
for version, configuration in enumerate(configurations, 1):
    if apm_path in configuration:
        configuration[apm_path]["revision"] = version
generate(root / "signed-fixtures.json", configurations)
