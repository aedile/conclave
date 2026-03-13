"""Unit tests for the OTEL telemetry configuration module.

Verifies graceful degradation when OTLP endpoint is absent and
correct tracer acquisition from the global provider.

CONSTITUTION Priority 3: TDD RED Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
"""

import os
from unittest.mock import patch

from opentelemetry.trace import Tracer


def test_configure_telemetry_no_endpoint_uses_noop() -> None:
    """configure_telemetry() succeeds when OTEL_EXPORTER_OTLP_ENDPOINT is absent.

    In an air-gapped deployment with no Jaeger instance, the function must
    complete without raising an exception and fall back to the no-op exporter.
    """
    from synth_engine.shared.telemetry import configure_telemetry

    env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
    with patch.dict(os.environ, env, clear=True):
        # Must not raise
        configure_telemetry("test-service")


def test_configure_telemetry_with_endpoint_falls_back_gracefully() -> None:
    """configure_telemetry() falls back to no-op when OTLP package is missing.

    Even with the env var set, if opentelemetry-exporter-otlp is not installed,
    the function must not crash — it logs a warning and continues.
    """
    import builtins

    from synth_engine.shared.telemetry import configure_telemetry

    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if "otlp" in name.lower():
            raise ImportError("Simulated missing OTLP exporter")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
        with patch("builtins.__import__", side_effect=mock_import):
            configure_telemetry("test-service-fallback")


def test_get_tracer_returns_tracer_instance() -> None:
    """get_tracer() returns a valid Tracer instance.

    The returned object must be an instance of the OTEL Tracer abstract type,
    confirming it was acquired from the global TracerProvider.
    """
    from synth_engine.shared.telemetry import configure_telemetry, get_tracer

    configure_telemetry("tracer-test")
    tracer = get_tracer("synth_engine.test")

    assert isinstance(tracer, Tracer)


def test_get_tracer_with_different_names() -> None:
    """get_tracer() returns a tracer for any given instrumentation scope name.

    Different module names produce independent tracer instances, consistent
    with OTEL's per-module instrumentation pattern.
    """
    from synth_engine.shared.telemetry import configure_telemetry, get_tracer

    configure_telemetry("multi-tracer-test")
    tracer_a = get_tracer("module.a")
    tracer_b = get_tracer("module.b")

    assert isinstance(tracer_a, Tracer)
    assert isinstance(tracer_b, Tracer)
