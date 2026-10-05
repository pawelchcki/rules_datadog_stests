"""Bounded decoders for Agent intake protobuf and MessagePack stats.

Field numbers and types come from the pinned upstream descriptor in schema.json.
Unknown protobuf fields are skipped as required by the protocol. Unsupported
MessagePack extensions are rejected rather than silently losing their contents.
"""
import base64
import json
from pathlib import Path
import struct

SCHEMA = json.loads(Path(__file__).with_name("schema.json").read_text())["messages"]
MAX_DEPTH = 64


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, length):
        if length < 0 or self.pos + length > len(self.data):
            raise ValueError("truncated payload")
        result = self.data[self.pos:self.pos + length]
        self.pos += length
        return result

    def varint(self):
        result = 0
        for shift in range(0, 70, 7):
            byte = self.take(1)[0]
            if shift == 63 and byte > 1:
                raise ValueError("protobuf varint overflow")
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError("protobuf varint overflow")


def scalar(raw, kind):
    if kind in (1, 2, 6, 7, 15, 16):
        return struct.unpack({1: "<d", 2: "<f", 6: "<Q", 7: "<I", 15: "<i", 16: "<q"}[kind], raw)[0]
    if kind == 8:
        return bool(raw)
    if kind in (3, 5, 14):
        bits = 64 if kind == 3 else 32
        value = raw & ((1 << bits) - 1)
        return value - (1 << bits) if value & (1 << (bits - 1)) else value
    if kind in (17, 18):
        return (raw >> 1) ^ -(raw & 1)
    return raw


def protobuf(data, message=".datadog.trace.AgentPayload", depth=0, catalog=None):
    if depth > MAX_DEPTH:
        raise ValueError("protobuf nesting limit")
    catalog = SCHEMA if catalog is None else catalog
    schema = catalog[message]
    fields = schema["fields"]
    result = {}
    reader = Reader(data)
    while reader.pos < len(data):
        key = reader.varint()
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError("protobuf field zero")
        if wire == 0:
            raw = reader.varint()
        elif wire == 1:
            raw = reader.take(8)
        elif wire == 2:
            raw = reader.take(reader.varint())
        elif wire == 5:
            raw = reader.take(4)
        else:
            raise ValueError("unsupported protobuf wire type")
        spec = fields.get(str(number))
        if spec is None:
            continue
        kind = spec["type"]
        expected = 2 if kind in (9, 11, 12) else 1 if kind in (1, 6, 16) else 5 if kind in (2, 7, 15) else 0
        if spec["repeated"] and expected != 2 and wire == 2:
            # Proto3 numeric repeated fields accept packed and unpacked forms.
            packed = Reader(raw)
            while packed.pos < len(raw):
                item = packed.take(8 if expected == 1 else 4) if expected in (1, 5) else packed.varint()
                result.setdefault(spec["name"], []).append(scalar(item, kind))
            continue
        if wire != expected:
            raise ValueError("incorrect protobuf wire type for " + spec["name"])
        if kind == 11:
            value = protobuf(raw, spec["message"], depth + 1, catalog)
        elif kind == 9:
            value = raw.decode("utf-8")
        elif kind == 12:
            value = {"base64": base64.b64encode(raw).decode("ascii")}
        else:
            value = scalar(raw, kind)
        name = spec["name"]
        if spec["repeated"] and kind == 11 and catalog[spec["message"]]["map"]:
            # Proto3 map entries may omit either default-valued field.
            entry = catalog[spec["message"]]["fields"]
            default_key = "" if entry["1"]["type"] == 9 else 0
            default_value = "" if entry["2"]["type"] == 9 else {} if entry["2"]["type"] == 11 else 0
            result.setdefault(name, {})[value.get("key", default_key)] = value.get("value", default_value)
        elif spec["repeated"]:
            result.setdefault(name, []).append(value)
        else:
            result[name] = value
    return result


