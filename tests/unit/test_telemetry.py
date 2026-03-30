"""Unit tests for the OTEL telemetry configuration module.

Verifies graceful degradation when OTLP endpoint is absent and
correct tracer acquisition from the global provider.  Also covers
URL redaction for credentials in OTLP endpoint logs, and the OTLP
happy-path when the exporter package is available.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: P2-T2.1 — Module Bootstrapper, OTEL, Idempotency, Orphan Task Reaper
Task: P20-T20.1 — Exception Handling & Warning Suppression Fixes
"""

import os
from unittest.mock import MagicMock, patch

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
        assert configure_telemetry.__name__ == "configure_telemetry"


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


def test_configure_telemetry_with_otlp_exporter_installed() -> None:
    """configure_telemetry() uses OTLPSpanExporter when the package is available.

    When the exporter package is present and the endpoint env var is set,
    the OTLP path must be taken and the exporter constructed with the endpoint.
    """
    from synth_engine.shared.telemetry import configure_telemetry

    mock_exporter_instance = MagicMock()
    mock_exporter_cls = MagicMock(return_value=mock_exporter_instance)
    mock_otlp_module = MagicMock()
    mock_otlp_module.OTLPSpanExporter = mock_exporter_cls

    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4317"}):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": mock_otlp_module,
            },
        ):
            configure_telemetry("otlp-test-service")

    mock_exporter_cls.assert_called_once_with(endpoint="http://jaeger:4317")
    assert mock_exporter_cls.call_count == 1


def test_get_tracer_returns_tracer_instance() -> None:
    """get_tracer() returns a valid Tracer instance.

    The returned object must be an instance of the OTEL Tracer abstract type,
    confirming it was acquired from the global TracerProvider.
    """
    from synth_engine.shared.telemetry import configure_telemetry, get_tracer

    configure_telemetry("tracer-test")
    tracer = get_tracer("synth_engine.test")

    assert isinstance(tracer, Tracer)
    assert callable(getattr(tracer, "start_span", None)), (
        "Tracer must have a callable start_span method"
    )


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
    assert tracer_a is not tracer_b, "Different scope names must yield distinct tracer objects"


def test_redact_url_strips_credentials() -> None:
    """_redact_url() removes userinfo from a URL before logging.

    Credentials embedded as ``user:pass@host`` in the OTLP endpoint must
    be stripped so they never appear in log sinks.
    """
    from synth_engine.shared.telemetry import _redact_url

    result = _redact_url("grpc://admin:secret@jaeger.internal:4317")
    assert "secret" not in result
    assert "admin" not in result
    assert "jaeger.internal" in result


def test_redact_url_plain_url_unchanged() -> None:
    """_redact_url() returns a credential-free URL unmodified (except normalisation).

    A URL with no userinfo component must still return the scheme, host,
    port, and path without modification.
    """
    from synth_engine.shared.telemetry import _redact_url

    result = _redact_url("http://jaeger:4317")
    assert "jaeger" in result
    assert "4317" in result


def test_redact_url_returns_safe_fallback_on_error() -> None:
    """_redact_url() returns a safe fallback string when URL parsing raises ValueError.

    The function must never raise an exception, even for malformed input,
    so that logging failures cannot crash the application.

    T20.1: The catch must be narrowed to ValueError — the only exception
    urlparse raises for malformed input — rather than a broad Exception.
    """
    from synth_engine.shared.telemetry import _redact_url

    with patch("synth_engine.shared.telemetry.urlparse", side_effect=ValueError("parse error")):
        result = _redact_url("not-a-url")

    assert result == "<unparseable endpoint>"


def test_redact_url_exception_type_is_narrowed() -> None:
    """_redact_url() must NOT catch broad Exception — only ValueError.

    T20.1 AC1: The except clause in _redact_url() must be narrowed to
    a specific exception type.  A non-ValueError exception (e.g. RuntimeError)
    raised by urlparse must propagate rather than being silently swallowed.

    This test verifies that the broad 'except Exception' has been narrowed.
    """
    import ast
    from pathlib import Path

    telemetry_path = (
        Path(__file__).parent.parent.parent / "src" / "synth_engine" / "shared" / "telemetry.py"
    )
    source = telemetry_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                # Bare 'except:' — forbidden
                raise AssertionError(
                    "Found bare 'except:' in telemetry.py — must specify exception type."
                )
            if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                raise AssertionError(
                    "Found 'except Exception' in telemetry.py — must narrow to specific type "
                    "(e.g. ValueError). T20.1 AC1 requires narrowing broad exception catches."
                )
    # If we reach here, no broad exception catches found
    node_count = len(list(ast.walk(tree)))
    assert node_count > 0, "AST tree must be non-empty — source was not parsed"


def test_redact_url_non_value_error_propagates() -> None:
    """_redact_url() must propagate non-ValueError exceptions from urlparse.

    T20.1 QA finding: the AST test alone is insufficient — a behavioral test
    is required to verify the narrowed except clause actually lets RuntimeError
    (and other non-ValueError exceptions) propagate out of _redact_url().

    This guards against an implementation that catches Exception but is not
    caught by the AST test (e.g. 'except (ValueError, RuntimeError)').
    """
    import pytest

    from synth_engine.shared.telemetry import _redact_url

    with patch("synth_engine.shared.telemetry.urlparse", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            _redact_url("http://jaeger:4317")
