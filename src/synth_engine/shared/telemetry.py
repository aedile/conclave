"""OpenTelemetry setup for the Conclave Engine.

Provides a thin, air-gap-safe wrapper around OTEL tracing. When the
OTLP endpoint environment variable is absent, a NoOpSpanExporter is used
so the application starts cleanly in fully offline environments.
"""

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

logger = logging.getLogger(__name__)


def _build_exporter() -> SpanExporter:
    """Build a span exporter based on the runtime environment.

    Returns an OTLP gRPC exporter when OTEL_EXPORTER_OTLP_ENDPOINT is set,
    otherwise falls back to a no-op in-memory exporter for air-gapped and
    local environments.

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

            logger.info("OTEL: using OTLP exporter at %s", endpoint)
            return OTLPSpanExporter(endpoint=endpoint)
        except ImportError:
            logger.warning(
                "OTEL: opentelemetry-exporter-otlp not installed; falling back to no-op exporter"
            )

    logger.info("OTEL: %s not set — using no-op exporter", _OTLP_ENDPOINT_ENV)
    return InMemorySpanExporter()


def configure_telemetry(service_name: str) -> None:
    """Configure the global OpenTelemetry TracerProvider.

    Sets up a BatchSpanProcessor wired to an OTLP exporter if
    OTEL_EXPORTER_OTLP_ENDPOINT is present, otherwise a no-op exporter is
    used so the application starts cleanly in air-gapped deployments.

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
