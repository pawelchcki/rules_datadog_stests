import base64
import copy
import ctypes
import gzip
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zlib

from harness.datadog_backend.backend import API_KEY, METRIC_SCHEMA, BackendServer, decompress
from harness.datadog_backend.wire import msgpack, protobuf, trace_chunks

# Generated with the pinned system-tests agent.descriptor and google.protobuf,
# independently of wire.py. The same schema hash is recorded in schema.json.
LEGACY = bytes.fromhex(
    "0a086c61622d686f73741204746573742ad6011206707974686f6e2205332e302e3032c40108ffffffffffffffffff01120a73796e746865746963731aaa010a08746573742d6170701207726571756573741a062f7175657279207b28c803388080a8b1e39fe7cb1740c0c407480152180a0a6572726f722e74797065120a56616c75654572726f72521d0a095f64642e702e7469641210313233343536373839306162636465665a180a0d5f64642e746f705f6c6576656c11000000000000f03f5a200a155f73616d706c696e675f7072696f726974795f763111000000000000f0bf62037765623a06372e38332e31"
)
INDEXED = bytes.fromhex(
    "0a086c61622d686f73743a06372e38332e315a93010a000a036170700a07726571756573740a062f71756572790a037765620a0b687474702e6d6574686f640a034745540a155f73616d706c696e675f7072696f726974795f76315a4b0802223508011002180321c8010000000000003100002a36fe9c971738c0c4074a0d080712091900000000000000404a06080512020806500432101234567890abcdef000000000000007b"
)


