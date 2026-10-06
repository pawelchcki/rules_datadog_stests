package main

import (
	"encoding/json"
	"flag"
	"fmt"
)

func main() {
	protocol := flag.String("protocol", "datadog", "telemetry protocol")
	flag.StringVar(&datadogWire, "wire-version", "v0.5", "Datadog intake wire version")
	flag.StringVar(&datadogCaseName, "case", "", "shared contract to run independently (empty runs all for diagnostics)")
	app := flag.String("app", "", "fixture application")
	launcher := flag.String("launcher", "", "app launcher runfile")
	adapter := flag.String("upstream-datadog-adapter", "", "upstream adapter runfile")
	source := flag.String("upstream-datadog-test", "", "pinned upstream test")
	launchJSON := flag.String("launch-args", "[]", "launcher arguments")
	flag.Parse()
	if *protocol != "datadog" || (datadogWire != "v0.4" && datadogWire != "v0.5") {
		fail(fmt.Errorf("unsupported Datadog protocol or wire version"))
	}
	if *app == "" || *launcher == "" {
		fail(fmt.Errorf("app and launcher are required"))
	}
	cases, err := ddSelectCases(datadogCaseName)
	if err != nil {
		fail(err)
	}
	if err := ddValidateUpstreamInputs(cases, *adapter, *source); err != nil {
		fail(err)
	}
	var args []string
	if err := json.Unmarshal([]byte(*launchJSON), &args); err != nil {
		fail(err)
	}
	if len(args) == 0 {
		fail(fmt.Errorf("launch arguments are required"))
	}
	if *adapter != "" {
		datadogUpstreamAdapter = resolve(*adapter)
	}
	if *source != "" {
		datadogUpstreamTest = resolve(*source)
	}
	if err := runDatadog(*app, resolve(*launcher), args); err != nil {
		fail(err)
	}
}
func experimentEnvironment(app, sink string) map[string]string { return datadogEnvironment(sink) }
func workload(base, caseName string, ownership func() error) error {
	return datadogWorkload(base, ownership)
}
