"""OpenTelemetry setup for the Conclave Engine.

Provides a thin, air-gap-safe wrapper around OTEL tracing. When the
OTLP endpoint environment variable is absent, an InMemorySpanExporter is
used so the application starts cleanly in fully offline environments.
The InMemorySpanExporter accumulates spans in memory and is intended for
development and testing only — it does not export spans to any backend.

T20.1 AC1: All exception catches in this telemetry module are narrowed to
specific types.  The ``_redact_url`` helper uses ``ValueError`` — the only
exception ``urlparse`` raises for malformed input — rather than a broad
``Exception`` catch that could silently swallow unrelated errors.
"""

import logging
import os
from typing import cast
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

logger = logging.getLogger(__name__)


def _redact_url(endpoint: str) -> str:
    """Return the endpoint URL with any userinfo (credentials) stripped.

    Parses the URL and reconstructs it using only scheme, host, port, and
    path.  Any username or password embedded in the URL is discarded before
    the value reaches a log sink.  For example, a URL of the form
    ``grpc://<user>:<token>@jaeger.internal:4317`` would be returned as
    ``grpc://jaeger.internal:4317``.

    T20.1 AC1: catches only ``ValueError`` — the specific exception raised
    by ``urlparse`` on malformed input — rather than a broad ``Exception``.
    This ensures unrelated errors (e.g., ``AttributeError`` from a
    fundamentally broken URL object) propagate rather than being silently
    swallowed.

    Args:
        endpoint: Raw endpoint URL that may contain credentials in the
            userinfo component.

    Returns:
        The URL with scheme, host, optional port, and path only.
        Returns ``"<unparseable endpoint>"`` if ``urlparse`` raises
        ``ValueError`` for a malformed URL.
    """
    try:
        parsed = urlparse(endpoint)
        # Reconstruct without userinfo: netloc without credentials
        host_part = parsed.hostname or ""
        if parsed.port:
            host_part = f"{host_part}:{parsed.port}"
        redacted = parsed._replace(netloc=host_part)
        return redacted.geturl()
    except ValueError:  # best-effort: urlparse raises ValueError for malformed URLs
        return "<unparseable endpoint>"


def _build_exporter() -> SpanExporter:
    """Build a span exporter based on the runtime environment.

    Returns an OTLP gRPC exporter when OTEL_EXPORTER_OTLP_ENDPOINT is set,
    otherwise falls back to an InMemorySpanExporter for air-gapped and
    local environments.  The InMemorySpanExporter accumulates spans in
    memory; it is suitable for development and testing but does not forward
    spans to any external backend.

    Returns:
        A configured SpanExporter instance.
    """
    endpoint = os.environ.get(_OTLP_ENDPOINT_ENV)
    if endpoint:
        # Import lazily so that opentelemetry-exporter-otlp is optional;
        # if it's absent the fallback path is taken automatically.
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            logger.info("OTEL: using OTLP exporter at %s", _redact_url(endpoint))
            # cast: OTLPSpanExporter implements SpanExporter but mypy cannot
            # resolve the type from the lazy import without the optional package
            # installed in the type-checking environment.
            return cast(SpanExporter, OTLPSpanExporter(endpoint=endpoint))
        except ImportError:
            logger.warning(
                "OTEL: opentelemetry-exporter-otlp not installed; "
                "falling back to InMemorySpanExporter"
            )

    logger.info("OTEL: %s not set — using InMemorySpanExporter (dev/test only)", _OTLP_ENDPOINT_ENV)
    return InMemorySpanExporter()


def configure_telemetry(service_name: str) -> None:
    """Configure the global OpenTelemetry TracerProvider.

    Sets up a BatchSpanProcessor wired to an OTLP exporter if
    OTEL_EXPORTER_OTLP_ENDPOINT is present, otherwise an
    InMemorySpanExporter is used so the application starts cleanly in
    air-gapped deployments.  The InMemorySpanExporter accumulates spans in
    memory; it is intended for development and testing only.

    Args:
        service_name: Logical name of this service, embedded in every span.
    """
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_build_exporter()))
    trace.set_tracer_provider(provider)
    logger.info("OTEL: TracerProvider configured for service '%s'", service_name)


def get_tracer(name: str) -> Tracer:
    """Return a named tracer from the globally configured TracerProvider.

    Args:
        name: Instrumentation scope name, typically the calling module's
            ``__name__``.

    Returns:
        A Tracer bound to the global TracerProvider.
    """
    return trace.get_tracer(name)
