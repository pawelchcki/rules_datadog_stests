"""Common assertions for existing capability scopes with framework-neutral spans."""
from urllib.parse import urlsplit
import time
from local_cases import Test_Lab_Http as UpstreamLabHTTP


class ServiceMapping:
    def mapped_services(self, test_agent, test_library):
        with test_library:
            with test_library.dd_start_span("mapped.root", service="source-service") as root:
                with test_library.dd_start_span("mapped.inherited", parent_id=root.span_id) as child:
                    pass
                with test_library.dd_start_span("mapped.override", service="second-source", parent_id=root.span_id) as other:
                    pass
                with test_library.dd_start_span("mapped.unchanged", service="unmapped", parent_id=root.span_id) as unchanged:
                    pass
        native = {span["span_id"]: span for trace in test_agent.wait_for_num_traces(1) for span in trace}
        assert len(native) == 4
        assert native[root.span_id]["service"] == native[child.span_id]["service"] == "mapped-service"
        assert native[other.span_id]["service"] == "second-mapped"
        assert native[unchanged.span_id]["service"] == "unmapped"
        for span in (child, other, unchanged):
            assert native[span.span_id]["parent_id"] == root.span_id
            assert native[span.span_id]["trace_id"] == native[root.span_id]["trace_id"]


class HTTP(UpstreamLabHTTP):
    def request(self, test_agent, test_library, status=200, query=None, headers=None):
        receipt = test_library.rpc("http_request", status=status, query=query or {}, headers=headers or {})
        test_library.dd_flush()
        for _ in range(40):
            traces = test_agent.traces()
            if all(any(span["span_id"] == receipt[key]["span_id"] for trace in traces for span in trace)
                   for key in ("control", "target")):
                break
            time.sleep(0.05)
        spans = [span for trace in traces for span in trace
                 if span["trace_id"] == receipt["control"]["trace_id"] & ((1 << 64) - 1)]
        controls = [span for span in spans if span["span_id"] == receipt["control"]["span_id"]]
        servers = [span for span in spans if span["span_id"] == receipt["target"]["span_id"]]
        clients = [span for span in spans if span.get("type") == "http"
                   and urlsplit(span.get("meta", {}).get("http.url", "")).path == urlsplit(receipt["url"]).path]
        assert len(controls) == len(servers) == len(clients) == 1, spans
        server, client = servers[0], clients[0]
        assert server["parent_id"] == client["span_id"] and client["parent_id"] == controls[0]["span_id"]
        assert server["meta"]["http.status_code"] == client["meta"]["http.status_code"] == str(status)
        assert server["type"] == "web" and client["type"] == "http"
        assert server["meta"]["component"] and client["meta"]["component"]
        return server, client

    def query_enabled(self, test_agent, test_library):
        _, client = self.request(test_agent, test_library, query={"safe": "visible"})
        assert urlsplit(client["meta"]["http.url"]).query == "safe=visible"

    def query_disabled(self, test_agent, test_library):
        _, client = self.request(test_agent, test_library, query={"safe": "visible"})
        assert not urlsplit(client["meta"]["http.url"]).query

    def baggage_tags(self, test_agent, test_library):
        server, _ = self.request(test_agent, test_library, headers={"baggage": "user.id=doggo,session.id=controlled-session,other=private"})
        assert server["meta"]["baggage.user.id"] == "doggo"
        assert server["meta"]["baggage.session.id"] == "controlled-session"
        assert "baggage.other" not in server["meta"]

    def request_header_tags(self, test_agent, test_library):
        server, _ = self.request(test_agent, test_library, headers={"X-Lab-Request": "request-value"})
        assert server["meta"]["lab.request"] == "request-value", server

    def matching_status_ranges(self, test_agent, test_library):
        for status, error in ((200, 0), (400, 0), (500, 1), (599, 1)):
            server, client = self.request(test_agent, test_library, status)
            assert server.get("error", 0) == client.get("error", 0) == error

    def referrer(self, test_agent, test_library):
        for referrer, expected in (("https://example.test/path?q=value", "example.test"), ("https://user:password@example.test:8443/path", "example.test"), ("invalid-referrer", None), ("", None)):
            server, _ = self.request(test_agent, test_library, headers={"Referer": referrer})
            assert server["meta"].get("http.referrer_hostname") == expected


def cases():
    specifications = [
        (ServiceMapping, "mapped_services", ["dd_service_mapping"],
         {"DD_SERVICE_MAPPING": "source-service:mapped-service,second-source:second-mapped"}),
        (HTTP, "test_independent_client_status_ranges", ["trace_http_client_error_statuses"],
         {"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "200-202", "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "400-402"}),
        (HTTP, "query_enabled", ["trace_http_client_tag_query_string"], {"DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING": "true"}),
        (HTTP, "query_disabled", ["trace_http_client_tag_query_string"], {"DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING": "false"}),
        (HTTP, "baggage_tags", ["baggage_span_tags"],
         {"DD_TRACE_BAGGAGE_TAG_KEYS": "user.id,session.id", "DD_TRACE_PROPAGATION_STYLE_EXTRACT": "datadog,tracecontext,baggage",
          "DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog,tracecontext,baggage"}),
        (HTTP, "test_referrer_hostname", ["referrer_hostname"], {}),
        (HTTP, "matching_status_ranges", ["trace_http_server_error_statuses", "trace_http_client_error_statuses", "integration_enablement"],
         {"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "500-599", "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "500-599"}),
        (HTTP, "test_custom_status_ranges", ["trace_http_server_error_statuses", "trace_http_client_error_statuses"],
         {"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "200-202", "DD_TRACE_HTTP_CLIENT_ERROR_STATUSES": "200-202"}),
        (HTTP, "request_header_tags", ["http_headers_as_tags_dd_trace_header_tags"],
         {"DD_TRACE_HEADER_TAGS": "x-lab-request:lab.request,x-lab-response:lab.response"}),
        (HTTP, "test_query_redaction_default", ["trace_query_string_obfuscation"], {}),
        (HTTP, "test_query_redaction_custom", ["trace_query_string_obfuscation"],
         {"DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP": "ssn=[0-9-]+"}),
        (HTTP, "test_query_redaction_empty", ["trace_query_string_obfuscation"],
         {"DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP": ""}),
    ]
    results = []
    for cls, method, features, env in specifications:
        inherited = method.startswith("test_")
        origin = "local_cases.Test_Lab_Http" if inherited else "portable_cases." + cls.__name__
        case = {"name": origin + "." + method + "[0]",
                "file": "local_cases.py" if inherited else "portable_cases.py",
                "class": "Test_Lab_Http" if inherited else cls.__name__, "method": method,
                "features": features,
                "parameters": {"library_env": env if cls is ServiceMapping else {"DD_LAB_HTTP_MODE": "true", **env}},
                "instance": cls(), "function": getattr(cls, method), "local": True}
        if "referrer_hostname" in features:
            case["upstreamSelector"] = "tests/test_standard_tags.py::Test_StandardTagsReferrerHostname::test_referrer_hostname"
        results.append(case)
    return results
