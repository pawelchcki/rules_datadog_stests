"""Exercise providers and exporters configured by the real Datadog SDK."""
import argparse
import json
import time
from pathlib import Path

from ddtrace import tracer, __version__ as DDTRACE_VERSION
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, get_logger, SeverityNumber
from opentelemetry.version import __version__ as API_VERSION
from opentelemetry.exporter.otlp.proto.http.version import __version__ as EXPORTER_VERSION


def main(identity_file, pause):
    meter_provider = metrics.get_meter_provider()
    logger_provider = get_logger_provider()
    meter = metrics.get_meter("datadog-otlp-lab", "1.0.0", attributes={"scope.kind": "lab"})
    counter = meter.create_counter("lab.counter", unit="requests", description="controlled counter")
    updown = meter.create_up_down_counter("lab.inflight", unit="requests", description="controlled updown")
    histogram = meter.create_histogram("lab.latency", unit="ms", description="controlled histogram")
    gauge = meter.create_gauge("lab.gauge", unit="items", description="controlled gauge")
    meter.create_observable_counter("lab.observable_counter", callbacks=[lambda options: [metrics.Observation(11, {"kind": "observable"})]])
    meter.create_observable_gauge("lab.observable_gauge", callbacks=[lambda options: [metrics.Observation(13, {"kind": "observable"})]])
    meter.create_observable_up_down_counter("lab.observable_updown", callbacks=[lambda options: [metrics.Observation(7, {"kind": "observable"})]])
    logger = get_logger("datadog-otlp-lab", "1.0.0", attributes={"scope.kind": "lab"})
    identities = []
    log_emission_started = None
    with trace.get_tracer(__name__).start_as_current_span("otlp.control") as span:
        context = span.get_span_context()
        identities.append({"trace_id": context.trace_id, "span_id": context.span_id})
        attributes = {"kind": "sync", "boolean": True, "integer": 3}
        counter.add(2, attributes)
        counter.add(3, attributes)
        updown.add(8, attributes)
        updown.add(-3, attributes)
        histogram.record(2, attributes)
        histogram.record(8, attributes)
        gauge.set(17, attributes)
        log_emission_started = time.time_ns()
        for index in range(5):
            logger.emit(body="lab.log." + str(index), severity_number=SeverityNumber.INFO,
                severity_text="INFO", attributes={"index": index, "boolean": True, "array": ["a", "b"]})
    tracer.flush()
    time.sleep(pause)
    flush_started = time.time_ns()
    metric_flush = meter_provider.force_flush() if hasattr(meter_provider, "force_flush") else None
    log_flush = logger_provider.force_flush() if hasattr(logger_provider, "force_flush") else None
    reader_config = []
    readers = getattr(meter_provider, "_metric_readers", [])
    for reader in readers:
        reader_config.append({"class": type(reader).__name__,
            "export_timeout_millis": getattr(reader, "_export_timeout_millis", None),
            "export_interval_millis": getattr(reader, "_export_interval_millis", None)})
    processors = getattr(getattr(logger_provider, "_multi_log_record_processor", None), "_log_record_processors", [])
    processor_config = []
    for processor in processors:
        batch = getattr(processor, "_batch_processor", processor)
        processor_config.append({key: getattr(batch, "_" + key, None)
            for key in ("max_queue_size", "max_export_batch_size", "schedule_delay_millis", "export_timeout_millis")})
    result = {"ddtraceVersion": DDTRACE_VERSION, "apiVersion": API_VERSION, "exporterVersion": EXPORTER_VERSION,
        "identities": identities, "meterProvider": type(meter_provider).__name__, "loggerProvider": type(logger_provider).__name__,
        "metricFlush": metric_flush, "logFlush": log_flush, "readerConfiguration": reader_config,
        "processorConfiguration": processor_config}
    result.update(logEmissionStartedUnixNano=log_emission_started, flushStartedUnixNano=flush_started)
    Path(identity_file).write_text(json.dumps(result, indent=2) + "\n")
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()
    if hasattr(logger_provider, "shutdown"):
        logger_provider.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity-file", required=True)
    parser.add_argument("--pause", type=float, default=0)
    args = parser.parse_args()
    main(args.identity_file, args.pause)