def msgpack(data):
    reader = Reader(data)

    def number(fmt):
        return struct.unpack(fmt, reader.take(struct.calcsize(fmt)))[0]

    def unpack(depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("MessagePack nesting limit")
        tag = reader.take(1)[0]
        if tag < 0x80:
            return tag
        if tag >= 0xe0:
            return tag - 256
        if 0xa0 <= tag <= 0xbf:
            return reader.take(tag & 31).decode("utf-8")
        if tag == 0xc0:
            return None
        if tag in (0xc2, 0xc3):
            return tag == 0xc3
        formats = {0xca: ">f", 0xcb: ">d", 0xcc: ">B", 0xcd: ">H", 0xce: ">I", 0xcf: ">Q", 0xd0: ">b", 0xd1: ">h", 0xd2: ">i", 0xd3: ">q"}
        if tag in formats:
            return number(formats[tag])
        lengths = {0xc4: ">B", 0xc5: ">H", 0xc6: ">I", 0xd9: ">B", 0xda: ">H", 0xdb: ">I", 0xdc: ">H", 0xdd: ">I", 0xde: ">H", 0xdf: ">I"}
        if tag in lengths:
            length = number(lengths[tag])
        elif 0x80 <= tag <= 0x9f:
            length = tag & 15
        else:
            raise ValueError("unsupported MessagePack tag: " + hex(tag))
        if tag in (0xc4, 0xc5, 0xc6):
            return {"base64": base64.b64encode(reader.take(length)).decode("ascii")}
        if tag in (0xd9, 0xda, 0xdb):
            return reader.take(length).decode("utf-8")
        if 0x90 <= tag <= 0x9f or tag in (0xdc, 0xdd):
            return [unpack(depth + 1) for _ in range(length)]
        result = {}
        for _ in range(length):
            key = unpack(depth + 1)
            if not isinstance(key, (str, int)) or key in result:
                raise ValueError("invalid or duplicate MessagePack map key")
            result[key] = unpack(depth + 1)
        return result

    result = unpack()
    if reader.pos != len(data):
        raise ValueError("trailing MessagePack data")
    return result


def trace_chunks(payload):
    """Return legacy and indexed Agent chunks in a common assertion format.

    The original protobuf representation remains in the stored request. Indexed
    traces carry a 128-bit ID on the chunk; canonical spans use its low 64 bits
    plus the standard _dd.p.tid tag so they match tracer v0.4/v0.5 captures.
    """
    chunks = [chunk for tracer in payload.get("tracer_payloads", [])
              for chunk in tracer.get("chunks", [])]
    for tracer in payload.get("idx_tracer_payloads", []):
        strings = tracer.get("strings", [])

        def string(index):
            if not isinstance(index, int) or index < 0 or index >= len(strings):
                raise ValueError("indexed trace string reference out of bounds")
            return strings[index]

        def value(item):
            if "string_value_ref" in item:
                return string(item["string_value_ref"])
            for key in ("bool_value", "double_value", "int_value", "bytes_value"):
                if key in item:
                    return item[key]
            if "array_value" in item:
                return [value(entry) for entry in item["array_value"].get("values", [])]
            if "key_value_list" in item:
                return {string(entry.get("key", 0)): value(entry.get("value", {}))
                        for entry in item["key_value_list"].get("key_values", [])}
            raise ValueError("indexed trace attribute has no supported value")

        def attributes(mapping):
            return {string(int(index)): value(item) for index, item in mapping.items()}

        for raw_chunk in tracer.get("chunks", []):
            trace_bytes = base64.b64decode(raw_chunk.get("trace_id", {}).get("base64", ""), validate=True)
            if len(trace_bytes) != 16:
                raise ValueError("indexed chunk requires 16-byte trace ID")
            trace_id = int.from_bytes(trace_bytes, "big")
            chunk = {key: item for key, item in raw_chunk.items()
                     if key not in ("spans", "origin_ref", "attributes", "trace_id")}
            chunk["origin"] = string(raw_chunk.get("origin_ref", 0))
            chunk["tags"] = attributes(raw_chunk.get("attributes", {}))
            spans = []
            for raw_span in raw_chunk.get("spans", []):
                span = {key: item for key, item in raw_span.items()
                        if not key.endswith("_ref") and key != "attributes"}
                for name in ("service", "name", "resource", "type", "env", "version", "component"):
                    span[name] = string(raw_span.get(name + "_ref", 0))
                attrs = attributes(raw_span.get("attributes", {}))
                span["meta"] = {key: item for key, item in attrs.items() if isinstance(item, str)}
                span["metrics"] = {key: item for key, item in attrs.items() if isinstance(item, (int, float)) and not isinstance(item, bool)}
                span["attributes"] = attrs
                span["trace_id"] = trace_id & ((1 << 64) - 1)
                if trace_id >> 64:
                    span["meta"]["_dd.p.tid"] = f"{trace_id >> 64:016x}"
                spans.append(span)
            chunk["spans"] = spans
            chunks.append(chunk)
    return chunks
