//go:build tools
// +build tools

package tools

import (
	_ "github.com/DataDog/dd-trace-go/contrib/net/http/v2"
	_ "github.com/DataDog/dd-trace-go/v2/ddtrace/tracer"
	_ "github.com/DataDog/orchestrion"
)
