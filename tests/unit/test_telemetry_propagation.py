"""Unit tests for OTEL trace context propagation helpers.

Verifies that ``inject_trace_context()`` and ``extract_trace_context()``
correctly serialize and deserialize span context for cross-process
propagation into Huey worker tasks.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: T25.2 — OTEL Trace Context Propagation into Huey Workers
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _force_reset_tracer_provider(provider: TracerProvider) -> None:
    """Force-reset the global TracerProvider, bypassing the once-only guard.

    OpenTelemetry's ``set_tracer_provider()`` uses an internal ``Once`` guard
    that prevents it from being called more than once per process.  In a test
    suite the same process runs many tests sequentially, so the guard blocks
    subsequent fixture setups.  This helper directly resets the internal state
    so each test starts with a clean, isolated TracerProvider.

    Must only be called from test fixtures.

    Args:
        provider: The new TracerProvider to install as the global.
    """
    import opentelemetry.trace as _trace_module

    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    _trace_module.set_tracer_provider(provider)


@pytest.fixture(autouse=True)
def reset_tracer_provider() -> Generator[None]:
    """Reset the global TracerProvider before and after each test.

    OpenTelemetry keeps a global TracerProvider singleton protected by a
    once-only guard. This fixture force-resets the guard so each test
    starts with a clean, isolated provider, and restores a clean no-op
    provider after the test to prevent state leakage into subsequent tests.

    Yields:
        None — pure setup/teardown fixture.
    """
    _force_reset_tracer_provider(TracerProvider())
    yield
    _force_reset_tracer_provider(TracerProvider())


@pytest.fixture
def in_memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Provide a TracerProvider backed by an InMemorySpanExporter.

    Returns:
        A tuple of (TracerProvider, InMemorySpanExporter) configured for testing.
        The provider is force-set as the global provider for the duration of the test.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _force_reset_tracer_provider(provider)
    return provider, exporter


class TestInjectTraceContext:
    """Tests for inject_trace_context()."""

    def test_inject_returns_dict_with_traceparent_when_active_span(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """inject_trace_context() returns a dict with 'traceparent' key when in an active span.

        AC1: TraceContextTextMapPropagator.inject() must be called and serialise the
        current span context into a carrier dict containing the W3C 'traceparent' header.
        """
        from synth_engine.shared.telemetry import inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test-parent"):
            carrier = inject_trace_context()

        assert isinstance(carrier, dict)
        assert "traceparent" in carrier

    def test_inject_returns_empty_dict_when_no_active_span(self) -> None:
        """inject_trace_context() returns an empty dict when there is no active span.

        AC1: When no span is active, the carrier dict must be empty (no traceparent
        header to propagate).
        """
        from synth_engine.shared.telemetry import inject_trace_context

        # No span is active — reset_tracer_provider fixture installed a plain provider
        carrier = inject_trace_context()

        assert isinstance(carrier, dict)
        assert "traceparent" not in carrier

    def test_inject_traceparent_contains_valid_trace_id(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """inject_trace_context() serialises the active span's trace ID into the carrier.

        The traceparent header must encode the current span's trace_id in the
        W3C format: 00-<trace_id_hex>-<span_id_hex>-<flags>.
        """
        from synth_engine.shared.telemetry import inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test-span") as span:
            carrier = inject_trace_context()
            expected_trace_id = format(span.get_span_context().trace_id, "032x")

        traceparent = carrier["traceparent"]
        # W3C traceparent format: 00-<trace_id>-<span_id>-<flags>
        parts = traceparent.split("-")
        assert len(parts) == 4
        assert parts[1] == expected_trace_id

    def test_inject_is_o1_constant_time(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """inject_trace_context() completes within 1ms (AC5: O(1) performance).

        The carrier serialisation must add negligible overhead per task dispatch.
        """
        import time

        from synth_engine.shared.telemetry import inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("perf-span"):
            start = time.perf_counter()
            inject_trace_context()
            elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 1.0, (
            f"inject_trace_context() took {elapsed_ms:.3f}ms — exceeded 1ms budget (AC5)"
        )


class TestExtractTraceContext:
    """Tests for extract_trace_context()."""

    def test_extract_returns_context_with_valid_carrier(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """extract_trace_context() returns a non-empty Context from a valid carrier.

        AC3: extract_trace_context() with a valid W3C traceparent carrier must
        return a Context object that re-attaches the remote span context.
        """
        from opentelemetry.context import Context

        from synth_engine.shared.telemetry import extract_trace_context, inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("parent"):
            carrier = inject_trace_context()

        ctx = extract_trace_context(carrier)
        assert isinstance(ctx, Context)
        # The context must contain a span (not an empty context)
        span_from_ctx = trace.get_current_span(ctx)
        assert span_from_ctx.get_span_context().is_valid

    def test_extract_returns_default_context_with_none_carrier(self) -> None:
        """extract_trace_context() returns a default Context when carrier is None.

        AC4: A None carrier must not raise — it must return a default (empty)
        Context so the worker can proceed without trace context.
        """
        from opentelemetry.context import Context

        from synth_engine.shared.telemetry import extract_trace_context

        ctx = extract_trace_context(None)
        assert isinstance(ctx, Context)

    def test_extract_returns_default_context_with_empty_carrier(self) -> None:
        """extract_trace_context() returns a default Context when carrier is an empty dict.

        AC4: An empty carrier dict must not raise — it must return a default Context.
        """
        from opentelemetry.context import Context

        from synth_engine.shared.telemetry import extract_trace_context

        ctx = extract_trace_context({})
        assert isinstance(ctx, Context)
        # No valid span in a default context
        span_from_ctx = trace.get_current_span(ctx)
        assert not span_from_ctx.get_span_context().is_valid

    def test_extract_default_context_is_not_valid_span(self) -> None:
        """extract_trace_context() with None returns a context without a valid span.

        A None carrier has nothing to propagate, so the resulting context must
        not carry a valid span context (no trace ID to attach to).
        """
        from synth_engine.shared.telemetry import extract_trace_context

        ctx = extract_trace_context(None)
        span = trace.get_current_span(ctx)
        assert not span.get_span_context().is_valid


class TestRoundTripPropagation:
    """Tests for round-trip inject → extract trace ID continuity."""

    def test_roundtrip_preserves_trace_id(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """inject then extract preserves the original trace ID.

        AC5 (round-trip): After injecting a trace context and extracting it
        back, the resulting span context must have the same trace_id as
        the original span.  This verifies W3C traceparent serialization is
        lossless.
        """
        from synth_engine.shared.telemetry import extract_trace_context, inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)

        original_trace_id: int = 0
        with tracer.start_as_current_span("source-span") as span:
            original_trace_id = span.get_span_context().trace_id
            carrier = inject_trace_context()

        ctx = extract_trace_context(carrier)
        remote_span = trace.get_current_span(ctx)
        extracted_trace_id = remote_span.get_span_context().trace_id

        assert extracted_trace_id == original_trace_id, (
            f"Trace ID mismatch: original={original_trace_id:#034x}, "
            f"extracted={extracted_trace_id:#034x}"
        )

    def test_roundtrip_extracted_span_is_remote(
        self,
        in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
    ) -> None:
        """The extracted span context is marked as remote (is_remote=True).

        After propagation across a process boundary (simulated by
        inject → extract), the re-attached span context must be flagged as
        remote so the worker's child span is correctly identified as a
        continuation from a different process.
        """
        from synth_engine.shared.telemetry import extract_trace_context, inject_trace_context

        provider, _ = in_memory_provider
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("source-span"):
            carrier = inject_trace_context()

        ctx = extract_trace_context(carrier)
        remote_span = trace.get_current_span(ctx)
        assert remote_span.get_span_context().is_remote
