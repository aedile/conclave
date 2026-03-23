"""Tests verifying Redis TLS URL promotion is consolidated into shared/task_queue.py.

ADV-P47-02: The ``_promote_redis_url_to_tls`` helper must not be duplicated in
``bootstrapper/dependencies/redis.py``.  The canonical implementation lives in
``shared/task_queue.py`` and the bootstrapper must import from there.

CONSTITUTION Priority 0: Security — single source of truth for URL promotion logic
CONSTITUTION Priority 3: TDD — RED before GREEN (Rule 22)
Advisory: ADV-P47-02 — Redis TLS URL promotion duplication
"""

from __future__ import annotations

import inspect
import types

import pytest

pytestmark = pytest.mark.unit


def test_bootstrapper_redis_dep_does_not_define_own_promote_function() -> None:
    """bootstrapper/dependencies/redis.py must not define its own _promote_redis_url_to_tls.

    After consolidation the function must be absent from the bootstrapper redis
    module's own source — it should be imported from shared/task_queue.py
    rather than duplicated.
    """
    from synth_engine.bootstrapper.dependencies import redis as redis_dep

    # If the module defines _promote_redis_url_to_tls itself (rather than
    # importing it), the function's __module__ will be the bootstrapper redis
    # module.  After consolidation the name should either be absent from the
    # module namespace or — if re-exported as a convenience alias — its
    # __module__ must point to shared.task_queue.
    func = getattr(redis_dep, "_promote_redis_url_to_tls", None)

    if func is not None:
        # The name exists in the namespace — verify it is NOT defined here
        assert func.__module__ != redis_dep.__name__, (
            "_promote_redis_url_to_tls is defined directly in "
            "bootstrapper/dependencies/redis.py instead of being imported "
            "from synth_engine.shared.task_queue (ADV-P47-02)."
        )


def test_bootstrapper_redis_dep_source_has_no_duplicate_implementation() -> None:
    """The source of bootstrapper/dependencies/redis.py must not contain a duplicate impl.

    Inspects the module's own members and confirms that no function defined in
    that file re-implements the ``redis://`` → ``rediss://`` string replacement.
    This catches the case where someone re-exports the function under the same
    name (which would hide the violation from test_bootstrapper_redis_dep_does_not_define_own_promote_function).
    """
    from synth_engine.bootstrapper.dependencies import redis as redis_dep

    module_file = redis_dep.__file__
    assert module_file is not None, "Could not determine source file for redis dep module"

    with open(module_file) as f:
        source = f.read()

    # The duplication signature: a function body that replaces redis:// with rediss://
    # using a string prefix check.  The import from task_queue does NOT need this logic.
    assert 'return "rediss://" +' not in source, (
        "bootstrapper/dependencies/redis.py contains a duplicate implementation of "
        "the Redis TLS URL promotion logic.  Remove it and import from "
        "synth_engine.shared.task_queue instead (ADV-P47-02)."
    )


def test_shared_task_queue_exposes_promote_function() -> None:
    """shared/task_queue.py must expose _promote_redis_url_to_tls as the canonical impl."""
    from synth_engine.shared import task_queue

    assert hasattr(task_queue, "_promote_redis_url_to_tls"), (
        "synth_engine.shared.task_queue must define _promote_redis_url_to_tls "
        "as the single canonical implementation (ADV-P47-02)."
    )

    func = task_queue._promote_redis_url_to_tls
    assert callable(func)
    # Verify the function is defined in this module (not imported from elsewhere)
    assert func.__module__ == "synth_engine.shared.task_queue"