class WireTest(unittest.TestCase):
    def test_metrics_catalog_and_packed_sketch_scalars(self):
        # Independently specified Dogsketch wire: zigzag k=[-1,1], n=[2,3].
        packed = bytes.fromhex("3a02010242020203")
        decoded = protobuf(packed, ".datadog.agentpayload.SketchPayload.Sketch.Dogsketch", catalog=METRIC_SCHEMA)
        self.assertEqual(decoded, {"k": [-1, 1], "n": [2, 3]})
        self.assertEqual(protobuf(bytes.fromhex("3801380240024003"), ".datadog.agentpayload.SketchPayload.Sketch.Dogsketch", catalog=METRIC_SCHEMA), decoded)
        for malformed in [packed[:-1], bytes.fromhex("3a01ff"), bytes.fromhex("3d00000000")]:
            with self.subTest(body=malformed), self.assertRaises(ValueError):
                protobuf(malformed, ".datadog.agentpayload.SketchPayload.Sketch.Dogsketch", catalog=METRIC_SCHEMA)
    def test_descriptor_generated_legacy_payload(self):
        payload = protobuf(LEGACY)
        self.assertEqual(payload["host_name"], "lab-host")
        self.assertEqual(payload["agent_version"], "7.83.1")
        self.assertEqual(payload["tracer_payloads"][0]["language_name"], "python")
        chunk, = trace_chunks(payload)
        self.assertEqual(chunk["priority"], -1)
        span, = chunk["spans"]
        self.assertEqual((span["trace_id"], span["span_id"]), (123, 456))
        self.assertEqual(span["start"], 1700000000000000000)
        self.assertEqual(span["duration"], 123456)
        self.assertEqual(span["meta"]["_dd.p.tid"], "1234567890abcdef")
        self.assertEqual(span["metrics"]["_sampling_priority_v1"], -1)
        self.assertEqual(span["metrics"]["_dd.top_level"], 1)
        self.assertEqual(span["error"], 1)

    def test_descriptor_generated_indexed_payload(self):
        payload = protobuf(INDEXED)
        before = copy.deepcopy(payload)
        chunk, = trace_chunks(payload)
        self.assertEqual(chunk["priority"], 2)
        span, = chunk["spans"]
        self.assertEqual((span["service"], span["name"], span["resource"]), ("app", "request", "/query"))
        self.assertEqual((span["trace_id"], span["span_id"]), (123, 456))
        self.assertEqual(span["meta"]["_dd.p.tid"], "1234567890abcdef")
        self.assertEqual(span["meta"]["http.method"], "GET")
        self.assertEqual(span["metrics"]["_sampling_priority_v1"], 2)
        self.assertEqual(payload, before)
        payload["idx_tracer_payloads"][0]["strings"] = [""]
        with self.assertRaisesRegex(ValueError, "reference out of bounds"):
            trace_chunks(payload)

    def test_protobuf_malformed_and_unknown_fields(self):
        for body in (b"\x00", b"\x0a\xff", b"\x0a\x02x", b"\x08\x01", b"\xff" * 10, b"\x0b"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                protobuf(body)
        self.assertEqual(protobuf(LEGACY + b"\xa0\x06\x01"), protobuf(LEGACY))

    def test_messagepack_stats_bytes_and_unsigned_ids(self):
        # {Stats: [{Hits: 2, OkSummary: binary(00ff), TraceID: uint64(max)}]}
        packed = bytes.fromhex("81a553746174739183a44869747302a94f6b53756d6d617279c40200ffa754726163654944cfffffffffffffffff")
        value = msgpack(packed)["Stats"][0]
        self.assertEqual(value["Hits"], 2)
        self.assertEqual(value["TraceID"], (1 << 64) - 1)
        self.assertEqual(base64.b64decode(value["OkSummary"]["base64"]), b"\x00\xff")
        for malformed in (b"\x81", b"\x82\xa1x\x01\xa1x\x02", packed + b"\x00", b"\xc1", b"\xdd\xff\xff\xff\xff"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                msgpack(malformed)

    def test_compression(self):
        self.assertEqual(decompress(gzip.compress(LEGACY), "gzip"), LEGACY)
        self.assertEqual(decompress(zlib.compress(LEGACY), "deflate"), LEGACY)
        # Frame generated by libzstd, as used by the pinned Agent.
        library = ctypes.CDLL("libzstd.so.1")
        library.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        library.ZSTD_compress.restype = ctypes.c_size_t
        destination = ctypes.create_string_buffer(4096)
        size = library.ZSTD_compress(destination, 4096, LEGACY, len(LEGACY), 1)
        self.assertEqual(decompress(destination.raw[:size], "zstd"), LEGACY)
        for encoding in ("gzip", "deflate", "zstd", "other"):
            with self.subTest(encoding=encoding), self.assertRaises((ValueError, OSError, EOFError, zlib.error)):
                decompress(b"broken", encoding)


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = BackendServer(("127.0.0.1", 0), self.directory.name)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_accepts_retains_and_redacts_authenticated_trace_request(self):
        body = gzip.compress(LEGACY)
        headers = {"DD-API-KEY": API_KEY, "Content-Type": "application/x-protobuf", "Content-Encoding": "gzip"}
        self.assertEqual(self.request("POST", "/api/v0.2/traces", body, headers), (200, {}))
        status, records = self.request("GET", "/dump")
        self.assertEqual(status, 200)
        record, = records
        self.assertEqual(record["payload"]["agent_version"], "7.83.1")
        self.assertEqual(record["raw_sha256"], hashlib.sha256(body).hexdigest())
        directory = Path(self.directory.name)
        self.assertEqual((directory / record["raw_file"]).read_bytes(), body)
        self.assertEqual(json.loads((directory / "requests.json").read_text()), records)
        self.assertNotIn(API_KEY, (directory / "request-000000.json").read_text())

    def test_bad_auth_unknown_route_and_malformed_trace_are_not_acknowledged(self):
        headers = {"DD-API-KEY": API_KEY, "Content-Type": "application/x-protobuf"}
        self.assertEqual(self.request("POST", "/api/v0.2/traces", LEGACY, {})[0], 403)
        self.assertEqual(self.request("POST", "/typo", LEGACY, headers)[0], 404)
        self.assertEqual(self.request("POST", "/api/v0.2/traces", b"broken", headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/v0.2/traces", b"", headers)[0], 400)
        self.assertEqual([item["status"] for item in self.server.snapshot()], [403, 404, 400, 400])
        self.assertTrue(all("error" in item for item in self.server.snapshot()))

    def test_api_key_validation_and_stats_route(self):
        self.assertEqual(self.request("GET", "/api/v1/validate"), (403, {"valid": False}))
        self.assertEqual(self.request("GET", "/api/v1/validate", headers={"DD-API-KEY": API_KEY}), (200, {"valid": True}))
        headers = {"DD-API-KEY": API_KEY, "Content-Type": "application/msgpack"}
        self.assertEqual(self.request("POST", "/api/v0.2/stats", b"\x81\xa5Stats\x90", headers), (200, {}))
        self.assertEqual(self.server.snapshot()[-1]["payload"], {"Stats": []})
        status, payload = self.request("GET", "/api/v2/validate", headers={"DD-API-KEY": API_KEY})
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["id"], "00000000-0000-0000-0000-000000000001")

    def test_core_query_key_authentication_is_strict_and_redacted(self):
        path = "/api/v1/validate?api_key=" + API_KEY
        self.assertEqual(self.request("GET", path), (200, {"valid": True}))
        self.assertEqual(self.request("GET", path + "&api_key=wrong")[0], 403)
        self.assertEqual(self.request("GET", path, headers={"DD-API-KEY": "wrong"})[0], 403)
        records = self.server.snapshot()
        self.assertTrue(all(record["path"] == "/api/v1/validate" for record in records))
        self.assertNotIn(API_KEY, json.dumps(records))


if __name__ == "__main__":
    unittest.main()
