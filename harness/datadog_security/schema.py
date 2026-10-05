"""Validate the exact keywords used by the pinned upstream IAST JSON schema."""
import json
from pathlib import Path

SCHEMA = json.loads(Path(__file__).with_name("iast-schema.json").read_text())
KEYWORDS = {"$schema", "description", "title", "definitions", "$ref", "type", "properties",
            "items", "enum", "const", "additionalProperties", "required", "oneOf", "minimum"}


def validate(value, schema=None, root=None):
    schema = SCHEMA if schema is None else schema
    root = SCHEMA if root is None else root
    assert not set(schema) - KEYWORDS, "Unsupported pinned schema keyword"
    if "$ref" in schema:
        target = root
        assert schema["$ref"].startswith("#/"), schema
        for part in schema["$ref"][2:].split("/"):
            target = target[part]
        return validate(value, target, root)
    if "oneOf" in schema:
        matched = 0
        for alternative in schema["oneOf"]:
            try:
                validate(value, alternative, root)
            except AssertionError:
                continue
            matched += 1
        assert matched == 1, ("oneOf", value, schema)
    kind = schema.get("type")
    if kind:
        valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
                 "string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool)}
        assert kind in valid and valid[kind], (kind, value)
    if "enum" in schema:
        assert value in schema["enum"], value
    if "const" in schema:
        assert type(value) is type(schema["const"]) and value == schema["const"], value
    if "minimum" in schema:
        assert value >= schema["minimum"], value
    if isinstance(value, dict):
        assert set(schema.get("required", [])) <= value.keys(), value
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert value.keys() <= properties.keys(), value
        for key, child in value.items():
            if key in properties:
                validate(child, properties[key], root)
    if isinstance(value, list) and "items" in schema:
        for child in value:
            validate(child, schema["items"], root)
