"""Additional native-intake assertions where upstream's Python fixture has gaps."""
import time
import uuid
class Test_Lab_ServiceMapping:
    def test_explicit_and_inherited_service_mapping(self, test_agent, test_library, library_env):
        with test_library:
            with test_library.dd_start_span("mapped.root", service="source-service") as root:
                with test_library.dd_start_span("mapped.inherited", parent_id=root.span_id) as inherited:
                    pass
                with test_library.dd_start_span("mapped.override", service="second-source", parent_id=root.span_id) as override:
                    pass
                with test_library.dd_start_span("mapped.unchanged", service="unmapped", parent_id=root.span_id) as unchanged:
                    pass
        spans = {span["span_id"]: span for trace in test_agent.wait_for_num_traces(1) for span in trace}
        assert len(spans) == 4
        assert spans[root.span_id]["service"] == "mapped-service"
        assert spans[inherited.span_id]["service"] == "mapped-service"
        assert spans[override.span_id]["service"] == "second-mapped"
        assert spans[unchanged.span_id]["service"] == "unmapped"
        for span in (inherited, override, unchanged):
            assert spans[span.span_id]["parent_id"] == root.span_id
            assert spans[span.span_id]["trace_id"] == spans[root.span_id]["trace_id"]


class Test_Lab_Http:
    def request(self, test_agent, test_library, status=200, query=None, headers=None):
        receipt = test_library.rpc("http_request", status=status, query=query or {}, headers=headers or {})
        test_library.dd_flush()
        spans = [span for trace in test_agent.traces() for span in trace
                 if span["trace_id"] == receipt["control"]["trace_id"] & ((1 << 64) - 1)]
        servers = [span for span in spans if span["span_id"] == receipt["target"]["span_id"]]
        clients = [span for span in spans if span.get("meta", {}).get("component") == "aiohttp_client" and span.get("meta", {}).get("http.url", "").split("?")[0] == receipt["url"]]
        assert len(servers) == len(clients) == 1, spans
        assert servers[0]["type"] == "web" and clients[0]["type"] == "http"
        assert servers[0]["meta"]["http.status_code"] == clients[0]["meta"]["http.status_code"] == str(status)
        assert servers[0]["parent_id"] == clients[0]["span_id"]
        return servers[0], clients[0]

    def test_default_status_semantics(self, test_agent, test_library, library_env):
        for status, error in ((200, 0), (400, 0), (500, 1), (599, 1)):
            server, client = self.request(test_agent, test_library, status)
            assert server.get("error", 0) == client.get("error", 0) == error

    def test_custom_status_ranges(self, test_agent, test_library, library_env):
        for status, server_error, client_error in ((200, 1, 1), (202, 1, 1), (203, 0, 0), (400, 0, 0), (402, 0, 0), (403, 0, 0), (500, 0, 0)):
            server, client = self.request(test_agent, test_library, status)
            assert server.get("error", 0) == server_error, server
            assert client.get("error", 0) == client_error, client

    def test_configured_http_headers(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, headers={"X-Lab-Request": "request-value"})
        for span in (server, client):
            assert span["meta"]["lab.request"] == "request-value", span
            assert span["meta"]["lab.response"] == "response-value", span

    def test_independent_client_status_ranges(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, 200)
        assert server["error"] == 1
        assert client["error"] == 0

    def test_query_redaction_default(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, query={"token": "secret-value", "safe": "visible"})
        for span in (server, client):
            url = span["meta"]["http.url"]
            assert "secret-value" not in url and "<redacted>" in url and "safe=visible" in url, span

    def test_query_redaction_custom(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, query={"ssn": "123-45-6789", "safe": "visible"})
        for span in (server, client):
            url = span["meta"]["http.url"]
            assert "123-45-6789" not in url and "<redacted>" in url and "safe=visible" in url, span

    def test_query_redaction_empty(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, query={"token": "secret-value"})
        for span in (server, client):
            assert span["meta"]["http.url"].endswith("?token=secret-value"), span

    def test_client_query_tagging_disabled(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, query={"safe": "visible"})
        assert "?" not in client["meta"]["http.url"], client

    def test_client_query_tagging_enabled(self, test_agent, test_library, library_env):
        server, client = self.request(test_agent, test_library, query={"safe": "visible"})
        assert client["meta"]["http.url"].endswith("?safe=visible"), client

    def test_referrer_hostname(self, test_agent, test_library, library_env):
        for referrer, expected in (("https://user:pass@example.com:8080/path?query=123#fragment", "example.com"),
                                  ("https://192.0.2.1:8080/path", "192.0.2.1"),
                                  ("invalid-referrer", None), ("", None), ("file:///path", None)):
            server, client = self.request(test_agent, test_library, headers={"Referer": referrer})
            assert server["meta"].get("http.referrer_hostname") == expected, server

    def test_client_integration_disabled(self, test_agent, test_library, library_env):
        receipt = test_library.rpc("http_request", status=200, query={}, headers={})
        test_library.dd_flush()
        spans = [span for trace in test_agent.traces() for span in trace]
        assert any(span["span_id"] == receipt["control"]["span_id"] for span in spans)
        assert not any(span.get("meta", {}).get("component") == "aiohttp_client" for span in spans), spans

    def test_runtime_id_service_entries(self, test_agent, test_library, library_env):
        runtime_ids = []
        for status in (200, 400):
            server, client = self.request(test_agent, test_library, status)
            runtime_id = server["meta"]["runtime-id"]
            assert uuid.UUID(runtime_id).version == 4
            runtime_ids.append(runtime_id)
        assert runtime_ids[0] == runtime_ids[1]


