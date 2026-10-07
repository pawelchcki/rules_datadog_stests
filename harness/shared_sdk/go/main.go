// The HTTP API transports SDK calls for the unchanged system-tests assertions.
// SDK objects survive between calls; no trace payloads are manufactured here.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	httptrace "github.com/DataDog/dd-trace-go/contrib/net/http/v2"
	ddotel "github.com/DataDog/dd-trace-go/v2/ddtrace/opentelemetry"
	"github.com/DataDog/dd-trace-go/v2/ddtrace/tracer"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	oteltrace "go.opentelemetry.io/otel/trace"
)

type arguments struct {
	Operation   string            `json:"operation"`
	SpanID      uint64            `json:"span_id"`
	ParentID    *uint64           `json:"parent_id"`
	Name        string            `json:"name"`
	Service     *string           `json:"service"`
	Resource    *string           `json:"resource"`
	SpanType    *string           `json:"span_type"`
	Headers     json.RawMessage   `json:"headers"`
	Query       map[string]string `json:"query"`
	Tags        [][2]any          `json:"tags"`
	Key         string            `json:"key"`
	Value       any               `json:"value"`
	ErrorType   string            `json:"error_type"`
	Message     string            `json:"message"`
	Stack       string            `json:"stack"`
	Attributes  map[string]any    `json:"attributes"`
	SpanKind    int               `json:"span_kind"`
	Timestamp   *float64          `json:"timestamp"`
	Status      int               `json:"status"`
	Description string            `json:"description"`
	Links       []struct {
		ParentID   uint64         `json:"parent_id"`
		Attributes map[string]any `json:"attributes"`
	} `json:"links"`
}

type sdk struct {
	mu           sync.Mutex
	spans        map[uint64]*tracer.Span
	contexts     map[uint64]*tracer.SpanContext
	otelSpans    map[uint64]oteltrace.Span
	otelContexts map[uint64]context.Context
	parents      map[uint64]uint64
	active       uint64
	otelTracer   oteltrace.Tracer
	logger       *startupLogger
	baseURL      string
}

// Configuration assertions read the SDK's serialized effective startup state.
// Reading requested environment variables would not prove SDK translation.
type startupLogger struct {
	mu            sync.Mutex
	configuration map[string]any
}

func (l *startupLogger) Log(message string) {
	fmt.Fprintln(os.Stderr, message)
	const marker = "DATADOG TRACER CONFIGURATION "
	if _, body, ok := strings.Cut(message, marker); ok {
		var configuration map[string]any
		if json.Unmarshal([]byte(strings.TrimSpace(body)), &configuration) == nil {
			l.mu.Lock()
			l.configuration = configuration
			l.mu.Unlock()
		}
	}
}

func identity(c *tracer.SpanContext) map[string]any {
	id, ok := new(big.Int).SetString(c.TraceID(), 16)
	if !ok {
		panic("SDK returned an invalid trace ID")
	}
	return map[string]any{"span_id": c.SpanID(), "trace_id": json.Number(id.String())}
}

func otelIdentity(c oteltrace.SpanContext) map[string]any {
	id := c.TraceID()
	spanID := c.SpanID()
	return map[string]any{"span_id": new(big.Int).SetBytes(spanID[:]).Uint64(), "trace_id": json.Number(new(big.Int).SetBytes(id[:]).String())}
}

