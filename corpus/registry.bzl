"""Datadog assertion libraries and reviewed profile registry."""

load("@rules_stests//corpus:registry.bzl", "REALWORLD_BASE_HURL_CASES")

# Datadog has a separate wire feature catalog and proof runtime. The shared
# scenario observations describe application HTTP behavior, not telemetry.
# Every list is dependency ordered, like OTEL_CORE_LIBRARIES.

# Named capture assertions, one library per theme, then their registry.
DATADOG_CAPTURE_LIBRARIES = [
    "@rules_stests//corpus:telemetry/contract-error.scm",
    "datadog/capture/base.scm",
    "datadog/capture/intake.scm",
    "datadog/capture/traces.scm",
    "datadog/capture/service.scm",
    "datadog/capture/sampling.scm",
    "datadog/capture/propagation.scm",
    "datadog/capture/http.scm",
    "datadog/capture/database.scm",
    "datadog/capture/errors.scm",
    "datadog/capture/coverage.scm",
    "datadog/capture/shapes.scm",
]

# Feature id -> assertion tables, one per theme, then their facade.
DATADOG_PROOF_LIBRARIES = [
    "datadog/proofs/intake.scm",
    "datadog/proofs/traces.scm",
    "datadog/proofs/service.scm",
    "datadog/proofs/sampling.scm",
    "datadog/proofs/propagation.scm",
    "datadog/proofs/http.scm",
    "datadog/proofs/database.scm",
    "datadog/proofs/errors.scm",
    "datadog/proofs/coverage.scm",
    "datadog/proofs.scm",
]

# The vocabulary reviewed trace shapes are written in: the shape language and
# its matcher, what each tracer adds, and one library per application.
DATADOG_SHAPE_LIBRARIES = [
    "datadog/trace-shape.scm",
    "datadog/trace-shape/match.scm",
    "datadog/shape/tracers.scm",
    "datadog/shape/http.scm",
    "datadog/shape/aiohttp.scm",
    "datadog/shape/django.scm",
    "datadog/shape/rails.scm",
    "datadog/shape/falcon.scm",
    "datadog/shape/gin.scm",
]

DATADOG_CORE_LIBRARIES = DATADOG_CAPTURE_LIBRARIES + DATADOG_PROOF_LIBRARIES + DATADOG_SHAPE_LIBRARIES + [
    "@rules_stests//corpus:realworld/scenarios.scm",
    "datadog/profile.scm",
]

def datadog_library_args(libraries):
    """Command-line paths for libraries in their dependency order.

    `$(rootpaths)` sorts a filegroup's files, so tests that assemble a Scheme
    bundle receive one `$(rootpath)` per library instead.
    """
    return ["$(rootpath {})".format(label) for label in datadog_library_labels(libraries)]

def datadog_library_labels(libraries):
    """The labels datadog_library_args refers to; list them as test data."""
    return [path if path.startswith("@") else "//corpus:{}".format(path) for path in libraries]

DATADOG_SCENARIOS = REALWORLD_BASE_HURL_CASES + ["propagation_datadog"]

# Each profile's reviewed shapes are datadog/realworld/shape/<profile>/<scenario>.scm.
DATADOG_PROFILES = {
    "go-gin-datadog-v2-10-1-v04": struct(implementation = "go-v2.10.1", wire_version = "v0.4"),
    "ruby-rails-datadog-v2-42-0-v04": struct(implementation = "ruby-v2.42.0", wire_version = "v0.4"),
    "ruby-falcon-datadog-v2-42-0-v04": struct(implementation = "ruby-v2.42.0", wire_version = "v0.4"),
    "python-aiohttp-datadog-v4-14-0-v05": struct(implementation = "python-v4.14.0", wire_version = "v0.5"),
    "python-django-datadog-v4-14-0-v05": struct(implementation = "python-v4.14.0", wire_version = "v0.5"),
    "python-aiohttp-datadog-v4-14-0-v04": struct(implementation = "python-v4.14.0", wire_version = "v0.4"),
    "python-django-datadog-v4-14-0-v04": struct(implementation = "python-v4.14.0", wire_version = "v0.4"),
}

def declare_datadog_profiles(datadog_realworld_profile):
    """Declares Datadog's independent profiles and native wire assertions."""
    for profile_id, declaration in DATADOG_PROFILES.items():
        datadog_realworld_profile(
            name = profile_id,
            specification = "datadog/realworld/profile/{}.scm".format(profile_id),
            implementation_libraries = ["datadog/implementation/{}.scm".format(declaration.implementation)],
            runtime_libraries = [],
            # Until its candidates are reviewed, a profile validates contracts only.
            shape_root = "datadog/realworld/shape/{}".format(profile_id) if getattr(declaration, "reviewed", True) else None,
            signals = ["traces"],
            scenarios = DATADOG_SCENARIOS,
            wire_version = declaration.wire_version,
        )
