"""Mutation regressions for sampled-stack and profile multipart evidence."""
import copy
import unittest

from harness.datadog_profiling.probe import check_profiles
from harness.datadog_profiling.wire import pprof

# pprof with one stack location and its linked function, plus the mandatory
# empty string-table entry. Location 1 -> function 1 -> name index 1.
PROFILE = bytes.fromhex("12030a010122060801220208012a04080110013200320c") + b"profile_work"


class ProfileEvidenceTest(unittest.TestCase):
    def test_samples_need_linked_locations_and_functions(self):
        decoded = pprof(PROFILE)
        self.assertEqual(decoded["sampledFunctions"], ["profile_work"])
        self.assertEqual(decoded["samples"], 1)
        for body in [PROFILE.replace(bytes.fromhex("12030a0101"), bytes.fromhex("12030a0102")),
                     PROFILE.replace(bytes.fromhex("2a0408011001"), b""),
                     PROFILE.replace(bytes.fromhex("12030a0101"), b""), PROFILE[:-2]]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                pprof(body)

    def test_unlinked_workload_name_does_not_pass(self):
        event = {"start": "2026-10-03T00:00:00Z", "end": "2026-10-03T00:00:01Z",
                 "tags_profiler": "service:profiling-lab,env:profiling-env,version:profiling-version",
                 "process_tags": "entrypoint.name:workload,entrypoint.workdir:lab,svc.user:true"}
        record = {"status": 200, "payload": {"event": event, "parts": [{"profile": pprof(PROFILE)}]}}
        check_profiles([record])
        for change in ("function", "interval", "service", "process"):
            mutated = copy.deepcopy(record)
            if change == "function":
                mutated["payload"]["parts"][0]["profile"]["sampledFunctions"] = ["unrelated"]
            elif change == "interval":
                mutated["payload"]["event"]["end"] = event["start"]
            elif change == "service":
                mutated["payload"]["event"]["tags_profiler"] = "service:unrelated"
            else:
                mutated["payload"]["event"]["process_tags"] = "entrypoint.name:only"
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_profiles([mutated])


if __name__ == "__main__":
    unittest.main()
