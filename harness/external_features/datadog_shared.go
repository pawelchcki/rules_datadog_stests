package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const datadogInventoryRevision = "098fe0967c587db8a16b74a1e711777d0a9d5867"

func ddSelectCases(name string) ([]ddCase, error) {
	cases := append(ddCases(), ddProbeCases()...)
	if name == "" {
		return cases, nil
	}
	for _, c := range cases {
		if c.Name == name {
			return []ddCase{c}, nil
		}
	}
	return nil, fmt.Errorf("unknown shared Datadog contract: %q", name)
}

func ddValidateUpstreamInputs(cases []ddCase, adapter, source string) error {
	for _, c := range cases {
		if c.UpstreamMethod != "" && (adapter == "" || source == "") {
			return fmt.Errorf("%s requires its upstream adapter and pinned test source", c.Name)
		}
	}
	return nil
}

// These workloads and assertions are identical for every native SDK fixture.
// Fixture launch/configuration adaptation belongs in runDatadog, not here.
func ddSharedCases() []ddCase {
	errorFlag := 1
	keep := 2
	cases := []ddCase{
		{Name: "runtime-identity", RuntimeIdentity: true, Source: "test_tracer.py"},
		{Name: "tags-unicode", Source: "test_config_consistency.py", SourceRevision: datadogConfigRevision,
			ReferenceClass: "Test_Config_Tags", ReferenceTest: "test_comma_space_tag_separation",
			Env: map[string]string{"DD_TAGS": "probe.unicode:zażółć-日本語-🚀", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}, ExpectedTags: map[string]string{"probe.unicode": "zażółć-日本語-🚀"}},
		{Name: "sample-service-glob", Source: "test_trace_sampling.py", Priority: &keep,
			Env: map[string]string{"DD_TRACE_SAMPLING_RULES": `[{"service":"external-*","sample_rate":1}]`}},
		{Name: "server-custom-error", Source: "test_config_consistency.py", SourceRevision: datadogConfigRevision, HTTPError: &errorFlag,
			ReferencePath: "tests/test_config_consistency.py", ReferenceClass: "Test_Config_HttpServerErrorStatuses_FeatureFlagCustom", ReferenceTest: "test_status_code_200",
			Env: map[string]string{"DD_TRACE_HTTP_SERVER_ERROR_STATUSES": "200-299"}},
		{Name: "client-ip-override", Source: "test_config_consistency.py", SourceRevision: datadogConfigRevision,
			ReferencePath: "tests/test_config_consistency.py", ReferenceClass: "Test_Config_ClientIPHeader_Configured", ReferenceTest: "test_ip_headers_sent_in_one_request",
			Env:          map[string]string{"DD_TRACE_CLIENT_IP_ENABLED": "true", "DD_TRACE_CLIENT_IP_HEADER": "x-shared-client-ip"},
			Headers:      map[string]string{"x-shared-client-ip": "203.0.113.42", "x-forwarded-for": "198.51.100.7"},
			ExpectedTags: map[string]string{"http.client_ip": "203.0.113.42"}},
	}
	// Malformed identities must start a new local root. A successful HTTP
	// response alone supplies no evidence that extraction rejected the carrier.
	for _, item := range []struct{ name, parent string }{
		{"zero-trace", "00-00000000000000000000000000000000-000000003ade68b1-01"},
		{"zero-parent", "00-1234567890abcdef00000000075bcd15-0000000000000000-01"},
		{"invalid-hex", "00-zzzzzzzzzzzzzzzz00000000075bcd15-000000003ade68b1-01"},
		{"forbidden-version", "ff-1234567890abcdef00000000075bcd15-000000003ade68b1-01"},
		{"short-trace", "00-075bcd15-000000003ade68b1-01"},
	} {
		cases = append(cases, ddCase{Name: "tracecontext-" + item.name, Source: "test_headers_tracecontext.py",
			Env: map[string]string{"DD_TRACE_PROPAGATION_STYLE_EXTRACT": "tracecontext"}, Headers: map[string]string{"traceparent": item.parent}})
	}
	for _, item := range []struct {
		name, style string
		headers     map[string]string
	}{
		{"b3-zero", "b3", map[string]string{"b3": "0-0-1"}},
		{"b3-invalid-hex", "b3", map[string]string{"b3": "zzzzzzzzzzzzzzzz-000000003ade68b1-1"}},
		{"b3multi-zero", "b3multi", map[string]string{"x-b3-traceid": "0", "x-b3-spanid": "0", "x-b3-sampled": "1"}},
		{"b3multi-invalid-hex", "b3multi", map[string]string{"x-b3-traceid": "zzzzzzzzzzzzzzzz", "x-b3-spanid": "000000003ade68b1"}},
	} {
		cases = append(cases, ddCase{Name: item.name, Source: "test_headers_" + item.style + ".py",
			Env: map[string]string{"DD_TRACE_PROPAGATION_STYLE_EXTRACT": item.style}, Headers: item.headers})
	}
	for _, style := range []string{"datadog", "tracecontext", "b3", "b3multi"} {
		cases = append(cases, ddCase{Name: "outbound-" + style, Kind: "outbound", Source: "test_headers_" + style + ".py",
			Env: map[string]string{"RULES_STESTS_PROBES": "true", "DD_TRACE_PROPAGATION_STYLE_EXTRACT": style, "DD_TRACE_PROPAGATION_STYLE_INJECT": style}})
	}
	return cases
}

