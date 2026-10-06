package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
)

type ddOutboundRequest struct {
	RequestID string      `json:"requestId"`
	Host      string      `json:"host"`
	Headers   http.Header `json:"headers"`
}

// Observe the SDK's actual HTTP carrier before forwarding it unchanged to the
// instrumented echo application. Both the wire headers and extracted native
// server spans therefore belong to the same outbound client request.
func ddObserveOutbound(base, output string) (*httptest.Server, error) {
	target, err := url.Parse(base)
	if err != nil {
		return nil, err
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	var mu sync.Mutex
	var requests []ddOutboundRequest
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		requests = append(requests, ddOutboundRequest{r.URL.Query().Get("probe_request_id"), r.Host, r.Header.Clone()})
		data, err := json.MarshalIndent(requests, "", "  ")
		if err == nil {
			err = os.WriteFile(output, data, 0644)
		}
		mu.Unlock()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		proxy.ServeHTTP(w, r)
	})), nil
}

func ddValidateOutboundHeaders(c ddCase, spans []ddNativeSpan, data []byte) error {
	var requests []ddOutboundRequest
	if err := json.Unmarshal(data, &requests); err != nil {
		return err
	}
	roots := ddServers(spans)
	if len(requests) != 4 || len(roots) != 4 {
		return fmt.Errorf("missing or extra outbound header observations")
	}
	style := datadogEnvironment("")["DD_TRACE_PROPAGATION_STYLE_INJECT"]
	if value, ok := c.Env["DD_TRACE_PROPAGATION_STYLE_INJECT"]; ok {
		style = value
	}
	seen := map[string]bool{}
	for _, request := range requests {
		if seen[request.RequestID] {
			return fmt.Errorf("duplicate outbound request header observation")
		}
		seen[request.RequestID] = true
		var root, client ddNativeSpan
		for _, span := range roots {
			if span.Meta["probe.request_id"] == request.RequestID {
				root = span
			}
		}
		for _, span := range spans {
			if span.Type == "http" && span.TraceID == root.TraceID && span.Meta["_dd.p.tid"] == root.Meta["_dd.p.tid"] {
				if client.SpanID != 0 {
					return fmt.Errorf("ambiguous outbound native client span")
				}
				client = span
			}
		}
		if root.SpanID == 0 || client.SpanID == 0 {
			return fmt.Errorf("outbound headers lack their native request/client span")
		}
		if err := ddValidateOutboundCarrier(style, root, client, request.Headers); err != nil {
			return fmt.Errorf("request %s: %w", request.RequestID, err)
		}
	}
	return nil
}