class Test_Lab_MixedContext:
    def test_datadog_and_otel_share_both_parent_directions(self, test_agent, test_library, library_env):
        receipt = test_library.rpc("mixed_context")
        traces = test_agent.wait_for_num_traces(2)
        spans = {span["span_id"]: span for trace in traces for span in trace}
        assert len(spans) == 4
        for parent_name, child_name in (("dd_root", "otel_child"), ("otel_root", "dd_child")):
            parent, child = receipt[parent_name], receipt[child_name]
            assert parent["trace_id"] == child["trace_id"]
            assert spans[child["span_id"]]["parent_id"] == parent["span_id"]
            assert spans[parent["span_id"]]["trace_id"] == parent["trace_id"] & ((1 << 64) - 1)
            assert spans[child["span_id"]]["trace_id"] == spans[parent["span_id"]]["trace_id"]
        assert receipt["dd_root"]["trace_id"] != receipt["otel_root"]["trace_id"]
        assert receipt["dd_current_while_otel"] == receipt["otel_child"]["span_id"]
        assert receipt["otel_current_while_dd"] == receipt["dd_child"]["span_id"]


class Test_Lab_AwsApiGateway:
    def test_inferred_gateway_parent_and_http_errors(self, test_agent, test_library, library_env):
        for status in (200, 500):
            start_ms = int(time.time() * 1000) - 20
            path = "/api/data/" + str(status)
            headers = {"x-dd-proxy": "aws-apigateway", "x-dd-proxy-request-time-ms": str(start_ms),
                "x-dd-proxy-path": path, "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "system-tests-api-gateway.com", "x-dd-proxy-stage": "staging"}
            receipt = test_library.rpc("http_request", status=status, headers=headers, query={})
            test_library.dd_flush()
            spans = [span for trace in test_agent.traces() for span in trace
                if span["trace_id"] == receipt["control"]["trace_id"] & ((1 << 64) - 1)]
            server = next(span for span in spans if span["span_id"] == receipt["target"]["span_id"])
            inferred = [span for span in spans if span["name"] == "aws.apigateway" and span["span_id"] == server["parent_id"]]
            assert len(inferred) == 1, spans
            inferred = inferred[0]
            client = next(span for span in spans if span.get("meta", {}).get("component") == "aiohttp_client")
            assert server["parent_id"] == inferred["span_id"] and inferred["parent_id"] == client["span_id"]
            assert inferred["service"] == "system-tests-api-gateway.com" and inferred["type"] == "web"
            assert inferred["resource"] == "GET " + path and inferred["start"] == start_ms * 1000000
            assert inferred["duration"] > 0 and inferred["metrics"]["_dd.inferred_span"] == 1
            assert inferred["meta"]["component"] == "aws-apigateway" and inferred["meta"]["stage"] == "staging"
            assert inferred["meta"]["http.method"] == "GET" and inferred["meta"]["http.status_code"] == str(status)
            assert inferred["meta"]["http.url"] == "https://system-tests-api-gateway.com" + path
            assert inferred.get("error", 0) == server.get("error", 0) == int(status >= 500)


def cases():
    http = {"DD_LAB_HTTP_MODE": "true"}
    specifications = [
        (Test_Lab_ServiceMapping, "test_explicit_and_inherited_service_mapping", ["dd_service_mapping"],
         {"DD_SERVICE_MAPPING": "source-service:mapped-service,second-source:second-mapped"}),
        (Test_Lab_MixedContext, "test_datadog_and_otel_share_both_parent_directions",
         ["f_otel_interoperability", "otel_api"], {"DD_TRACE_OTEL_ENABLED": "true"}),
        (Test_Lab_AwsApiGateway, "test_inferred_gateway_parent_and_http_errors", ["aws_api_gateway_inferred_span_creation"],
         {**http, "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED": "true"}),
        (Test_Lab_Http, "test_default_status_semantics", ["trace_http_server_error_statuses", "trace_http_client_error_statuses", "integration_enablement"], http),
        (Test_Lab_Http, "test_custom_status_ranges", ["trace_http_server_error_statuses", "trace_http_client_error_statuses"],
         {**http, "DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "200-202", "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "200-202"}),
        (Test_Lab_Http, "test_configured_http_headers", ["http_headers_as_tags_dd_trace_header_tags"],
         {**http, "DD_TRACE_HEADER_TAGS": "x-lab-request:lab.request,x-lab-response:lab.response"}),
        (Test_Lab_Http, "test_independent_client_status_ranges", ["trace_http_client_error_statuses"],
         {**http, "DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "200-202", "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "400-402"}),
        (Test_Lab_Http, "test_query_redaction_default", ["trace_query_string_obfuscation"], http),
        (Test_Lab_Http, "test_query_redaction_custom", ["trace_query_string_obfuscation"],
         {**http, "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP": "ssn=[0-9-]+"}),
        (Test_Lab_Http, "test_query_redaction_empty", ["trace_query_string_obfuscation"],
         {**http, "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP": ""}),
        (Test_Lab_Http, "test_client_query_tagging_disabled", ["trace_http_client_tag_query_string"],
         {**http, "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING": "false"}),
        (Test_Lab_Http, "test_client_query_tagging_enabled", ["trace_http_client_tag_query_string"],
         {**http, "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING": "true"}),
        (Test_Lab_Http, "test_referrer_hostname", ["referrer_hostname"], http),
        (Test_Lab_Http, "test_client_integration_disabled", ["integration_enablement"],
         {**http, "DD_TRACE_AIOHTTP_ENABLED": "false"}),
        (Test_Lab_Http, "test_runtime_id_service_entries", ["runtime_id_in_span_metadata_for_service_entry_spans"], http),
    ]
    return [{"name": "local_cases." + cls.__name__ + "." + method + "[0]",
             "file": "local_cases.py", "class": cls.__name__, "method": method,
             "features": features, "parameters": {"library_env": env},
             "instance": cls(), "function": getattr(cls, method), "local": True}
            for cls, method, features, env in specifications]
