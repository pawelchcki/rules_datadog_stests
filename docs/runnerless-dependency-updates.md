# Coordinated Runnerless SDK updates

The repository-owned updater implements the candidate path for
[my-infra #114](https://github.com/pawelchcki/my-infra/issues/114). Its source
is ready for review independently of live enrollment. The top-level `enabled`
flag in `.runnerless/dependency-updates.json` is **false**. No weekly job should
be inferred from the presence of that file.

The remaining activation prerequisite is GitHub Packages **Manage Actions
access → Write** for `pawelchcki/rules_datadog_stests` on the existing
`rules_stest_agents` and `rules_stest_apps` packages. The previously observed
publisher authenticated and received `packages: write`, but its first upload
was rejected with `permission_denied:write_package`. Workflow token scope and
package access are separate. Preserve any already sufficient access; no new
PAT, identity, package or App permission is required. Verify a normal publisher
upload and both anonymous pulls before enabling the top-level flag in a
reviewed change. Live App PR creation, repeat reuse, normal protected CI and the
weekly occurrence remain acceptance work; source tests do not establish them.

The App loads the regular opt-in from the immutable default branch after
verifying adoption in `test123`. The existing publishing bot identity is
`317265358`. The configured workflow is `dependency-updates.yml`, artifact
`runnerless-dependency-update`, once per week Monday at 08:00 UTC. The App uses
its existing Contents-write repository dispatch and Actions-read observations;
this path needs no BuildBuddy enrollment or execution credential.

## Candidate production

`.runnerless/dependency-update-recipe.json` pins the public registry endpoints
for the Ruby gem, Python release and Go module. Runnerless's Rust executable
performs the bounded anonymous reads and invokes this repository's trusted
host commands in a fresh private checkout. The requesting scope, base and
namespaced initial run ID are supplied by the native host. Stable release
versions are required; a downgrade, changed Ruby admission range or compiler
minimum without a reviewed builder stops the producer.

`tools/runnerless_update_datadog.py` performs these coordinated steps:

1. Resolve Ruby with the payload's exact Ruby/Bundler pair. Resolve the native
   runtime gem graph with RubyGems requirements, explicit platforms, existing
   compatible pins and streamed SHA-256 verification of every selected gem.
   The supported Ruby range remains the explicitly modeled `>= 2.5.0, < 4.1`.
2. Read the actual Go SDK module's compiler minimum and choose a reviewed
   immutable compiler. Run real `go get`, `go mod tidy` and `go mod verify` on
   the composed Gin application, shared SDK harness and runtime Orchestrion
   graph. Update the runtime SDK/tool identity and minimum from their actual
   module declarations. Go 2.11.2's modules require Go 1.26; the reviewed
   builder map includes that compiler. PR selection adds a new required minimum
   without removing the older controls.
3. Verify the official Python `<version>-1` OCI index anonymously, require its
   unique Linux amd64 entry and pin the exact index bytes. Native SDK health
   and profile tests verify the executed SDK, rather than trusting its tag.
4. Commit source contexts in the private producer checkout, build and verify
   both Ruby and Gin candidates, then invoke the existing candidate publisher.
   The publisher independently rechecks actual SDK payloads and provenance,
   preserves manifest digests and requires both real anonymous pulls before
   installing the coordinated OCI and rootfs locks.
5. Add versioned implementation/profile files and the append-only profile
   catalog. Every historical definition remains intact. Reviewed profiles use
   explicit references to their existing reviewed expectations; native tests
   still have to pass those expectations with the new SDK. Sinatra's unreviewed
   profiles continue to validate contracts and retain their existing scope.
6. Settle module metadata and emit the update receipt outside the checkout.
   The native host commits the final candidate, executes verification and
   refuses changed HEADs or dirty verification. Failed builds, failed pulls or
   failed tests produce no accepted candidate.

Verification runs actual current profile/scenario tests across the compatible
Ruby runtimes, the sink/conformance tests and native Go/Python SDK probes.
The resulting App PR must independently pass the normal full representative
fresh-execution evidence, integrity and coverage gates plus Codex review. Neither
this producer nor the App enables auto-merge or merges a PR.

`bazel/datadog_sdk_versions.json` is the current source of truth for active
fixture labels and runtime probes. Historical expected-failure receipts remain
bound to their original SDK/source versions; the updater never extends them to
a new SDK. A newly observed SDK difference needs its own actual evidence and
review. The public OCI references in historical implementation files remain
unchanged when active fixtures move to a new version.

## Producer and credential transport

The Actions job builds Runnerless's reviewed Rust producer from immutable
`my-infra` commit `148fe13aa8d7435a604ceb8ed24dee8baf7775b2` in a separate Bazel
tools workspace. Its Rust source is unchanged. The consumer explicitly pins
Bazel, Rust, Python and C/C++ toolchains, imports the same vendored Cosmopolitan
module, and applies only module-ownership adjustments for unused named Go/Node
toolchains plus the source repository's existing CRT debug-path fix. This
workspace has its own local execution policy; it does not override the source
repository's root RBE policy or require a BuildBuddy key. The fixture producer
and ordinary PR tests retain the requesting repository's execution settings.

Actions checkout does not persist credentials. Registry authentication stays in
memory: the fixed GHCR push receives the ephemeral token as an auth document
over stdin. It is absent from command arguments, auth files, diagnostic output
and all other child environments. Anonymous pulls remain explicitly anonymous.
The existing reviewed-payload publisher uses the same transport. Its optional
Git push authenticates through `gh auth git-credential` without persisting a
token in Git configuration.

The job retains the bounded candidate artifact for seven days. Runnerless
rehashes the complete ZIP, privately stages bounded parts and checks the same
App actor, immutable base, initial attempt and artifact before writing an
ordinary PR. The configured allowlist excludes recipes, workflows, CI control
scripts and reviewed shape files. Native limits remain 64 changes, 512 KiB per
file and 2 MiB of edit content.

This adds no cloud resource, credential or permission grant and no fixed
monthly idle cost. Runs use the existing Actions and package-storage allowances.