// Datadog, W3C and B3 identity/sampling fields are checked against captured
// native spans. Optional fields stay optional; foreign carriers are forbidden.
func ddValidateOutboundCarrier(style string, root, client ddNativeSpan, headers http.Header) error {
	selected := map[string]bool{}
	for _, value := range strings.Split(style, ",") {
		value = strings.TrimSpace(value)
		if value == "b3 single header" {
			value = "b3"
		}
		selected[value] = true
	}
	families := map[string][]string{
		"datadog":      {"x-datadog-trace-id", "x-datadog-parent-id", "x-datadog-sampling-priority", "x-datadog-tags", "x-datadog-origin"},
		"tracecontext": {"traceparent", "tracestate"},
		"b3":           {"b3"},
		"b3multi":      {"x-b3-traceid", "x-b3-spanid", "x-b3-sampled", "x-b3-flags", "x-b3-parentspanid"},
	}
	for family := range selected {
		if _, ok := families[family]; !ok {
			return fmt.Errorf("unknown outbound propagation style %q", family)
		}
	}
	for family, names := range families {
		for _, name := range names {
			values := headers.Values(name)
			if len(values) > 1 || (!selected[family] && len(values) != 0) {
				return fmt.Errorf("unexpected or duplicate outbound carrier %s", name)
			}
		}
	}
	high := client.Meta["_dd.p.tid"]
	if high == "" {
		high = "0000000000000000"
	}
	trace := high + fmt.Sprintf("%016x", client.TraceID)
	span := fmt.Sprintf("%016x", client.SpanID)
	priority, ok := root.Metrics["_sampling_priority_v1"]
	if !ok || priority != 2 {
		return fmt.Errorf("outbound keep-rule control lacks native sampling decision")
	}
	require := func(name, want string) error {
		if got := headers.Get(name); got != want {
			return fmt.Errorf("outbound %s = %q, want %q", name, got, want)
		}
		return nil
	}
	if selected["datadog"] {
		for name, want := range map[string]string{
			"x-datadog-trace-id":          strconv.FormatUint(client.TraceID, 10),
			"x-datadog-parent-id":         strconv.FormatUint(client.SpanID, 10),
			"x-datadog-sampling-priority": "2",
		} {
			if err := require(name, want); err != nil {
				return err
			}
		}
		tags := map[string]string{}
		for _, pair := range strings.Split(headers.Get("x-datadog-tags"), ",") {
			key, value, ok := strings.Cut(pair, "=")
			if !ok || tags[key] != "" {
				return fmt.Errorf("malformed outbound Datadog propagation tags")
			}
			tags[key] = value
		}
		if tags["_dd.p.tid"] != high || tags["_dd.p.dm"] != root.Meta["_dd.p.dm"] {
			return fmt.Errorf("outbound Datadog propagation tags differ from native trace")
		}
		if headers.Get("x-datadog-origin") != root.Meta["_dd.origin"] {
			return fmt.Errorf("outbound Datadog origin differs from native trace")
		}
	}
	if selected["tracecontext"] {
		if err := require("traceparent", "00-"+trace+"-"+span+"-01"); err != nil {
			return err
		}
		// Vendor state is optional in W3C propagation, but when emitted its
		// Datadog identity and sampling fields must agree with the native trace.
		state := map[string]string{}
		for _, member := range strings.Split(headers.Get("tracestate"), ",") {
			key, value, ok := strings.Cut(strings.TrimSpace(member), "=")
			if key != "dd" {
				continue
			}
			if !ok || len(state) != 0 {
				return fmt.Errorf("malformed or duplicate outbound Datadog tracestate")
			}
			for _, field := range strings.Split(value, ";") {
				key, value, ok := strings.Cut(field, ":")
				if !ok || state[key] != "" {
					return fmt.Errorf("malformed outbound Datadog tracestate field")
				}
				state[key] = value
			}
		}
		if len(state) != 0 {
			if state["s"] != "2" || state["p"] != span || state["t.dm"] != root.Meta["_dd.p.dm"] {
				return fmt.Errorf("outbound Datadog tracestate differs from native client")
			}
			if tid, ok := state["t.tid"]; ok && tid != high {
				return fmt.Errorf("outbound Datadog tracestate trace high bits differ")
			}
		}
	}
	if selected["b3"] {
		parts := strings.Split(headers.Get("b3"), "-")
		if len(parts) < 3 || len(parts) > 4 || parts[0] != trace || parts[1] != span || (parts[2] != "1" && parts[2] != "d") {
			return fmt.Errorf("outbound B3 identity/sampling differs from native client")
		}
		if len(parts) == 4 && parts[3] != fmt.Sprintf("%016x", client.ParentID) {
			return fmt.Errorf("outbound B3 parent differs from native client")
		}
	}
	if selected["b3multi"] {
		if err := require("x-b3-traceid", trace); err != nil {
			return err
		}
		if err := require("x-b3-spanid", span); err != nil {
			return err
		}
		if flag := headers.Get("x-b3-flags"); flag != "" && flag != "1" {
			return fmt.Errorf("invalid outbound B3 debug flag")
		}
		if headers.Get("x-b3-flags") != "1" {
			if err := require("x-b3-sampled", "1"); err != nil {
				return err
			}
		} else if sampled := headers.Get("x-b3-sampled"); sampled != "" && sampled != "1" {
			return fmt.Errorf("inconsistent outbound B3 sampling/debug flags")
		}
		if parent := headers.Get("x-b3-parentspanid"); parent != "" && parent != fmt.Sprintf("%016x", client.ParentID) {
			return fmt.Errorf("outbound B3 parent differs from native client")
		}
	}
	return nil
}