func ddLanguage(app string) string {
	if rubyApp(app) {
		return "ruby"
	}
	if app == "gin" {
		return "go"
	}
	if app == "aiohttp" || app == "django" {
		return "python"
	}
	return ""
}

func ddRuntimeID(value string) bool {
	if len(value) == 36 {
		if value[8] != '-' || value[13] != '-' || value[18] != '-' || value[23] != '-' {
			return false
		}
		value = strings.ReplaceAll(value, "-", "")
	}
	if len(value) != 32 || value == strings.Repeat("0", 32) {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

// Groups state the exact shared claim; other behaviors in the same upstream
// capability remain outside this scope and never become passing evidence.
func ddSharedGroups() map[string][]string {
	return map[string][]string{
		"trace_agent_connection":                              {"agent-url-precedence"},
		"trace_enablement":                                    {"disabled"},
		"trace_global_tags":                                   {"tags-comma", "tags-space", "tags-colon-value", "tags-unicode", "tags-identity-precedence"},
		"unified_service_tagging":                             {"identity", "tags-identity-precedence"},
		"trace_id_128_bit_generation_propagation":             {"generate-128", "generate-64", "extract-64", "datadog", "tracecontext"},
		"datadog_headers_propagation":                         {"extract-64", "datadog", "malformed", "malformed-zero", "malformed-overflow", "outbound-datadog"},
		"w3c_headers_injection_and_extraction":                {"tracecontext", "tracecontext-zero-trace", "tracecontext-zero-parent", "tracecontext-invalid-hex", "tracecontext-forbidden-version", "tracecontext-short-trace", "outbound-tracecontext"},
		"b3_headers_propagation":                              {"b3", "b3-zero", "b3-invalid-hex", "outbound-b3"},
		"b3multi_headers_propagation":                         {"b3multi", "b3multi-zero", "b3multi-invalid-hex", "outbound-b3multi"},
		"context_propagation_extract_behavior":                {"none", "malformed", "precedence"},
		"trace_sampling":                                      {"sample-one", "sample-zero", "rule-precedence", "sample-service-glob", "manual-keep", "manual-drop"},
		"partial_flush":                                       {"partial-1", "partial-2", "partial-1000", "partial-disabled"},
		"trace_query_string_obfuscation":                      {"query-redaction"},
		"trace_http_server_error_statuses":                    {"server-custom-error"},
		"trace_client_ip_header":                              {"client-ip-override"},
		"runtime_id_in_span_metadata_for_service_entry_spans": {"runtime-identity"},
		"trace_data_integrity":                                {"nested", "outbound", "exception"},
		"http_headers_as_tags_dd_trace_header_tags":           {"head", "outbound"},
		"trace_annotation":                                    {"nested"},
	}
}

func ddCapabilities(name string) []string {
	result := []string{}
	for feature, cases := range ddSharedGroups() {
		for _, item := range cases {
			if item == name {
				result = append(result, feature)
				break
			}
		}
	}
	sort.Strings(result)
	return result
}

func ddWriteCapabilityResults(out string, results []ddResult) error {
	for _, result := range results {
		if result.Language == "" {
			return fmt.Errorf("unknown native fixture application: %s", result.Application)
		}
	}
	data, err := json.MarshalIndent(struct {
		Results []ddResult `json:"results"`
	}{results}, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(out, "datadog-shared-results.json"), data, 0644)
}

// Compare observed behavioral fields between fresh processes. Generated IDs,
// timestamps, durations, ports, process IDs and runtime IDs vary by design;
// their validity and parentage are checked independently on each execution.
func ddNormalizedResponse(c ddCase, spans []ddNativeSpan) ([]byte, error) {
	index := map[uint64]ddNativeSpan{}
	for _, span := range spans {
		index[span.SpanID] = span
	}
	var rows []string
	for _, span := range spans {
		server := span.Meta["probe.request_id"] != "" && span.Meta["span.kind"] == "server"
		if !server && !strings.HasPrefix(span.Name, "probe.") && !(c.Kind == "outbound" && (span.Type == "http" || span.Type == "web")) {
			continue
		}
		request := span.Meta["probe.request_id"]
		ancestor := span
		for depth := 0; request == "" && depth < 64; depth++ {
			parent, present := index[ancestor.ParentID]
			if !present {
				break
			}
			request = parent.Meta["probe.request_id"]
			ancestor = parent
		}
		parent := "local-root"
		if span.ParentID != 0 {
			if value, present := index[span.ParentID]; present {
				parent = value.Name + ":" + value.Resource
			} else {
				parent = "remote-parent"
			}
		}
		meta := map[string]string{}
		for key, value := range span.Meta {
			_, expected := c.ExpectedTags[key]
			if expected || strings.HasPrefix(key, "probe.") || key == "http.method" || key == "http.status_code" || key == "error.type" || key == "error.message" || key == "_dd.p.dm" || key == "_dd.origin" {
				meta[key] = value
			}
			if key == "http.url" {
				parsed, err := url.Parse(value)
				if err != nil {
					return nil, err
				}
				// The outbound fixture embeds its fresh echo listener in the
				// request URL. Preserve its host, path and parameters, while
				// removing the generated loopback listener port.
				if c.Kind == "outbound" && parsed.Path == "/__rules_stests/outbound" {
					query, err := url.ParseQuery(parsed.RawQuery)
					if err != nil {
						return nil, err
					}
					target, err := url.Parse(query.Get("url"))
					if err != nil {
						return nil, err
					}
					if target.Hostname() == "127.0.0.1" || target.Hostname() == "localhost" {
						target.Host = target.Hostname()
						query.Set("url", target.String())
						parsed.RawQuery = query.Encode()
					}
				}
				meta[key] = parsed.EscapedPath()
				if parsed.RawQuery != "" {
					meta[key] += "?" + parsed.RawQuery
				}
			}
		}
		metrics := map[string]float64{}
		for _, key := range []string{"_sampling_priority_v1", "_dd.rule_psr"} {
			if value, present := span.Metrics[key]; present {
				metrics[key] = value
			}
		}
		row, err := json.Marshal(struct {
			Request, Name, Service, Resource, Type, Parent string
			Error                                          int
			Meta                                           map[string]string
			Metrics                                        map[string]float64
		}{request, span.Name, span.Service, span.Resource, span.Type, parent, span.Error, meta, metrics})
		if err != nil {
			return nil, err
		}
		rows = append(rows, string(row))
	}
	sort.Strings(rows)
	return json.MarshalIndent(rows, "", "  ")
}

type ddExpectedDefect struct {
	Reason string
	Match  func(ddCase, []ddNativeSpan, []ddNativeSpan) bool
}

func ddCaseOutcome(defect ddExpectedDefect, c ddCase, baseline, spans []ddNativeSpan, validationErr error) (string, error) {
	if defect.Match != nil {
		if validationErr == nil {
			return "failed", fmt.Errorf("%s unexpectedly passed; review the recorded SDK defect: %s", c.Name, defect.Reason)
		}
		if defect.Match(c, baseline, spans) {
			return "unsupported", nil
		}
	}
	if validationErr != nil {
		return "failed", validationErr
	}
	return "passed", nil
}
