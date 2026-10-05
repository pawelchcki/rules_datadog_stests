"""Regenerate public test metadata offline; OpenSSL is not used by tests.

The first four signed responses are pinned system-tests fixtures. The last two are
signed with the existing harness's intentionally public zero-seed test key and
enable AppSec, initialize its WAF, and add remote trace-tagging rules after activation/deactivation/removal.
"""
import base64
import json
from pathlib import Path
import tempfile

from harness.datadog_remote_config.generate_fixtures import encode, generate


def main():
    root = Path(__file__).parent
    responses = json.loads((root / "onboarding-responses.json").read_text())[:4]
    configs = [{}, {}, {}, {}, {"datadog/2/ASM_FEATURES/ASM_FEATURES-base/config": {"asm": {"enabled": True}}}, {
        "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config": {"asm": {"enabled": True}},
        "datadog/2/ASM_DD/security-tagging/config": json.loads((root / "tagging-rules.json").read_text()),
    }]
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "signed.json"
        generate(target, configs)
        stages = json.loads(target.read_text())["stages"][-2:]
    b64 = lambda value: base64.b64encode(encode(value)).decode()
    for stage in stages:
        responses.append({"targets": b64(stage["targets"]),
                      "target_files": [{"path": path, "raw": b64(body)} for path, body in stage["files"].items()],
                      "client_configs": list(stage["files"])})
    (root / "onboarding-tagging-responses.json").write_text(json.dumps(responses, indent=2) + "\n")


if __name__ == "__main__":
    main()
