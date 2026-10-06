"""Bounded Agent readiness polling across startup connection failures."""
from http.client import RemoteDisconnected
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from harness.datadog_agent import probe


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.process = Mock()
        self.process.poll.return_value = None
        self.log = Mock()
        self.log.read_text.return_value = "agent startup log"

    def test_retries_startup_disconnect_until_valid_response(self):
        with patch.object(probe, "get", side_effect=[
            RemoteDisconnected("starting"), URLError("not listening"), b'{"version":"7.83.1"}',
        ]) as get, patch.object(probe.time, "sleep") as sleep:
            self.assertEqual(probe.wait_ready(self.process, "http://localhost/info", self.log),
                             {"version": "7.83.1"})
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_disconnect_retry_keeps_existing_deadline(self):
        with patch.object(probe, "get", side_effect=RemoteDisconnected("starting")) as get, \
                patch.object(probe.time, "monotonic", side_effect=[0, 0, 31]), \
                patch.object(probe.time, "sleep"):
            with self.assertRaisesRegex(AssertionError, "readiness timeout: agent startup log"):
                probe.wait_ready(self.process, "http://localhost/info", self.log)
        get.assert_called_once()

    def test_invalid_response_is_not_a_startup_retry(self):
        with patch.object(probe, "get", return_value=b"invalid JSON"), \
                patch.object(probe.time, "sleep") as sleep:
            with self.assertRaises(json.JSONDecodeError):
                probe.wait_ready(self.process, "http://localhost/info", self.log)
        sleep.assert_not_called()

    def test_dead_agent_fails_before_retrying(self):
        self.process.poll.return_value = 1
        with patch.object(probe, "get") as get:
            with self.assertRaisesRegex(AssertionError, "agent startup log"):
                probe.wait_ready(self.process, "http://localhost/info", self.log)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
