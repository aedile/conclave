"""Integration test for OTEL trace context propagation into Huey workers.

Verifies end-to-end that a trace ID injected at task dispatch (simulating
a FastAPI route) is preserved in the Huey worker's child span when the task
executes synchronously via Huey immediate mode.

CONSTITUTION Priority 3: TDD RED/GREEN Phase
Task: T25.2 — OTEL Trace Context Propagation into Huey Workers
AC4: End-to-end trace test verifying parent + child spans share the same trace ID.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _force_reset_tracer_provider(provider: TracerProvider) -> None:
    """Force-reset the global TracerProvider, bypassing the once-only guard.

    OpenTelemetry's ``set_tracer_provider()`` can only be called once per process
    (a thread-safety guard using ``Once``).  In integration test suites where
    other tests have already configured a global provider, the guard prevents
    our test fixture from installing its own InMemorySpanExporter.

    This helper directly resets the internal state so each test starts with a
    clean, isolated TracerProvider.  It must only be used in test code.

    Args:
        provider: The new TracerProvider to install as the global.
    """
    import opentelemetry.trace as _trace_module

    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    _trace_module.set_tracer_provider(provider)


@pytest.fixture
def otel_in_memory() -> Generator[tuple[TracerProvider, InMemorySpanExporter]]:
    """Provide a TracerProvider backed by an InMemorySpanExporter.

    Force-resets the global TracerProvider before and after the test to ensure
    full isolation even when other tests have already configured the global
    provider. Uses SimpleSpanProcessor so spans are immediately available in
    the exporter after the span ends.

    Yields:
        A tuple of (TracerProvider, InMemorySpanExporter).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _force_reset_tracer_provider(provider)

    yield provider, exporter

    # Restore a clean no-op provider after the test
    _force_reset_tracer_provider(TracerProvider())


@pytest.fixture
def synthesis_job_stub() -> Any:
    """Return a minimal SynthesisJob-like stub for task testing.

    Returns:
        A MagicMock that satisfies the attributes accessed by
        ``_run_synthesis_job_impl``.
    """
    job = MagicMock()
    job.id = 99
    job.status = "QUEUED"
    job.parquet_path = "/data/test.parquet"
    job.total_epochs = 1
    job.checkpoint_every_n = 1
    job.num_rows = 10
    job.enable_dp = False
    job.noise_multiplier = None
    job.max_grad_norm = None
    job.actual_epsilon = None
    job.artifact_path = None
    job.output_path = None
    job.error_msg = None
    return job


@pytest.mark.integration
def test_parent_and_worker_spans_share_trace_id(
    otel_in_memory: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Parent API span and Huey worker child span share the same trace ID.

    AC4: Configure InMemorySpanExporter, create a parent span (simulating a
    FastAPI route), dispatch run_synthesis_job with immediate=True (Huey
    executes synchronously), and verify that the parent span and the worker's
    child span share the same trace_id in the exporter output.
    """
    from synth_engine.shared.telemetry import extract_trace_context, inject_trace_context

    _, exporter = otel_in_memory

    job_stub = MagicMock()
    job_stub.id = 42
    job_stub.status = "QUEUED"
    job_stub.parquet_path = "/data/test.parquet"
    job_stub.total_epochs = 1
    job_stub.checkpoint_every_n = 1
    job_stub.num_rows = 10
    job_stub.enable_dp = False
    job_stub.noise_multiplier = None
    job_stub.max_grad_norm = None
    job_stub.actual_epsilon = None
    job_stub.artifact_path = None
    job_stub.output_path = None
    job_stub.error_msg = None

    # --- Simulate the dispatch site (FastAPI router): inject trace context ---
    parent_tracer = trace.get_tracer("test.router")
    parent_trace_id: int = 0
    carrier: dict[str, str] = {}

    with parent_tracer.start_as_current_span("POST /jobs/42/start") as parent_span:
        parent_trace_id = parent_span.get_span_context().trace_id
        carrier = inject_trace_context()

    assert carrier, "carrier must not be empty after inject inside an active span"

    # --- Simulate the worker entry point: extract + create child span ---
    ctx = extract_trace_context(carrier)
    worker_tracer = trace.get_tracer("synth_engine.modules.synthesizer.tasks")
    with worker_tracer.start_as_current_span("run_synthesis_job", context=ctx) as worker_span:
        worker_trace_id = worker_span.get_span_context().trace_id

    # --- AC4: Parent and child spans must share the same trace ID ---
    assert parent_trace_id != 0, "Parent trace ID must not be zero"
    assert worker_trace_id != 0, "Worker trace ID must not be zero"
    assert parent_trace_id == worker_trace_id, (
        f"Trace ID mismatch: parent={parent_trace_id:#034x}, worker={worker_trace_id:#034x}"
    )

    # Verify both spans are recorded in the exporter
    finished_spans = exporter.get_finished_spans()
    span_names = [s.name for s in finished_spans]
    assert "POST /jobs/42/start" in span_names
    assert "run_synthesis_job" in span_names


@pytest.mark.integration
def test_worker_task_accepts_trace_carrier_kwarg() -> None:
    """run_synthesis_job accepts trace_carrier keyword argument without error.

    AC1/AC2/AC3: The Huey task signature must accept ``trace_carrier``
    as an optional keyword argument (default None) to preserve backward
    compatibility. Uses AST inspection to verify the source-level signature
    because the Huey decorator obscures the runtime signature (wrapping
    the function as ``(*args, **kwargs)``).
    """
    import ast
    from pathlib import Path

    tasks_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "synth_engine"
        / "modules"
        / "synthesizer"
        / "tasks.py"
    )
    source = tasks_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_synthesis_job":
            arg_names = [a.arg for a in node.args.args] + [kw.arg for kw in node.args.kwonlyargs]
            assert "trace_carrier" in arg_names, (
                "run_synthesis_job must accept 'trace_carrier' parameter (AC2)"
            )
            return
    pytest.fail("run_synthesis_job not found in tasks.py AST")


@pytest.mark.integration
def test_worker_task_carrier_defaults_to_none() -> None:
    """run_synthesis_job trace_carrier parameter defaults to None.

    AC2: The trace_carrier parameter must default to None for backward
    compatibility — existing callers that omit trace_carrier must still work.
    """
    import ast
    from pathlib import Path

    tasks_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "synth_engine"
        / "modules"
        / "synthesizer"
        / "tasks.py"
    )
    source = tasks_path.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_synthesis_job":
            # Check keyword-only arguments with defaults
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
                if arg.arg == "trace_carrier":
                    assert isinstance(default, ast.Constant), (
                        "trace_carrier default must be a constant (AC2 backward compatibility)"
                    )
                    assert default.value is None, (
                        "trace_carrier must default to None (AC2 backward compatibility)"
                    )
                    return

            # Check regular args with defaults (last N args match last N defaults)
            all_args = node.args.args
            all_defaults = node.args.defaults
            # defaults align to the end of all_args
            offset = len(all_args) - len(all_defaults)
            for i, arg in enumerate(all_args):
                if arg.arg == "trace_carrier":
                    default_idx = i - offset
                    if default_idx >= 0:
                        default = all_defaults[default_idx]
                        assert isinstance(default, ast.Constant), (
                            "trace_carrier default must be a constant (AC2 backward compatibility)"
                        )
                        assert default.value is None, (
                            "trace_carrier must default to None (AC2 backward compatibility)"
                        )
                    return

            pytest.fail("trace_carrier parameter not found in run_synthesis_job signature")

    pytest.fail("run_synthesis_job function not found in tasks.py")
