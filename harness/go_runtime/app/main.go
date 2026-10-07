// A tracer-free application: instrumentation is supplied by the build or launcher.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io/ioutil"
	"net"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"sync"
)

type pair struct {
	Integer int
	Float   float64
}

//go:noinline
func mixedArguments(a, b, c, d, e, f, g, h, i, j int, scalar float64, flag bool, message string, values []int, p pair) (int, float64, string) {
	result := a + b + c + d + e + f + g + h + i + j + len(message) + p.Integer
	for _, value := range values {
		result += value
	}
	if !flag {
		result = -result
	}
	return result, scalar + p.Float, message
}

//go:noinline
func touch(buffer *[1024]byte) int { return int(buffer[0]) + int(buffer[1023]) }

//go:noinline
func growStack(depth int) int {
	var buffer [1024]byte
	buffer[0] = byte(depth)
	if depth == 0 {
		return touch(&buffer)
	}
	result := growStack(depth - 1)
	return result + touch(&buffer)
}

func writeJSON(w http.ResponseWriter, value interface{}) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		panic(err)
	}
}

func main() {
	ready := flag.String("ready-file", "", "atomic readiness JSON path")
	flag.Parse()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic(err)
	}
	address := "http://" + listener.Addr().String()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]interface{}{"runtime": runtime.Version(), "architecture": runtime.GOARCH, "pid": os.Getpid(), "cgo": true})
	})
	mux.HandleFunc("/target", func(w http.ResponseWriter, r *http.Request) {
		status, _ := strconv.Atoi(r.URL.Query().Get("status"))
		if status == 0 {
			status = 200
		}
		w.WriteHeader(status)
		writeJSON(w, map[string]interface{}{"traceparent": r.Header.Get("traceparent"), "datadogTraceID": r.Header.Get("x-datadog-trace-id"), "datadogParentID": r.Header.Get("x-datadog-parent-id")})
	})
	mux.HandleFunc("/capability", func(w http.ResponseWriter, r *http.Request) {
		number, floating, text := mixedArguments(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1.25, true, "abi", []int{1, 2}, pair{7, 2.5})
		results := make(chan int, 8)
		var workers sync.WaitGroup
		for i := 0; i < 8; i++ {
			workers.Add(1)
			go func() { defer workers.Done(); results <- growStack(32) }()
		}
		workers.Wait()
		close(results)
		total := 0
		for result := range results {
			total += result
		}
		callback := cgoRoundTrip(7, 2.5)
		runtime.GC()
		request, err := requestForWork(r, address+"/target?status="+r.URL.Query().Get("status"))
		if err != nil {
			panic(err)
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			panic(err)
		}
		defer response.Body.Close()
		body, err := ioutil.ReadAll(response.Body)
		if err != nil {
			panic(err)
		}
		var target map[string]interface{}
		if err := json.Unmarshal(body, &target); err != nil {
			panic(err)
		}
		writeJSON(w, map[string]interface{}{"integer": number, "float": floating, "text": text,
			"stackTotal": total, "callback": callback, "targetStatus": response.StatusCode, "target": target})
	})
	if *ready != "" {
		data, _ := json.Marshal(map[string]interface{}{"url": address, "pid": os.Getpid()})
		if err := ioutil.WriteFile(*ready+".tmp", data, 0600); err != nil {
			panic(err)
		}
		if err := os.Rename(*ready+".tmp", *ready); err != nil {
			panic(err)
		}
	}
	fmt.Println("Go runtime capability application", runtime.Version(), address)
	if err := http.Serve(listener, mux); err != nil {
		panic(err)
	}
}
