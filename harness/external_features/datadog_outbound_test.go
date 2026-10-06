package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func outboundHeaderFixture(style string, root, client ddNativeSpan) http.Header {
	h := http.Header{}
	trace := client.Meta["_dd.p.tid"] + fmt.Sprintf("%016x", client.TraceID)
	span := fmt.Sprintf("%016x", client.SpanID)
	for _, style := range strings.Split(style, ",") {
		switch style {
		case "datadog":
			h.Set("x-datadog-trace-id", fmt.Sprint(client.TraceID))
			h.Set("x-datadog-parent-id", fmt.Sprint(client.SpanID))
			h.Set("x-datadog-sampling-priority", "2")
			h.Set("x-datadog-tags", "_dd.p.tid="+client.Meta["_dd.p.tid"]+",_dd.p.dm="+root.Meta["_dd.p.dm"])
		case "tracecontext":
			h.Set("traceparent", "00-"+trace+"-"+span+"-01")
		case "b3", "b3 single header":
			h.Set("b3", trace+"-"+span+"-1")
		case "b3multi":
			h.Set("x-b3-traceid", trace)
			h.Set("x-b3-spanid", span)
			h.Set("x-b3-sampled", "1")
		}
	}
	return h
}

func TestOutboundCarrierChecksNativeIdentitySamplingAndConfiguredStyles(t *testing.T) {
	spans, _ := probeFixture("outbound", false)
	client := spans[0]
	root := ddServers(spans)[0]
	root.Meta["_dd.p.dm"] = "-3"
	for _, style := range []string{"datadog", "tracecontext", "b3", "b3multi", "datadog,tracecontext", "b3 single header"} {
		t.Run(style, func(t *testing.T) {
			root.Metrics["_sampling_priority_v1"] = 2
			headers := outboundHeaderFixture(style, root, client)
			if err := ddValidateOutboundCarrier(style, root, client, headers); err != nil {
				t.Fatal(err)
			}
			for name := range headers {
				for _, mutate := range []func(http.Header){
					func(h http.Header) { h.Del(name) },
					func(h http.Header) { h.Set(name, "wrong") },
					func(h http.Header) { h.Add(name, h.Get(name)) },
				} {
					changed := headers.Clone()
					mutate(changed)
					if ddValidateOutboundCarrier(style, root, client, changed) == nil {
						t.Fatalf("missing, corrupt or duplicate %s accepted", name)
					}
				}
			}
			for _, family := range []struct{ style, name string }{{"datadog", "x-datadog-origin"}, {"tracecontext", "tracestate"}, {"b3", "b3"}, {"b3multi", "x-b3-flags"}} {
				if strings.Contains(style, family.style) {
					continue
				}
				changed := headers.Clone()
				changed.Set(family.name, "")
				if ddValidateOutboundCarrier(style, root, client, changed) == nil {
					t.Fatalf("unconfigured %s accepted", family.name)
				}
			}
			changed := client
			changed.SpanID++
			if ddValidateOutboundCarrier(style, root, changed, headers) == nil {
				t.Fatal("carrier continued the wrong client span")
			}
			changed = client
			changed.TraceID++
			if ddValidateOutboundCarrier(style, root, changed, headers) == nil {
				t.Fatal("carrier continued the wrong trace")
			}
			root.Metrics["_sampling_priority_v1"] = 0
			if ddValidateOutboundCarrier(style, root, client, headers) == nil {
				t.Fatal("carrier disagreed with native sampling decision")
			}
		})
	}
}

func TestOutboundHeaderEvidenceRequiresEveryNativeClientRequest(t *testing.T) {
	spans, _ := probeFixture("outbound", false)
	requests := []ddOutboundRequest{}
	for _, root := range ddServers(spans) {
		root.Metrics["_sampling_priority_v1"] = 2
		root.Meta["_dd.p.dm"] = "-3"
		for _, client := range spans {
			if client.Type == "http" && client.TraceID == root.TraceID {
				requests = append(requests, ddOutboundRequest{RequestID: root.Meta["probe.request_id"], Headers: outboundHeaderFixture("datadog", root, client)})
			}
		}
	}
	c := ddCase{Env: map[string]string{"DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog"}}
	check := func(requests []ddOutboundRequest) error {
		data, err := json.Marshal(requests)
		if err != nil {
			t.Fatal(err)
		}
		return ddValidateOutboundHeaders(c, spans, data)
	}
	if err := check(requests); err != nil {
		t.Fatal(err)
	}
	for _, requests := range [][]ddOutboundRequest{requests[:3], append(append([]ddOutboundRequest{}, requests...), requests[0]), {requests[0], requests[0], requests[2], requests[3]}} {
		if check(requests) == nil {
			t.Fatal("missing, extra or duplicate observed request accepted")
		}
	}
	requests[0].RequestID = "unknown"
	if check(requests) == nil {
		t.Fatal("headers unbound to a native request accepted")
	}
}

func TestOutboundVendorStateCannotContradictNativeIdentity(t *testing.T) {
	spans, _ := probeFixture("outbound", false)
	client, root := spans[0], ddServers(spans)[0]
	root.Metrics["_sampling_priority_v1"] = 2
	root.Meta["_dd.p.dm"] = "-3"
	state := fmt.Sprintf("dd=p:%016x;s:2;t.dm:-3;t.tid:%s,other=opaque", client.SpanID, client.Meta["_dd.p.tid"])
	headers := outboundHeaderFixture("tracecontext", root, client)
	headers.Set("tracestate", state)
	if err := ddValidateOutboundCarrier("tracecontext", root, client, headers); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{
		strings.Replace(state, "s:2", "s:0", 1),
		strings.Replace(state, fmt.Sprintf("p:%016x", client.SpanID), "p:0000000000000001", 1),
		strings.Replace(state, "t.dm:-3", "t.dm:-4", 1),
		strings.Replace(state, client.Meta["_dd.p.tid"], "aaaaaaaaaaaaaaaa", 1),
		state + ",dd=s:2",
		strings.Replace(state, "s:2", "s:2;s:2", 1),
	} {
		headers.Set("tracestate", bad)
		if ddValidateOutboundCarrier("tracecontext", root, client, headers) == nil {
			t.Fatalf("corrupt or duplicate vendor state accepted: %s", bad)
		}
	}
	headers = outboundHeaderFixture("datadog", root, client)
	headers.Set("x-datadog-origin", "unexpected-origin")
	if ddValidateOutboundCarrier("datadog", root, client, headers) == nil {
		t.Fatal("carrier introduced an origin absent from native trace")
	}
}

func TestOutboundObserverRetainsAndForwardsActualWireHeaders(t *testing.T) {
	header := "00-1234567890abcdef00000000075bcd15-000000003ade68b1-01"
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/__rules_stests/echo" || r.Header.Get("traceparent") != header {
			t.Error("forwarding changed the outbound route or propagation carrier")
		}
		w.Write([]byte("echo response"))
	}))
	defer app.Close()
	path := filepath.Join(t.TempDir(), "headers.json")
	observer, err := ddObserveOutbound(app.URL, path)
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close()
	req, _ := http.NewRequest("GET", observer.URL+"/__rules_stests/echo?probe_request_id=1", nil)
	req.Header.Set("traceparent", header)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if string(data) != "echo response" {
		t.Fatal("observer changed the instrumented echo response")
	}
	data, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var requests []ddOutboundRequest
	if err := json.Unmarshal(data, &requests); err != nil || len(requests) != 1 || requests[0].RequestID != "1" || requests[0].Headers.Get("traceparent") != header {
		t.Fatalf("actual received headers not retained: %s (%v)", data, err)
	}
}
