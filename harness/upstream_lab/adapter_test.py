"""Check upstream provenance and ensure assertions reject corrupt intake evidence."""
import hashlib
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import AgentIntake, Library, load_cases, fixture_namespace
from expected_failures import EXPECTED_FAILURES, matches_expected_failure


class AdapterChecks(unittest.TestCase):
    def test_executed_sdk_differences_match_only_fresh_native_signatures(self):
        fixtures = json.loads((Path(__file__).parent / "expected_failure_fixtures.json").read_text())
        self.assertEqual({row["name"] for row in fixtures}, set(EXPECTED_FAILURES))
        for row in fixtures:
            with self.subTest(case=row["name"], wire=row["wire"]):
                for field, hash_field in (("captures", "captureSha256"), ("operations", "operationsSha256")):
                    self.assertEqual(hashlib.sha256(json.dumps(row[field], sort_keys=True).encode()).hexdigest(), row[hash_field])
                def match(value):
                    return matches_expected_failure(value["name"], value["sdkVersion"], value["wire"],
                        value["failure"], value["captures"], value["operations"], value["sourceSha256"])
                self.assertTrue(match(row))
                for change in (
                    lambda value: value.update(name="different.case[0]"),
                    lambda value: value.update(sdkVersion="4.15.5"),
                    lambda value: value.update(sourceSha256="changed source"),
                    lambda value: value["failure"].update(type="ConnectionError"),
                    lambda value: value["failure"].update(function="different_failure"),
                    lambda value: value["failure"].update(line=1),
                    lambda value: value["failure"].clear(),
                    lambda value: value.update(operations=[]),
                ):
                    corrupted = copy.deepcopy(row)
                    change(corrupted)
                    self.assertFalse(match(corrupted))
                corrupted = copy.deepcopy(row)
                if corrupted["captures"] and any(corrupted["captures"]):
                    corrupted["captures"] = []
                else:
                    corrupted["captures"] = [[]]
                self.assertFalse(match(corrupted))

    def test_start_span_retains_explicit_finish_lifecycle(self):
        class Client(Library):
            def rpc(self, operation, **values):
                self.operations.append(operation)
                return {"span_id": 42, "trace_id": 123} if operation == "start" else None
        client = Client("unused")
        span = client.dd_start_span("control").__enter__()
        self.assertEqual(client.operations, ["start"])
        span.finish()
        span.finish()
        self.assertEqual(client.operations, ["start", "finish"])

    def test_vendored_sources_match_revision_hashes_and_cases_retain_parameters(self):
        vendor = Path(__file__).parent / "vendor"
        manifest = json.loads((vendor / "manifest.json").read_text())
        self.assertEqual(manifest["revision"], "098fe0967c587db8a16b74a1e711777d0a9d5867")
        for filename, spec in manifest["files"].items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((vendor / filename).read_bytes()).hexdigest(), spec["sha256"])
        cases = load_cases(vendor)
        self.assertEqual(len(cases), len({case["name"] for case in cases}))
        exact = [case for case in cases if case["method"] == "test_trace_sampled_by_trace_sampling_rule_exact_match" and case["class"] == "Test_Trace_Sampling_Basic"]
        self.assertEqual(len(exact), 3)
        self.assertEqual(len({json.dumps(case["parameters"], sort_keys=True) for case in exact}), 3)

    def test_upstream_assertion_reads_and_rejects_corrupt_native_span(self):
        vendor = Path(__file__).parent / "vendor"
        case = next(case for case in load_cases(vendor) if case["method"] == "test_distributed_headers_extract_datadog_D001")
        span = {"trace_id": 123456789, "span_id": 42, "parent_id": 987654321,
                "metrics": {"_sampling_priority_v1": 2}, "meta": {"_dd.origin": "synthetics;=web,z", "_dd.p.dm": "-4"}}
        class Library:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def dd_make_child_span_and_get_headers(self, headers):
                return {}
        class Agent:
            def wait_for_num_traces(self, count):
                return [[span]]
        case["function"](case["instance"], test_agent=Agent(), test_library=Library())
        span["trace_id"] = 999
        with self.assertRaises(AssertionError):
            case["function"](case["instance"], test_agent=Agent(), test_library=Library())

    def test_otel_api_kind_values_do_not_use_protobuf_kind_values(self):
        namespace = fixture_namespace(Path(__file__).parent / "vendor")
        self.assertEqual(namespace["SpanKind"].INTERNAL, 0)
        self.assertEqual(namespace["SpanKind"].PRODUCER, 3)

    def test_wrong_wire_is_rejected_without_rewriting_native_payload(self):
        class Intake(AgentIntake):
            def request(self, *args):
                return json.dumps([{"payload": {"wire_version": "v0.5", "traces": [[{"trace_id": 42}]]}}]).encode()
        with self.assertRaises(AssertionError):
            Intake("unused", "v0.4").traces()


if __name__ == "__main__":
    unittest.main()
