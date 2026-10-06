"""Exercise the pinned ddtrace 4.15.5 LLMObs dataset APIs in-process.

Replicates the upstream parametric test client
(tests/parametric/.../apm_test_client/llmobs.py): LLMObs.create_dataset() and
LLMObs._delete_dataset(), returning the same fields the upstream test
(Test_Dataset.test_dataset_create_delete) asserts on.
"""
import argparse
import json
from pathlib import Path

import ddtrace
from ddtrace.llmobs import LLMObs

DATASET_NAME = "test-dataset-basic"
DATASET_DESCRIPTION = "A basic test dataset"
PROJECT_NAME = "test-project"
ML_APP = "integrations-lab"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity-file", required=True)
    args = parser.parse_args()

    LLMObs.enable(agentless_enabled=False, ml_app=ML_APP)

    # The pinned SDK builds its experiments (DNE) client with is_agentless=True
    # ("agent proxy doesn't seem to work for experiments" in ddtrace 4.15.5), which
    # suppresses the X-Datadog-EVP-Subdomain header the trace-agent EVP proxy
    # requires (agent 7.83.1 rejects subdomain-less requests). Flip the private
    # flag so the pinned client emits exactly the headers it would in
    # agent-proxy mode; DD_LLMOBS_OVERRIDE_ORIGIN (set by the probe) routes the
    # requests at the real agent's /evp_proxy/v2 endpoint. Recorded in the
    # receipt configuration via the identity file.
    dne_client = LLMObs._instance._dne_client
    dne_client._agentless = False

    dataset = LLMObs.create_dataset(
        dataset_name=DATASET_NAME,
        description=DATASET_DESCRIPTION,
        project_name=PROJECT_NAME,
    )
    identity = {
        "tracer_version": ddtrace.__version__,
        "llmobs": {
            "ml_app": ML_APP,
            "agentless_enabled": False,
            "dne_override_origin": True,
            "dne_agent_proxy_headers": True,
        },
        "dataset": {
            "dataset_id": dataset._id,
            "name": dataset.name,
            "description": dataset.description,
            "project_name": dataset.project.get("name") if dataset.project else None,
            "project_id": dataset.project.get("_id") if dataset.project else None,
            "version": dataset._version,
            "latest_version": dataset._latest_version,
            "records": list(dataset._records),
        },
    }
    assert identity["dataset"]["dataset_id"], "dataset create returned no dataset_id"
    assert identity["dataset"]["name"] == DATASET_NAME, identity
    assert identity["dataset"]["description"] == DATASET_DESCRIPTION, identity
    assert identity["dataset"]["project_name"] == PROJECT_NAME, identity
    assert identity["dataset"]["records"] == [], identity

    LLMObs._delete_dataset(dataset_id=dataset._id)
    identity["delete"] = {"success": True, "dataset_id": dataset._id}

    LLMObs.flush()
    LLMObs.disable()
    ddtrace.tracer.shutdown()
    Path(args.identity_file).write_text(json.dumps(identity, indent=2) + "\n")


if __name__ == "__main__":
    main()
