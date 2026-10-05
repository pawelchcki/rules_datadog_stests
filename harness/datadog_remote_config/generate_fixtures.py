"""Regenerate signed test metadata with OpenSSL; not a test-time dependency.

Matches DataDog/system-tests 098fe096 utils/proxy/tuf.py and
rc_response_builder.py (Apache-2.0): intentionally public zero-seed test key.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

KEYID = "139e3940e64b5491722088d9a0d741628fc826e09475d341a780acde3c4b8070"
PUBLIC = "3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29"
EXPIRES = "3000-01-01T00:00:00Z"
PATH = "datadog/2/APM_TRACING/lab-dynamic/config"


def encode(document):
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def generate(out, configurations=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        key = root / "key.der"
        key.write_bytes(bytes.fromhex("302e020100300506032b657004220420") + bytes(32))
        def sign(content):
            message = root / "message"
            message.write_bytes(encode(content))
            signature = subprocess.check_output(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-keyform", "DER", "-in", str(message)])
            return {"signed": content, "signatures": [{"keyid": KEYID, "sig": signature.hex()}]}
        trust = sign({"_type": "root", "consistent_snapshot": True, "expires": EXPIRES,
            "keys": {KEYID: {"keyid_hash_algorithms": ["sha256"], "keytype": "ed25519", "keyval": {"public": PUBLIC}, "scheme": "ed25519"}},
            "roles": {role: {"keyids": [KEYID], "threshold": 1} for role in ("root", "snapshot", "targets", "timestamp")},
            "spec_version": "1.0", "version": 1})
        stages = []
        if configurations is None:
            configurations = []
            for version, settings in [(1, None), (2, {"tracing_sampling_rate": 0.0, "tracing_tags": ["rc_tag:remote-value"], "log_injection_enabled": True, "tracing_header_tags": [{"header": "x-rc-header", "tag_name": "rc.header"}]}), (3, {})]:
                configurations.append({} if settings is None else {PATH: {"action": "enable", "revision": version, "service_target": {"service": "rc-lab", "env": "rc-env"}, "lib_config": settings}})
        for version, configs in enumerate(configurations, 1):
            targets = sign({"_type": "targets", "custom": {"opaque_backend_state": "eyJmb28iOiAiYmFyIn0="}, "expires": EXPIRES,
                "spec_version": "1.0", "version": version, "targets": {path: {"custom": {"v": version}, "length": len(encode(content)),
                 "hashes": {"sha256": hashlib.sha256(encode(content)).hexdigest()}} for path, content in configs.items()}})
            def reference(document, name):
                content = encode(document)
                return {name: {"hashes": {"sha256": hashlib.sha256(content).hexdigest()}, "length": len(content), "version": version}}
            snapshot = sign({"_type": "snapshot", "expires": EXPIRES, "spec_version": "1.0", "version": version, "meta": reference(targets, "targets.json")})
            timestamp = sign({"_type": "timestamp", "expires": EXPIRES, "spec_version": "1.0", "version": version, "meta": reference(snapshot, "snapshot.json")})
            stages.append({"version": version, "targets": targets, "snapshot": snapshot, "timestamp": timestamp, "files": configs})
        out.write_text(json.dumps({"root": trust, "stages": stages}, indent=2) + "\n")


if __name__ == "__main__":
    generate(Path(__file__).with_name("signed-fixtures.json"))
