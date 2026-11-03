"""In-process OTLP/gRPC trace receiver: the test collector and its sink.

The observability standard (.scaratec/handbuch/observability.md §6)
witnesses telemetry through an independent channel: the server exports
OTLP like it would to a production collector, this receiver decodes
every span into a plain record, and steps read only those records.
Nothing here imports from ../server/.

Each record also goes to a JSONL file so a failing scenario can be
inspected after the fact.
"""

from __future__ import annotations

import json
import threading
from concurrent import futures
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import grpc
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.proto.common.v1 import common_pb2

# opentelemetry.proto.trace.v1.Status.StatusCode
_STATUS_NAMES = {0: "UNSET", 1: "OK", 2: "ERROR"}


@dataclass(frozen=True)
class SinkSpan:
    """One span as the collector received it."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def _any_value(value: common_pb2.AnyValue) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in value.kvlist_value.values}
    if kind == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, kind)


def _attributes(pairs: Any) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in pairs}


class _TraceService(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, sink: TraceSink) -> None:
        self._sink = sink

    def Export(  # noqa: N802 - gRPC method name
        self, request: trace_service_pb2.ExportTraceServiceRequest, context: Any
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        records: list[SinkSpan] = []
        for resource_spans in request.resource_spans:
            resource = _attributes(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    records.append(
                        SinkSpan(
                            name=span.name,
                            trace_id=span.trace_id.hex(),
                            span_id=span.span_id.hex(),
                            parent_span_id=span.parent_span_id.hex(),
                            start_time_unix_nano=span.start_time_unix_nano,
                            end_time_unix_nano=span.end_time_unix_nano,
                            status=_STATUS_NAMES.get(span.status.code, str(span.status.code)),
                            attributes=_attributes(span.attributes),
                            resource_attributes=dict(resource),
                            events=[
                                {"name": e.name, "attributes": _attributes(e.attributes)}
                                for e in span.events
                            ],
                        )
                    )
        self._sink.add(records)
        return trace_service_pb2.ExportTraceServiceResponse()


class TraceSink:
    """Thread-safe store of received spans, mirrored to a JSONL file."""

    def __init__(self, jsonl_path: Path) -> None:
        self._lock = threading.Lock()
        self._spans: list[SinkSpan] = []
        self.jsonl_path = jsonl_path

    def add(self, records: list[SinkSpan]) -> None:
        with self._lock:
            self._spans.extend(records)
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def spans(self) -> list[SinkSpan]:
        with self._lock:
            return list(self._spans)


class TraceCollector:
    """OTLP/gRPC TraceService on a free local port."""

    def __init__(self, jsonl_path: Path) -> None:
        self.sink = TraceSink(jsonl_path)
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            _TraceService(self.sink), self._server
        )
        self.port = self._server.add_insecure_port("127.0.0.1:0")

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop(grace=None)
