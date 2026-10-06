"""Digest locks for the independently reviewed Datadog fixtures."""

OCI_IMAGES = {
    "datadog_mariadb": struct(
        repository = "docker.io/library/mariadb",
        digest = "sha256:1292844148b311e4ed4300022a996d39083f415a963e970cf47cad1b3b18e3a6",
        version = "11.4",
    ),
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
    digest = "sha256:9249d9395e5e4ea643bcfe1f3d5c8472d62ed1fc3404dbbde8d2a3e5b394f13b",
    version = "4.15.5-1",
)

# Published after verifying the reviewed payload digest and anonymous pull.
DATADOG_RUBY = struct(
    repository = "ghcr.io/pawelchcki/rules_stest_agents",
    digest = "sha256:9b59812e1c1c95523ae82265c78e87d612a840a8ebf1140fb0c90cbb2df8640f",
    tree = "719cdf96e8569b8c39c841f3b8c9cb3d55191673",
    version = "2.43.0",
)
