"""Datadog profile declarations using the shared atomic profile compiler."""
load("@rules_stests//rules:otel_profile.bzl", "otel_profile", "otel_realworld_profile")
load("@rules_stests//corpus:registry.bzl", "REALWORLD_BASE_HURL_CASES")

def validate_scheme_identifier(value, field):
    if not value:
        fail("{} must be a non-empty Scheme identifier".format(field))
    first = value[0]
    if not ((first >= "a" and first <= "z") or
            (first >= "A" and first <= "Z") or
            first == "_"):
        fail("{} {} is not a valid Scheme identifier".format(field, value))
    for index in range(1, len(value)):
        character = value[index]
        if not ((character >= "a" and character <= "z") or
                (character >= "A" and character <= "Z") or
                (character >= "0" and character <= "9") or
                character in "-+_"):
            fail("{} {} is not a valid Scheme identifier".format(field, value))

def datadog_realworld_profile(
        name,
        specification,
        implementation_libraries,
        wire_version = "v0.5",
        runtime_libraries = [],
        signals = ["traces"],
        standard_registry = Label("//corpus:datadog_feature_registry"),
        core_libraries = Label("//corpus:datadog_core_libraries"),
        program = Label("//corpus:datadog/realworld/programs/validate_profile.scm"),
        reference_profile = None,
        scenarios = None,
        **kwargs):
    """Declares an independent Datadog schema-v2 profile and proof plan."""
    if wire_version not in ["v0.4", "v0.5"]:
        fail("Datadog wire_version must be v0.4 or v0.5")
    if signals != ["traces"]:
        fail("Datadog profiles support traces only")
    if reference_profile:
        if scenarios != None or "shape_root" in kwargs or "scenario_shapes" in kwargs:
            fail("reference mode inherits reviewed scenarios and shapes")
        resolved_profile_id = kwargs.pop("profile_id", name)
        validate_scheme_identifier(resolved_profile_id, "profile_id")
        otel_profile(
            name = name,
            profile_id = resolved_profile_id,
            specification = specification,
            implementation_libraries = implementation_libraries,
            runtime_libraries = runtime_libraries,
            signals = signals,
            scenarios = [],
            scenario_shapes = {},
            standard_registry = standard_registry,
            core_libraries = core_libraries,
            program = program,
            family = "datadog",
            wire_version = wire_version,
            reference_profile = reference_profile,
            **kwargs
        )
        native.filegroup(name = name + ".proof_plan", srcs = [":" + name], output_group = "proof_plan")
        return
    otel_realworld_profile(
        name = name,
        specification = specification,
        implementation_libraries = implementation_libraries,
        runtime_libraries = runtime_libraries,
        signals = signals,
        standard_registry = standard_registry,
        core_libraries = core_libraries,
        program = program,
        family = "datadog",
        wire_version = wire_version,
        scenarios = scenarios if scenarios != None else REALWORLD_BASE_HURL_CASES,
        consumer_scenarios = ["native_concurrency", "native_malformed", "native_exceptions", "native_ruby_client"],
        **kwargs
    )
