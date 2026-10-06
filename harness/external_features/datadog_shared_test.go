package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"testing"
)

func TestIndependentBazelContractsMatchRunnerAndRejectUnknownCases(t *testing.T) {
	root := filepath.Join(os.Getenv("TEST_SRCDIR"), os.Getenv("TEST_WORKSPACE"))
	data, err := os.ReadFile(filepath.Join(root, "fixtures", "external_features.bzl"))
	if err != nil {
		t.Fatal(err)
	}
	registry := regexp.MustCompile(`(?s)_DATADOG_SHARED_CASES = \[(.*?)\n\]`).FindSubmatch(data)
	if len(registry) != 2 {
		t.Fatal("missing independent Bazel contract registry")
	}
	names := regexp.MustCompile(`"([a-z0-9-]+)"`).FindAllSubmatch(registry[1], -1)
	cases, err := ddSelectCases("")
	if err != nil || len(names) != len(cases) {
		t.Fatalf("Bazel registry has %d cases, runner has %d: %v", len(names), len(cases), err)
	}
	seen := map[string]bool{}
	upstream := regexp.MustCompile(`_DATADOG_UPSTREAM_CASES = \[(.*?)\]`).FindSubmatch(data)
	if len(upstream) != 2 {
		t.Fatal("missing upstream method dependency registry")
	}
	upstreamNames := map[string]bool{}
	for _, item := range regexp.MustCompile(`"([a-z0-9-]+)"`).FindAllSubmatch(upstream[1], -1) {
		upstreamNames[string(item[1])] = true
	}
	for _, name := range names {
		key := string(name[1])
		if seen[key] {
			t.Fatalf("duplicate Bazel contract %q", key)
		}
		seen[key] = true
		selected, err := ddSelectCases(key)
		if err != nil || len(selected) != 1 || selected[0].Name != key {
			t.Fatalf("independent contract %q did not select exactly one case: %v", key, err)
		}
	}
	for _, c := range cases {
		selected, err := ddSelectCases(c.Name)
		if !seen[c.Name] || err != nil || !reflect.DeepEqual(selected, []ddCase{c}) {
			t.Fatalf("case %q differs between independent and diagnostic runs", c.Name)
		}
		needsUpstream := c.UpstreamMethod != ""
		if upstreamNames[c.Name] != needsUpstream {
			t.Fatalf("Bazel adapter dependencies disagree with %q's executed method", c.Name)
		}
		for _, inputs := range [][2]string{{"", ""}, {"adapter", ""}, {"", "source"}, {"adapter", "source"}} {
			err := ddValidateUpstreamInputs(selected, inputs[0], inputs[1])
			wantError := needsUpstream && (inputs[0] == "" || inputs[1] == "")
			if (err != nil) != wantError {
				t.Fatalf("upstream requirements for %q with %v: %v", c.Name, inputs, err)
			}
		}
		delete(upstreamNames, c.Name)
	}
	if len(upstreamNames) != 0 {
		t.Fatalf("unknown upstream dependency cases: %v", upstreamNames)
	}
	for _, name := range []string{"unknown", "shared-origin", "origin,identity", "origin/identity"} {
		if selected, err := ddSelectCases(name); err == nil || len(selected) != 0 {
			t.Fatalf("unknown selector %q must fail, not run another case", name)
		}
	}
}

func TestSharedCatalogMatchesExecutableCasesAndPinnedInventory(t *testing.T) {
	read := func(name string, dst any) {
		t.Helper()
		root := filepath.Join(os.Getenv("TEST_SRCDIR"), os.Getenv("TEST_WORKSPACE"))
		data, err := os.ReadFile(filepath.Join(root, "docs", name))
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(data, dst); err != nil {
			t.Fatal(err)
		}
	}
	var inventory struct {
		Revision string                  `json:"revision"`
		Features []struct{ Name string } `json:"features"`
	}
	read("datadog-capabilities-inventory.json", &inventory)
	if inventory.Revision != datadogInventoryRevision {
		t.Fatal("shared claims use a stale inventory revision")
	}
	features := map[string]bool{}
	for _, feature := range inventory.Features {
		features[feature.Name] = true
	}
	var mapping struct {
		Revision     string `json:"upstreamRevision"`
		Capabilities []struct {
			Name          string
			RequiredCases []string `json:"requiredCases"`
		} `json:"capabilities"`
	}
	read("datadog-shared-capabilities-mapping.json", &mapping)
	if mapping.Revision != datadogInventoryRevision {
		t.Fatal("shared mapping uses a stale inventory revision")
	}
	cases := map[string]bool{}
	for _, c := range append(ddCases(), ddProbeCases()...) {
		if cases[c.Name] {
			t.Fatalf("duplicate executable case %s", c.Name)
		}
		cases[c.Name] = true
	}
	groups := ddSharedGroups()
	if len(groups) != len(mapping.Capabilities) {
		t.Fatal("shared mappings and executable claim groups differ")
	}
	seen := map[string]bool{}
	for _, row := range mapping.Capabilities {
		if !features[row.Name] || seen[row.Name] {
			t.Fatalf("unknown or duplicate capability %s", row.Name)
		}
		seen[row.Name] = true
		var want []string
		for _, name := range groups[row.Name] {
			if !cases[name] {
				t.Fatalf("mapped %s has no executable case %s", row.Name, name)
			}
			want = append(want, "shared-"+name)
		}
		if !reflect.DeepEqual(want, row.RequiredCases) {
			t.Fatalf("required cases differ for %s: %v / %v", row.Name, want, row.RequiredCases)
		}
	}
	for name := range cases {
		claims := ddCapabilities(name)
		if !sort.StringsAreSorted(claims) {
			t.Fatal("receipt claims must be deterministic")
		}
	}
}

