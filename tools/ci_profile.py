"""Deterministic PR representatives; exhaustive variants remain the full default."""

PROFILES = ("full", "pr")
PR_RUBY_SERIES = ("2.5", "3.3", "4.0")
PR_GO_VERSIONS = ("go1.4.3", "go1.17.13", "go1.25.14", "go1.27.1")


def ruby_runtimes(compatibility, profile):
    runtimes = compatibility["supported"]
    if profile == "full":
        return runtimes
    if profile != "pr":
        raise ValueError("Unknown CI profile: " + profile)
    by_series = {runtime["series"]: runtime for runtime in runtimes}
    if not set(PR_RUBY_SERIES) <= by_series.keys():
        raise ValueError("Representative Ruby runtime disappeared from compatibility matrix")
    return [by_series[series] for series in PR_RUBY_SERIES]


def sdk_cases(registry, profile):
    if profile == "full":
        return registry
    if profile != "pr":
        raise ValueError("Unknown CI profile: " + profile)
    # Registry order is pinned to upstream methods/parameters. Keep both ends
    # of each class, including singleton classes and local adapter classes.
    classes = {}
    for entry in registry:
        classes.setdefault(entry["method"].rsplit(".", 1)[0], []).append(entry)
    names = {entry["name"] for entries in classes.values() for entry in (entries[0], entries[-1])}
    return [entry for entry in registry if entry["name"] in names]


def select_targets(labels, suite, profile, registry=None):
    if profile == "full":
        return labels
    if profile != "pr":
        raise ValueError("Unknown CI profile: " + profile)
    if suite in ("scenarios", "parallel"):
        prefixes = tuple("//fixtures:ruby_" + series.replace(".", "_") + "_" for series in PR_RUBY_SERIES)
        return [label for label in labels if not label.startswith("//fixtures:ruby_") or label.startswith(prefixes)]
    if suite == "features":
        # All framework and intake cases stay on PRs; versioned Ruby probes
        # run on main, with their ABI representatives covered by scenarios.
        return [label for label in labels if not label.startswith("//fixtures:ruby_")]
    if suite == "shared-sdk":
        names = {entry["name"] for entry in sdk_cases(registry, profile)}
        indices = [index for index, entry in enumerate(registry) if entry["name"] in names]
        expected = {f"//fixtures:datadog_shared_sdk_{language}_{index}_test"
                    for language in ("go", "python") for index in indices}
        if not expected <= set(labels):
            raise ValueError("Representative SDK targets missing from suite")
        return [label for label in labels if label in expected]
    if suite == "capabilities":
        return labels
    raise ValueError("Unknown CI suite: " + suite)
