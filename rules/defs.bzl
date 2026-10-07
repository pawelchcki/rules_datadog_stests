"""Public Datadog assertion API; infrastructure comes from rules_stests."""

load(":injection.bzl", _env = "datadog_env", _python = "datadog_python_injection", _ruby = "datadog_ruby_injection")
load(":profile.bzl", _profile = "datadog_realworld_profile")
load(":go_runtime.bzl", _go_runtime = "go_runtime_capability_tests")

datadog_realworld_profile = _profile
datadog_env = _env
datadog_python_injection = _python
datadog_ruby_injection = _ruby
go_runtime_capability_tests = _go_runtime