func attributes(values map[string]any) ([]attribute.KeyValue, error) {
	result := []attribute.KeyValue{}
	for key, value := range values {
		k := attribute.Key(key)
		switch v := value.(type) {
		case string:
			result = append(result, k.String(v))
		case bool:
			result = append(result, k.Bool(v))
		case json.Number:
			if integer, err := v.Int64(); err == nil {
				result = append(result, k.Int64(integer))
			} else if strings.ContainsAny(v.String(), ".eE") {
				number, err := v.Float64()
				if err != nil {
					return nil, err
				}
				result = append(result, k.Float64(number))
			} else {
				result = append(result, attribute.KeyValue{Key: k})
			}
		case []any:
			if len(v) == 0 {
				result = append(result, k.StringSlice(nil))
				continue
			}
			switch v[0].(type) {
			case string:
				items := []string{}
				for _, item := range v {
					s, ok := item.(string)
					if !ok {
						result = append(result, attribute.KeyValue{Key: k})
						goto nextAttribute
					}
					items = append(items, s)
				}
				result = append(result, k.StringSlice(items))
			case bool:
				items := []bool{}
				for _, item := range v {
					b, ok := item.(bool)
					if !ok {
						result = append(result, attribute.KeyValue{Key: k})
						goto nextAttribute
					}
					items = append(items, b)
				}
				result = append(result, k.BoolSlice(items))
			case json.Number:
				integers, numbers := []int64{}, []float64{}
				allInteger := true
				for _, item := range v {
					n, ok := item.(json.Number)
					if !ok {
						result = append(result, attribute.KeyValue{Key: k})
						goto nextAttribute
					}
					i, e := n.Int64()
					allInteger = allInteger && e == nil
					f, e := n.Float64()
					if e != nil {
						return nil, e
					}
					integers = append(integers, i)
					numbers = append(numbers, f)
				}
				if allInteger {
					result = append(result, k.Int64Slice(integers))
				} else {
					result = append(result, k.Float64Slice(numbers))
				}
			default:
				result = append(result, attribute.KeyValue{Key: k})
			}
		default:
			result = append(result, attribute.KeyValue{Key: k})
		}
	nextAttribute:
	}
	return result, nil
}

func (s *sdk) reset() {
	s.spans = map[uint64]*tracer.Span{}
	s.contexts = map[uint64]*tracer.SpanContext{}
	s.otelSpans = map[uint64]oteltrace.Span{}
	s.otelContexts = map[uint64]context.Context{}
	s.parents = map[uint64]uint64{}
	s.active = 0
}

