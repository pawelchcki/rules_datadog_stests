"""Active scenario targets and receipt paths share the same SDK identity."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from sdk_versions import VERSIONS
from ci_profile import PROFILES, ruby_runtimes


def active_profiles(versions, compatibility, ci_profile, *, ruby_matrix_only=False):
    if compatibility["tracerVersion"] != versions["ruby"]:
        raise ValueError("Ruby compatibility and active SDK pins differ")
    pins = {language: version.replace(".", "-") for language, version in versions.items()}
    profiles = {}
    if not ruby_matrix_only:
        for framework in ("aiohttp", "django"):
            for wire in ("04", "05"):
                name = f"python-{framework}-datadog-v{pins['python']}-v{wire}"
                profiles[name] = framework + "_datadog" + ("_v04" if wire == "04" else "")
        for framework in ("rails", "falcon"):
            profiles[f"ruby-{framework}-datadog-v{pins['ruby']}-v04"] = framework + "_datadog"
        profiles[f"go-gin-datadog-v{pins['go']}-v04"] = "gin_datadog"
    for runtime in ruby_runtimes(compatibility, ci_profile):
        series = runtime["series"]
        profiles[f"ruby-sinatra-{series.replace('.', '-')}-datadog-v{pins['ruby']}-v04"] = (
            "ruby_" + series.replace(".", "_") + "_datadog")
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compatibility", required=True, type=Path)
    parser.add_argument("--ci-profile", choices=PROFILES, default="full")
    args = parser.parse_args()
    profiles = active_profiles(VERSIONS, json.loads(args.compatibility.read_text()), args.ci_profile)
    print("\n".join("//corpus:" + profile for profile in profiles))


if __name__ == "__main__":
    main()