func TestSharedRuntimeIdentityRejectsMissingMalformedAndChangingMetadata(t *testing.T) {
	good := "37e6f28b-d416-438e-8e8a-ccaf0d1a6e51"
	for _, value := range []string{good, "37e6f28bd416438e8e8accaf0d1a6e51"} {
		if !ddRuntimeID(value) {
			t.Fatalf("valid SDK spelling rejected: %s", value)
		}
	}
	for _, value := range []string{"", "00000000-0000-0000-0000-000000000000", "37e6f28b-d416-438e-8e8a-ccaf0d1a6e5z", "37e6f28bd-416-438e-8e8a-ccaf0d1a6e51"} {
		if ddRuntimeID(value) {
			t.Fatalf("malformed runtime ID accepted: %s", value)
		}
	}
	c := ddCase{RuntimeIdentity: true}
	fixture := func() []ddNativeSpan {
		spans := ddBaselineFixture()
		for i := range spans {
			spans[i].Meta["runtime-id"] = good
			spans[i].Metrics["process_id"] = 123
		}
		return spans
	}
	if err := validateDatadogCase(c, fixture(), fixture()); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*ddNativeSpan){
		func(s *ddNativeSpan) { delete(s.Meta, "runtime-id") },
		func(s *ddNativeSpan) { s.Meta["runtime-id"] = "27e6f28b-d416-438e-8e8a-ccaf0d1a6e51" },
		func(s *ddNativeSpan) { s.Metrics["process_id"] = 0 },
		func(s *ddNativeSpan) { s.TraceID = 0 },
		func(s *ddNativeSpan) { s.SpanID = 0 },
		func(s *ddNativeSpan) { s.SpanID = 1 },
	} {
		spans := fixture()
		mutate(&spans[1])
		if validateDatadogCase(c, fixture(), spans) == nil {
			t.Fatal("corrupted native runtime/trace identity accepted")
		}
	}
}

func TestSharedRepeatedExecutionNormalizesFreshIdentityButKeepsBehavior(t *testing.T) {
	first := ddBaselineFixture()
	second := ddBaselineFixture()
	for i := range first {
		first[i].Meta["http.url"] = "http://127.0.0.1:1000/api/tags?safe=visible"
		second[i].Meta["http.url"] = "http://127.0.0.1:2000/api/tags?safe=visible"
		second[i].TraceID += 100
		second[i].SpanID += 100
		second[i].Start += 999
		second[i].Duration += 999
		second[i].Meta["runtime-id"] = "different fresh process"
		second[i].Metrics["process_id"] = 456
	}
	response := func(spans []ddNativeSpan) []byte {
		t.Helper()
		data, err := ddNormalizedResponse(ddCase{}, spans)
		if err != nil {
			t.Fatal(err)
		}
		return data
	}
	if !bytes.Equal(response(first), response(second)) {
		t.Fatal("fresh process identities or endpoint ports changed behavioral response")
	}
	for _, change := range []func(*ddNativeSpan){
		func(s *ddNativeSpan) { s.Error = 1 },
		func(s *ddNativeSpan) { s.ParentID = 999 },
		func(s *ddNativeSpan) { s.Meta["http.url"] += "&lost=control" },
		func(s *ddNativeSpan) { s.Metrics["_sampling_priority_v1"] = -1 },
	} {
		changed := ddBaselineFixture()
		for i := range changed {
			changed[i].Meta["http.url"] = first[i].Meta["http.url"]
		}
		change(&changed[0])
		if bytes.Equal(response(first), response(changed)) {
			t.Fatal("behavioral change disappeared during response normalization")
		}
	}
}

func TestSharedOutboundResponseNormalizesOnlyFreshLoopbackPorts(t *testing.T) {
	response := func(target string) []byte {
		t.Helper()
		spans := ddBaselineFixture()
		for i := range spans {
			spans[i].Meta["http.url"] = "http://127.0.0.1:1000/__rules_stests/outbound?url=" + url.QueryEscape(target) + "&safe=visible"
		}
		data, err := ddNormalizedResponse(ddCase{Kind: "outbound"}, spans)
		if err != nil {
			t.Fatal(err)
		}
		return data
	}
	first := response("http://127.0.0.1:1001/__rules_stests/echo?echo=visible")
	if !bytes.Equal(first, response("http://127.0.0.1:2002/__rules_stests/echo?echo=visible")) {
		t.Fatal("fresh nested listener port changed behavioral response")
	}
	for _, target := range []string{
		"http://localhost:1001/__rules_stests/echo?echo=visible",
		"http://127.0.0.1:1001/different?echo=visible",
		"http://127.0.0.1:1001/__rules_stests/echo?echo=changed",
		"https://127.0.0.1:1001/__rules_stests/echo?echo=visible",
	} {
		if bytes.Equal(first, response(target)) {
			t.Fatal("outbound behavioral change disappeared during response normalization")
		}
	}
}

func TestSharedExpectedDefectRejectsUnexpectedPassAndDifferentFailure(t *testing.T) {
	c := ddCase{Name: "manual-drop-rule", Kind: "drop"}
	defect := ddFixtureDefects("gin")[c.Name]
	if _, err := ddCaseOutcome(defect, c, nil, nil, nil); err == nil {
		t.Fatal("unexpected SDK defect fix did not require review")
	}
	if _, err := ddCaseOutcome(defect, c, nil, nil, fmt.Errorf("unrelated defect")); err == nil {
		t.Fatal("different failure was accepted as a known SDK defect")
	}
	if status, err := ddCaseOutcome(ddExpectedDefect{}, c, nil, nil, nil); err != nil || status != "passed" {
		t.Fatal("Go defect affected another adapter")
	}
}
