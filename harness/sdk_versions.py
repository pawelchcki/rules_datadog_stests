"""Actual current SDK pins; historical expected-failure receipts remain separate."""
import json
from pathlib import Path
import re


def versions(path=None):
    document=json.loads((path or Path(__file__).resolve().parents[1]/"bazel/datadog_sdk_versions.json").read_text())
    if set(document)!={"ruby","python","go"} or any(not isinstance(value,str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",value) for value in document.values()):
        raise ValueError("invalid SDK version pins")
    return document


VERSIONS=versions()
