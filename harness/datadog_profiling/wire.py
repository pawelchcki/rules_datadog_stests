"""Bounded multipart and pprof observations for actual SDK profile uploads."""
from email.parser import BytesParser
from email.policy import default
import hashlib
import json

from harness.datadog_backend.backend import MAX_BODY, decompress
from harness.datadog_backend.wire import Reader


def fields(body):
    reader = Reader(body)
    count = 0
    while reader.pos < len(body):
        count += 1
        if count > 262144:
            raise ValueError("profile node budget exceeded")
        key = reader.varint()
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError("profile field zero")
        if wire == 0:
            value = reader.varint()
        elif wire == 1:
            value = reader.take(8)
        elif wire == 5:
            value = reader.take(4)
        elif wire == 2:
            value = reader.take(reader.varint())
        else:
            raise ValueError("unsupported profile wire type")
        yield number, wire, value


def pprof(body):
    encoding = "gzip" if body.startswith(b"\x1f\x8b") else "zstd" if body.startswith(b"\x28\xb5\x2f\xfd") else "identity"
    body = decompress(body, encoding)
    strings, samples, functions, locations = [], [], {}, {}
    for number, wire, raw in fields(body):
        if number in (2, 4, 5, 6) and wire != 2:
            raise ValueError("wrong pprof field type")
        if number == 6:
            strings.append(raw.decode("utf-8"))
        elif number == 2:
            ids = []
            for key, kind, value in fields(raw):
                if key == 1:
                    if kind == 0:
                        ids.append(value)
                    elif kind == 2:
                        packed = Reader(value)
                        while packed.pos < len(value):
                            ids.append(packed.varint())
                    else:
                        raise ValueError("invalid sampled stack ids")
            if ids:
                samples.append(ids)
        elif number == 5:
            function = {key: value for key, kind, value in fields(raw) if kind == 0}
            if function.get(1):
                functions[function[1]] = function.get(2, 0)
        elif number == 4:
            location_id, names = None, []
            for key, kind, value in fields(raw):
                if key == 1 and kind == 0:
                    location_id = value
                elif key == 4 and kind == 2:
                    names.extend(item for field, kind, item in fields(value) if field == 1 and kind == 0)
            if location_id:
                locations[location_id] = names
    if not samples or not strings or strings[0] != "":
        raise ValueError("profile has no sampled stacks/string table")
    sampled_names = set()
    for stack in samples:
        for location in stack:
            if location not in locations:
                raise ValueError("sample references missing pprof location")
            for function in locations[location]:
                if function not in functions or functions[function] >= len(strings):
                    raise ValueError("sample references missing pprof function/name")
                sampled_names.add(strings[functions[function]])
    return {"samples": len(samples), "strings": strings, "sampledFunctions": sorted(sampled_names), "decodedSize": len(body)}


def multipart(headers, body):
    if len(body) > MAX_BODY:
        raise ValueError("profile upload exceeds limit")
    content_type = headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;") or "\r" in content_type or "\n" in content_type:
        raise ValueError("profile requires multipart form data")
    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart() or message.defects:
        raise ValueError("invalid profile multipart")
    parts, event = [], None
    for part in message.iter_parts():
        if len(parts) >= 16 or part.is_multipart() or part.defects:
            raise ValueError("profile part budget/schema violation")
        data = part.get_payload(decode=True)
        if data is None:
            raise ValueError("profile part lacks bytes")
        name = part.get_param("name", header="content-disposition")
        if not name or any(item["name"] == name for item in parts):
            raise ValueError("missing or duplicate profile part")
        record = {"name": name, "filename": part.get_filename(), "contentType": part.get_content_type(),
                  "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if name == "event":
            event = json.loads(data)
        elif "pprof" in name or "pprof" in (part.get_filename() or ""):
            record["profile"] = pprof(data)
        parts.append(record)
    if not isinstance(event, dict) or not any("profile" in part for part in parts):
        raise ValueError("profile upload needs event and sampled pprof")
    return {"event": event, "parts": parts}