func (s *sdk) call(a arguments) (any, error) {
	var parentID uint64
	if a.ParentID != nil {
		parentID = *a.ParentID
	}
	switch a.Operation {
	case "reset":
		tracer.Flush()
		s.reset()
		return nil, nil
	case "flush":
		tracer.Flush()
		return true, nil
	case "config":
		s.logger.mu.Lock()
		defer s.logger.mu.Unlock()
		configuration := s.logger.configuration
		if configuration == nil {
			// A disabled Go tracer does not log startup configuration.
			// Observe its no-op context instead of echoing requested env.
			control := tracer.StartSpan("shared.configuration.state")
			defer control.Finish()
			if control.Context().SpanID() == 0 {
				return map[string]any{"dd_trace_enabled": "false"}, nil
			}
			return nil, errors.New("SDK startup configuration was not emitted")
		}
		result := map[string]any{}
		for key, field := range map[string]string{"dd_service": "service", "dd_env": "env", "dd_version": "dd_version", "dd_trace_sample_rate": "sample_rate", "dd_trace_rate_limit": "sample_rate_limit"} {
			result[key] = configuration[field]
		}
		var tags []string
		if values, ok := configuration["tags"].(map[string]any); ok {
			for key, value := range values {
				tags = append(tags, key+":"+fmt.Sprint(value))
			}
		}
		sort.Strings(tags)
		result["dd_tags"] = strings.Join(tags, ",")
		result["dd_trace_enabled"] = "true"
		result["sdk_version"] = strings.TrimPrefix(fmt.Sprint(configuration["version"]), "v")
		return result, nil
	case "extract":
		h := http.Header{}
		var pairs [][2]string
		if err := json.Unmarshal(a.Headers, &pairs); err != nil {
			return nil, err
		}
		for _, pair := range pairs {
			h.Add(pair[0], pair[1])
		}
		c, err := tracer.Extract(tracer.HTTPHeadersCarrier(h))
		if errors.Is(err, tracer.ErrSpanContextNotFound) || errors.Is(err, tracer.ErrSpanContextCorrupted) {
			// Baggage-only contexts also have span ID zero. A failed extract
			// must not leave a prior successful context under that lookup key.
			delete(s.contexts, 0)
			return uint64(0), nil
		}
		if err != nil {
			return nil, err
		}
		s.contexts[c.SpanID()] = c
		return c.SpanID(), nil
	case "http_request":
		control, ctx := tracer.StartSpanFromContext(context.Background(), "http.lab.export-control")
		defer control.Finish()
		address := s.baseURL + "/target/" + strconv.Itoa(a.Status)
		query := url.Values{}
		for key, value := range a.Query {
			query.Set(key, value)
		}
		request, err := http.NewRequestWithContext(ctx, "GET", address+"?"+query.Encode(), nil)
		if err != nil {
			return nil, err
		}
		var headers map[string]string
		if err := json.Unmarshal(a.Headers, &headers); err != nil {
			return nil, err
		}
		for key, value := range headers {
			request.Header.Set(key, value)
		}
		client := httptrace.WrapClient(&http.Client{Timeout: 5 * time.Second})
		response, err := client.Do(request)
		if err != nil {
			return nil, err
		}
		defer response.Body.Close()
		body, err := io.ReadAll(response.Body)
		if err != nil {
			return nil, err
		}
		var target struct {
			Active map[string]any `json:"active"`
		}
		decoder := json.NewDecoder(strings.NewReader(string(body)))
		decoder.UseNumber()
		if err := decoder.Decode(&target); err != nil {
			return nil, err
		}
		return map[string]any{"control": identity(control.Context()), "target": target.Active, "url": address, "status": response.StatusCode}, nil
	case "start":
		opts := []tracer.StartSpanOption{}
		if parent := s.contexts[parentID]; a.ParentID != nil && parent != nil {
			opts = append(opts, tracer.ChildOf(parent), tracer.WithSpanLinks(parent.SpanLinks()))
		}
		if a.Service != nil {
			opts = append(opts, tracer.ServiceName(*a.Service))
		}
		if a.Resource != nil {
			opts = append(opts, tracer.ResourceName(*a.Resource))
		}
		if a.SpanType != nil {
			opts = append(opts, tracer.SpanType(*a.SpanType))
		}
		span := tracer.StartSpan(a.Name, opts...)
		for _, pair := range a.Tags {
			key, ok := pair[0].(string)
			if !ok {
				return nil, errors.New("tag key must be a string")
			}
			span.SetTag(key, pair[1])
		}
		id := span.Context().SpanID()
		s.spans[id] = span
		s.contexts[id] = span.Context()
		s.parents[id] = parentID
		s.active = id
		return identity(span.Context()), nil
	case "otel_start":
		ctx := context.Background()
		if parent, ok := s.otelContexts[parentID]; a.ParentID != nil && ok {
			ctx = parent
		} else if c := s.contexts[parentID]; a.ParentID != nil && c != nil {
			if parent := s.spans[parentID]; parent != nil {
				ctx = tracer.ContextWithSpan(ctx, parent)
			}
		}
		attrs, err := attributes(a.Attributes)
		if err != nil {
			return nil, err
		}
		// Python's parametric SpanKind starts at INTERNAL=0; Go reserves 0
		// for UNSPECIFIED and starts INTERNAL at 1.
		opts := []oteltrace.SpanStartOption{oteltrace.WithSpanKind(oteltrace.SpanKind(a.SpanKind + 1)), oteltrace.WithAttributes(attrs...)}
		if a.Timestamp != nil {
			opts = append(opts, oteltrace.WithTimestamp(time.Unix(0, int64(*a.Timestamp*1000))))
		}
		for _, link := range a.Links {
			parent := s.otelSpans[link.ParentID]
			if parent == nil {
				return nil, errors.New("unknown linked OTel span")
			}
			attrs, err := attributes(link.Attributes)
			if err != nil {
				return nil, err
			}
			opts = append(opts, oteltrace.WithLinks(oteltrace.Link{SpanContext: parent.SpanContext(), Attributes: attrs}))
		}
		ctx, span := s.otelTracer.Start(ctx, a.Name, opts...)
		idBytes := span.SpanContext().SpanID()
		id := new(big.Int).SetBytes(idBytes[:]).Uint64()
		s.otelSpans[id] = span
		s.otelContexts[id] = ctx
		s.parents[id] = parentID
		s.active = id
		if dd, ok := tracer.SpanFromContext(ctx); ok {
			s.spans[id] = dd
			s.contexts[id] = dd.Context()
		}
		return otelIdentity(span.SpanContext()), nil
	case "dd_current":
		if c := s.contexts[s.active]; c != nil {
			return identity(c), nil
		}
		return nil, nil
	case "otel_current":
		if span := s.otelSpans[s.active]; span != nil {
			return otelIdentity(span.SpanContext()), nil
		}
		return nil, nil
	}
	if span := s.otelSpans[a.SpanID]; span != nil {
		switch a.Operation {
		case "otel_end":
			opts := []oteltrace.SpanEndOption{}
			if a.Timestamp != nil {
				opts = append(opts, oteltrace.WithTimestamp(time.Unix(0, int64(*a.Timestamp*1000))))
			}
			span.End(opts...)
			s.active = s.parents[a.SpanID]
			return nil, nil
		case "otel_attributes":
			attrs, err := attributes(a.Attributes)
			if err != nil {
				return nil, err
			}
			span.SetAttributes(attrs...)
			return nil, nil
		case "otel_status":
			status := codes.Unset
			if a.Status == 1 {
				status = codes.Ok
			} else if a.Status == 2 {
				status = codes.Error
			}
			span.SetStatus(status, a.Description)
			return nil, nil
		case "otel_name":
			span.SetName(a.Name)
			return nil, nil
		case "otel_recording":
			return span.IsRecording(), nil
		case "otel_event":
			attrs, err := attributes(a.Attributes)
			if err != nil {
				return nil, err
			}
			opts := []oteltrace.EventOption{oteltrace.WithAttributes(attrs...)}
			if a.Timestamp != nil {
				opts = append(opts, oteltrace.WithTimestamp(time.Unix(0, int64(*a.Timestamp*1000))))
			}
			span.AddEvent(a.Name, opts...)
			return nil, nil
		case "otel_exception":
			attrs, err := attributes(a.Attributes)
			if err != nil {
				return nil, err
			}
			span.RecordError(errors.New(a.Message), oteltrace.WithAttributes(attrs...))
			return nil, nil
		case "otel_context":
			c := span.SpanContext()
			idBytes := c.SpanID()
			return map[string]any{"span_id": new(big.Int).SetBytes(idBytes[:]).Uint64(), "trace_id": c.TraceID().String(), "trace_flags": c.TraceFlags().String(), "trace_state": c.TraceState().String(), "remote": c.IsRemote()}, nil
		}
	}
	span, exists := s.spans[a.SpanID]
	if !exists {
		return nil, fmt.Errorf("unknown SDK span %d", a.SpanID)
	}
	switch a.Operation {
	case "finish":
		span.Finish()
		// Go's periodic writer interval exceeds the pinned lab's polling
		// window. Flush the writer without finishing the open trace; only
		// genuinely eligible partial chunks can be exported by the SDK.
		tracer.Flush()
		s.active = s.parents[a.SpanID]
		return nil, nil
	case "inject":
		h := http.Header{}
		err := tracer.Inject(span.Context(), tracer.HTTPHeadersCarrier(h))
		if err != nil {
			return nil, err
		}
		result := map[string]string{}
		for key, values := range h {
			if len(values) != 1 {
				return nil, errors.New("duplicate SDK carrier")
			}
			result[strings.ToLower(key)] = values[0]
		}
		return result, nil
	case "set_resource":
		if a.Resource == nil {
			return nil, errors.New("missing resource")
		}
		span.SetTag("resource.name", *a.Resource)
		return nil, nil
	case "set_meta":
		value := a.Value
		if a.Key == "manual.keep" || a.Key == "manual.drop" {
			// The common parametric API accepts a presence marker or "1";
			// Go's public API requires the equivalent boolean marker.
			value = value == nil || value == true || value == "1" || value == "true"
		}
		if number, ok := value.(json.Number); ok {
			n, err := number.Float64()
			if err != nil {
				return nil, err
			}
			value = n
		}
		span.SetTag(a.Key, value)
		return nil, nil
	case "set_metric":
		value, ok := a.Value.(json.Number)
		if !ok {
			return nil, errors.New("metric must be numeric")
		}
		number, err := value.Float64()
		if err != nil {
			return nil, err
		}
		span.SetTag(a.Key, number)
		return nil, nil
	case "set_error":
		span.SetTag("error", true)
		span.SetTag("error.type", a.ErrorType)
		span.SetTag("error.message", a.Message)
		span.SetTag("error.stack", a.Stack)
		return nil, nil
	case "set_baggage":
		value, ok := a.Value.(string)
		if !ok {
			return nil, errors.New("baggage must be a string")
		}
		span.SetBaggageItem(a.Key, value)
		return nil, nil
	case "get_baggage":
		value := span.BaggageItem(a.Key)
		if value == "" {
			return nil, nil
		}
		return value, nil
	case "get_all_baggage":
		result := map[string]string{}
		span.Context().ForeachBaggageItem(func(key, value string) bool { result[key] = value; return true })
		return result, nil
	case "add_link":
		c := s.contexts[parentID]
		if c == nil {
			return nil, errors.New("unknown linked span context")
		}
		attrs := map[string]string{}
		for key, value := range a.Attributes {
			switch v := value.(type) {
			case []any:
				for i, item := range v {
					attrs[key+"."+strconv.Itoa(i)] = fmt.Sprint(item)
				}
			default:
				attrs[key] = fmt.Sprint(v)
			}
		}
		// Obtain W3C state from the actual SDK propagator, rather than
		// reconstructing it from the incoming HTTP request.
		carrier := tracer.TextMapCarrier{}
		if err := tracer.Inject(c, carrier); err != nil {
			return nil, err
		}
		link := tracer.SpanLink{TraceID: c.TraceIDLower(), TraceIDHigh: c.TraceIDUpper(), SpanID: c.SpanID(), Attributes: attrs, Tracestate: carrier["tracestate"]}
		if traceparent := strings.Split(carrier["traceparent"], "-"); len(traceparent) == 4 {
			flags, err := strconv.ParseUint(traceparent[3], 16, 8)
			if err != nil {
				return nil, err
			}
			link.Flags = uint32(flags) | (1 << 31)
		}
		span.AddLink(link)
		return nil, nil
	default:
		return nil, fmt.Errorf("unimplemented SDK operation %q", a.Operation)
	}
}

