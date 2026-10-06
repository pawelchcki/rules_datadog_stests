"""Assert the pinned SDK's dataset create/delete reached the backend through the real Agent EVP proxy.

The SDK posts dataset API calls at <override origin>/evp_proxy/v2/api/unstable/llm-obs/v1/...
(ddtrace 4.15.5 LLMObsExperimentsClient). The trace-agent strips the /evp_proxy/v2 prefix and
forwards to https://api.<evp dd_url> with Via / X-Datadog-Hostname / X-Datadog-AgentDefaultEnv
headers. These checks mirror Test_Dataset.test_dataset_create_delete while also verifying both
wire boundaries.
"""
import re

DATASET_NAME = "test-dataset-basic"
DATASET_DESCRIPTION = "A basic test dataset"
PROJECT_NAME = "test-project"
EVP_PREFIX = "/evp_proxy/v2"
PROJECTS_PATH = "/api/unstable/llm-obs/v1/projects"
DATASET_DELETE_PATH = "/api/unstable/llm-obs/v1/datasets/delete"
DATASET_CREATE_PATH = re.compile(r"^/api/unstable/llm-obs/v1/(?P<project_id>[A-Za-z0-9_.-]+)/datasets$")


def check(identity, sdk_requests, backend_requests, relay_records, agent_version, hostname, default_env):
    """sdk_requests/backend_requests are capture records with parsed bodies/payloads."""
    # The SDK returned the objects the upstream test asserts on.
    dataset = identity["dataset"]
    assert dataset["dataset_id"], "upstream: dataset.get('dataset_id') is not None"
    assert dataset["name"] == DATASET_NAME, "upstream: dataset.get('name') == 'test-dataset-basic'"
    assert dataset["description"] == DATASET_DESCRIPTION, "upstream: dataset.get('description') == 'A basic test dataset'"
    assert dataset["project_name"] == PROJECT_NAME, "upstream: dataset.get('project_name') == 'test-project'"
    assert identity["delete"]["success"] is True, "upstream: result.get('success') is True"
    assert identity["delete"]["dataset_id"] == dataset["dataset_id"], identity
    assert identity["tracer_version"] == "4.15.5", identity

    # The SDK issued exactly the three pinned DNE calls, all through the agent EVP proxy path.
    projects, creates, deletes, other = [], [], [], []
    for request in sdk_requests:
        assert request["path"].startswith(EVP_PREFIX), request
        stripped = request["path"][len(EVP_PREFIX):]
        if stripped == PROJECTS_PATH:
            projects.append(request)
        elif stripped == DATASET_DELETE_PATH:
            deletes.append(request)
        elif (match := DATASET_CREATE_PATH.match(stripped)):
            creates.append((request, match.group("project_id")))
        else:
            other.append(request)
    assert not other, other
    assert len(projects) == len(deletes) == 1 and len(creates) == 1, (projects, creates, deletes)
    for request in sdk_requests:
        assert request["status"] == 200, request
        assert request["headers"]["x-datadog-evp-subdomain"] == "api", request
        assert request["headers"]["content-type"] == "application/json", request

    create_request, project_id = creates[0]
    assert projects[0]["body"] == {"data": {"type": "projects", "attributes": {"name": PROJECT_NAME, "description": ""}}}, projects[0]
    assert create_request["body"] == {"data": {"type": "datasets", "attributes": {"name": DATASET_NAME, "description": DATASET_DESCRIPTION}}}, create_request
    delete_body = deletes[0]["body"]
    assert delete_body["data"]["type"] == "datasets", delete_body
    assert delete_body["data"]["attributes"]["type"] == "soft", delete_body
    assert delete_body["data"]["attributes"]["dataset_ids"] == [dataset["dataset_id"]], delete_body

    # Each SDK request was forwarded by the real agent to the local backend, byte-identical.
    forwarded = []
    for request in sdk_requests:
        stripped = request["path"][len(EVP_PREFIX):]
        matches = [record for record in backend_requests if record["path"] == stripped
                   and record["raw_sha256"] == request["raw_sha256"]]
        assert len(matches) == 1, (stripped, matches)
        record = matches[0]
        assert record["status"] == 200, record
        assert record["headers"]["via"] == "trace-agent " + agent_version, record
        assert record["headers"]["x-datadog-hostname"] == hostname, record
        assert record["headers"]["x-datadog-agentdefaultenv"] == default_env, record
        forwarded.append(record)

    by_path = {record["path"]: record for record in forwarded}
    project_record = by_path[PROJECTS_PATH]
    assert project_record["payload"]["data"]["attributes"]["name"] == PROJECT_NAME, project_record
    create_record = by_path[create_request["path"][len(EVP_PREFIX):]]
    assert create_record["payload"] == create_request["body"], create_record
    # The pinned SDK resolved the project id from the create response and used
    # it in the dataset create path that reached the backend byte-identically.
    assert create_record["path"] == "/api/unstable/llm-obs/v1/" + project_id + "/datasets", create_record
    delete_record = by_path[DATASET_DELETE_PATH]
    assert delete_record["payload"]["data"]["attributes"]["dataset_ids"] == [dataset["dataset_id"]], delete_record

    # The agent reached the synthetic intake through the CONNECT relay.
    assert "api.backend.test:443" in relay_records, relay_records
