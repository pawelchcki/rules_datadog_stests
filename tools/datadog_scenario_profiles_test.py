"""Scenario retention follows upgrades without dropping framework/wire controls."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import datadog_scenario_profiles as profiles


class ScenarioProfilesTest(unittest.TestCase):
    def compatibility(self, ruby):
        return {"tracerVersion": ruby, "supported": [
            {"series": series} for series in ("2.5", "2.6", "2.7", "3.0", "3.1", "3.2", "3.3", "3.4", "4.0")]}

    def test_upgrade_routes_each_framework_and_wire_to_its_current_receipts(self):
        versions = {"ruby": "2.47.0", "python": "4.16.1", "go": "2.11.2"}
        selected = profiles.active_profiles(versions, self.compatibility("2.47.0"), "pr")
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected["python-aiohttp-datadog-v4-16-1-v04"], "aiohttp_datadog_v04")
        self.assertEqual(selected["python-aiohttp-datadog-v4-16-1-v05"], "aiohttp_datadog")
        self.assertEqual(selected["python-django-datadog-v4-16-1-v04"], "django_datadog_v04")
        self.assertEqual(selected["python-django-datadog-v4-16-1-v05"], "django_datadog")
        self.assertEqual(selected["ruby-rails-datadog-v2-47-0-v04"], "rails_datadog")
        self.assertEqual(selected["ruby-falcon-datadog-v2-47-0-v04"], "falcon_datadog")
        self.assertEqual(selected["go-gin-datadog-v2-11-2-v04"], "gin_datadog")
        self.assertEqual(selected["ruby-sinatra-2-5-datadog-v2-47-0-v04"], "ruby_2_5_datadog")

    def test_full_and_representative_scopes_retain_existing_controls_after_upgrade(self):
        for versions in [profiles.VERSIONS, {"ruby": "2.47.0", "python": "4.16.1", "go": "2.11.2"}]:
            compatibility = self.compatibility(versions["ruby"])
            full = profiles.active_profiles(versions, compatibility, "full")
            representative = profiles.active_profiles(versions, compatibility, "pr")
            matrix = profiles.active_profiles(versions, compatibility, "pr", ruby_matrix_only=True)
            self.assertEqual(len(full), 16)
            self.assertEqual(len(representative), 10)
            self.assertEqual(set(matrix.values()), {"ruby_2_5_datadog", "ruby_3_3_datadog", "ruby_4_0_datadog"})
            self.assertTrue(representative.items() <= full.items())
            self.assertEqual(len(set(representative) - set(matrix)), 7)

    def test_stale_built_ruby_compatibility_is_not_silently_relabelled(self):
        with self.assertRaises(ValueError):
            profiles.active_profiles(dict(profiles.VERSIONS, ruby="2.47.0"), self.compatibility("2.43.0"), "pr")

    def test_direct_parity_entrypoint_loads_declared_pins_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatibility.json"
            path.write_text(json.dumps(self.compatibility(profiles.VERSIONS["ruby"])))
            result = subprocess.run([sys.executable, str(Path(profiles.__file__).resolve()),
                "--compatibility", str(path), "--ci-profile", "pr"], cwd=directory, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        labels = result.stdout.splitlines()
        self.assertEqual(len(labels), 10)
        self.assertTrue(all(label.startswith("//corpus:") for label in labels))


if __name__ == "__main__":
    unittest.main()