func main() {
	ready := flag.String("ready-file", "", "readiness file")
	flag.Parse()
	if *ready == "" {
		panic("--ready-file is required")
	}
	s := &sdk{}
	s.reset()
	s.logger = &startupLogger{}
	provider := ddotel.NewTracerProvider(tracer.WithLogger(s.logger))
	defer provider.Shutdown()
	s.otelTracer = provider.Tracer("system-tests")
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic(err)
	}
	mux := http.NewServeMux()
	s.baseURL = "http://" + listener.Addr().String()
	mux.Handle("/target/", httptrace.WrapHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		span, ok := tracer.SpanFromContext(r.Context())
		if !ok {
			http.Error(w, "missing native HTTP server span", 500)
			return
		}
		status, err := strconv.Atoi(strings.TrimPrefix(r.URL.Path, "/target/"))
		if err != nil || status < 100 || status > 599 {
			http.Error(w, "invalid HTTP status", 400)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Lab-Response", "response-value")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]any{"active": identity(span.Context())})
	}), "shared-http", "GET /target/{status}"))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"ready":true,"ddtraceVersion":"2.10.1","language":"go"}`)
	})
	mux.HandleFunc("/sdk", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
		decoder.UseNumber()
		var args arguments
		if err := decoder.Decode(&args); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]any{"error": err.Error()})
			return
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		result, err := s.call(args)
		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]any{"error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"result": result})
	})
	if err := os.WriteFile(*ready+".tmp", []byte(strconv.Itoa(listener.Addr().(*net.TCPAddr).Port)), 0600); err != nil {
		panic(err)
	}
	if err := os.Rename(*ready+".tmp", *ready); err != nil {
		panic(err)
	}
	if err := http.Serve(listener, mux); err != nil {
		panic(err)
	}
}
