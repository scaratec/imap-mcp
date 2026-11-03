"""Optional OpenTelemetry tracing integration.

Provides a no-op fallback when the ``tracing`` extra is not installed
or no OTLP endpoint is configured. The server runs identically in
both cases — tracing is purely additive.

Usage::

    from .tracing import tracer

    with tracer.start_as_current_span("my.operation") as span:
        span.set_attribute("key", "value")
        ...

ADR 0038: the resource carries `OTEL_RESOURCE_ATTRIBUTES` plus
`service.name`, `service.version` and `service.instance.id`, and the
provider is shut down (flushing the batch) before the process exits.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Self

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False


class _NoOpSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, exception: BaseException, **kwargs: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(
        self, name: str, *, attributes: dict[str, Any] | None = None, **kwargs: Any
    ) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()


_tracer: _NoOpTracer | Any = _NoOpTracer()
_provider: Any = None
_initialized = False


def _get_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("sc-imap-mcp")
    except PackageNotFoundError:
        return "dev"


def init_tracer(service_name: str = "imap-mcp") -> None:
    global _tracer, _provider, _initialized
    if _initialized:
        return
    _initialized = True

    if not _HAS_OTEL:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    # Resource.create merges OTEL_RESOURCE_ATTRIBUTES; the attributes
    # given here take precedence over it.
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
            "service.version": _get_version(),
        }
    )
    if not resource.attributes.get("service.instance.id"):
        resource = resource.merge(Resource({"service.instance.id": str(uuid.uuid4())}))
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = trace.get_tracer(service_name)


def shutdown_tracer() -> None:
    """Shut the provider down, exporting every span still batched.

    Idempotent; a no-op when tracing is not active.
    """
    global _provider
    provider, _provider = _provider, None
    if provider is not None:
        provider.shutdown()


def install_sigterm_flush() -> None:
    """On SIGTERM, flush the spans first, then terminate as SIGTERM would.

    Installed from inside the running event loop, so the handler runs
    between callbacks, never in the middle of span bookkeeping. Without
    active tracing nothing is installed and SIGTERM keeps its default
    action.
    """
    if _provider is None:
        return

    def _on_sigterm() -> None:
        shutdown_tracer()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _on_sigterm)
    except (NotImplementedError, RuntimeError):
        # No signal support in this loop (e.g. Windows): the atexit
        # shutdown of the SDK still covers a normal exit.
        pass


def mark_span_error(span: Any, description: str) -> None:
    """Set span status ERROR (observability standard §2: technical failure)."""
    if _HAS_OTEL:
        span.set_status(Status(StatusCode.ERROR, description))


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
