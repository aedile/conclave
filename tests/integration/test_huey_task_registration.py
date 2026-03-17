"""Integration tests verifying that Huey tasks are registered and discoverable.

The Huey worker process discovers tasks by importing task modules.  If a task
module is not importable, or if the ``@huey.task()`` decorator is absent, the
worker silently drops all messages for that task type — leading to jobs stuck
in QUEUED status with no error.

This test module verifies that:
1. ``synth_engine.modules.synthesizer.tasks`` is importable.
2. ``run_synthesis_job`` is registered on the Huey instance (accessible via
   ``huey._registry._registry``).
3. The Huey instance used by tasks is the shared singleton from
   ``synth_engine.shared.task_queue``.
4. The bootstrapper DI factories (``set_dp_wrapper_factory``,
   ``set_spend_budget_fn``) can be called without error after module import.

Failure mode prevention
-----------------------
A missing ``@huey.task()`` decorator would cause the task to be a plain
function — Huey would never route messages to it.  An import error in the
task module would prevent the worker from starting.  These tests provide an
early warning before deploying to an environment with a live Huey worker.

Implementation note on the Huey registry API
---------------------------------------------
Huey does not expose a public ``all_tasks()`` method in its current release.
The task registry is accessible via ``huey._registry._registry``, which is a
``dict[str, type[Task]]`` mapping qualified task names to task classes.  This
is the same registry the worker consults when deserialising incoming messages.
Using a private attribute is a conscious tradeoff: the alternative (calling
``run_synthesis_job.huey`` and comparing identity) only validates the singleton
link, not the registry membership.  Both assertions are included for defence
in depth.

CONSTITUTION Priority 0: Security — no PII, no real credentials.
CONSTITUTION Priority 3: TDD — tests written before implementation.

Task: P26-T26.4 — HTTP Round-Trip Integration Tests (AC4)
"""

from __future__ import annotations

from typing import cast

import pytest

from synth_engine.shared.protocols import DPWrapperProtocol, SpendBudgetProtocol

pytestmark = pytest.mark.integration


def test_synthesizer_tasks_module_is_importable() -> None:
    """The synthesizer tasks module can be imported without raising ImportError.

    This test guards against broken imports in the task module that would
    prevent the Huey worker from starting.  Import errors in task modules
    are silent at deploy time — the worker crashes on startup rather than
    giving a clear error about the missing task.
    """
    try:
        import synth_engine.modules.synthesizer.tasks as tasks_module  # noqa: F401
    except ImportError as exc:
        pytest.fail(f"synth_engine.modules.synthesizer.tasks is not importable: {exc}")


def test_run_synthesis_job_is_registered_on_huey_instance() -> None:
    """run_synthesis_job is registered as a Huey task on the shared instance.

    After importing the tasks module (which executes the ``@huey.task()``
    decorator), the Huey instance's task registry must contain an entry
    whose key contains ``run_synthesis_job``.

    Huey uses the fully-qualified function name as the task key by default,
    so the key is ``synth_engine.modules.synthesizer.tasks.run_synthesis_job``.

    This test verifies that the worker will correctly receive and route
    ``run_synthesis_job`` messages from the queue.
    """
    # Import the tasks module to trigger @huey.task() registration
    import synth_engine.modules.synthesizer.tasks as tasks_module
    from synth_engine.shared.task_queue import huey

    # huey._registry._registry is a dict[str, type[Task]] mapping
    # qualified task names to Huey task classes.  This is the authoritative
    # registry the Huey worker consults when deserialising incoming messages.
    registered_task_names: set[str] = set(huey._registry._registry.keys())

    assert len(registered_task_names) > 0, (
        "No tasks registered on the Huey instance after importing tasks module. "
        "Check that @huey.task() decorators are present in tasks.py."
    )

    # The run_synthesis_job task must be in the registry.
    assert any("run_synthesis_job" in name for name in registered_task_names), (
        f"run_synthesis_job not found in Huey task registry. "
        f"Registered tasks: {sorted(registered_task_names)}"
    )

    # Verify run_synthesis_job is exposed on the tasks module and is callable.
    assert hasattr(tasks_module, "run_synthesis_job"), (
        "tasks module does not expose run_synthesis_job"
    )
    assert callable(tasks_module.run_synthesis_job), "run_synthesis_job must be callable"


def test_huey_instance_is_shared_singleton() -> None:
    """The Huey instance used by tasks is the shared singleton from task_queue.

    The bootstrapper DI wiring (ADR-0029) registers task factories on the
    synthesizer tasks module.  If the task module used a different Huey
    instance than the one in ``shared/task_queue``, workers and enqueuing
    code would be disconnected.

    This test verifies that both modules reference the same object.
    """
    import synth_engine.modules.synthesizer.tasks as tasks_module
    from synth_engine.shared.task_queue import huey as shared_huey

    # TaskWrapper.huey is the Huey instance the task was registered on.
    task_huey = tasks_module.run_synthesis_job.huey
    assert task_huey is shared_huey, (
        "run_synthesis_job.huey is not the shared singleton from shared.task_queue. "
        "This would disconnect the worker from the enqueuing code."
    )


def test_bootstrapper_di_factories_can_be_called_after_import() -> None:
    """Bootstrapper DI injection helpers are callable after module import.

    ``set_dp_wrapper_factory`` and ``set_spend_budget_fn`` are called at
    application startup by ``bootstrapper/main.py`` (Rule 8 — operational
    wiring).  This test verifies they accept a callable argument without
    raising, confirming the DI injection contract is intact.

    Uses cast() to satisfy the typed signatures without importing heavy
    DP/Privacy modules that require optional dependencies (torch, opacus).
    The factories are never actually called during this test — only the
    injection call site is exercised.
    """
    from collections.abc import Callable

    import synth_engine.modules.synthesizer.tasks as tasks_module

    # Provide a no-op function typed as Callable[[float, float], DPWrapperProtocol].
    # Cast is required because a plain no-op cannot structurally satisfy
    # DPWrapperProtocol (missing .epsilon_spent method).  The purpose of this
    # test is to verify the injection call site, not to test the factory itself.
    def _noop_dp_factory(max_grad_norm: float, noise_multiplier: float) -> DPWrapperProtocol:
        raise NotImplementedError("dummy — should never be called in this test")

    noop_spend_fn = cast(SpendBudgetProtocol, lambda **kwargs: None)

    try:
        tasks_module.set_dp_wrapper_factory(
            cast(Callable[[float, float], DPWrapperProtocol], _noop_dp_factory)
        )
    except Exception as exc:
        pytest.fail(f"set_dp_wrapper_factory raised unexpectedly: {exc}")

    try:
        tasks_module.set_spend_budget_fn(noop_spend_fn)
    except Exception as exc:
        pytest.fail(f"set_spend_budget_fn raised unexpectedly: {exc}")
