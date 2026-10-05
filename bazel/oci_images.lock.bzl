"""Digest locks for the independently reviewed Datadog fixtures."""

OCI_IMAGES = {
    # Same Agent release and manifest index as system-tests at 098fe0967c58.
    "datadog_agent": struct(
        repository = "docker.io/datadog/agent",
        digest = "sha256:ed0bd588e955d82f661d1b8dd1cdf179c1023e74a2817e7a812c99d52f05c319",
        version = "7.83.1",
    ),
    "gin_datadog_realworld": struct(
        repository = "ghcr.io/pawelchcki/rules_stest_apps",
        digest = "sha256:ee6a879cae36694b99a967fc4ba62b797a269422bd80249f8c7939de07cd0166",
        tree = "3e3c29c2f28bc232eba4d3911d0399abfb009b8e",
    ),
}

DATADOG_PYTHON = struct(
    repository = "install.datadoghq.com/apm-library-python-package",
    digest = "sha256:8276af62a8236cb92a3bd64710271b5f2a537cb586e4633d92f1742f3c4ff3a0",
    version = "4.14.0-1",
)

# Published after verifying the reviewed payload digest and anonymous pull.
DATADOG_RUBY = struct(
    repository = "ghcr.io/pawelchcki/rules_stest_agents",
    digest = "sha256:eb96229a846b2335a56e0fa2a4b6b454bceebcfe2cfb92b5a7b2841888fb61a8",
    tree = "1f1b230330b94d6d5198b9efcfebd4e4844bd3ef",
    version = "2.42.0",
)
