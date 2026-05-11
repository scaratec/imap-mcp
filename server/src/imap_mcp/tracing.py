"""Optional OpenTelemetry tracing integration.

Provides a no-op fallback when the ``tracing`` extra is not installed
or no OTLP endpoint is configured. The server runs identically in
both cases — tracing is purely additive.

Usage::

    from .tracing import tracer

    with tracer.start_as_current_span("my.operation") as span:
        span.set_attribute("key", "value")
        ...
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False


class _NoOpSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, exception: BaseException, **kwargs: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(
        self, name: str, *, attributes: dict[str, Any] | None = None, **kwargs: Any
    ):
        yield _NoOpSpan()


_tracer: _NoOpTracer | Any = _NoOpTracer()
_initialized = False


def init_tracer(service_name: str = "imap-mcp") -> None:
    global _tracer, _initialized
    if _initialized:
        return
    _initialized = True

    if not _HAS_OTEL:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
            "service.version": "0.5.0",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


def get_tracer() -> Any:
    return _tracer


tracer: Any = type(
    "_TracerProxy",
    (),
    {
        "start_as_current_span": lambda self, *a, **kw: _tracer.start_as_current_span(*a, **kw),
        "__getattr__": lambda self, name: getattr(_tracer, name),
    },
)()
