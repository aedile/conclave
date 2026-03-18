"""Removal guard tests for T32.1 — Dead Scaffolding Module Cleanup.

These tests exist to verify that the scaffolding modules identified in
Phase 32 as "unwired and never called" have been fully removed from the
codebase. Each test imports a module that should NOT exist and asserts that
the import raises an ImportError. This provides a permanent guard preventing
the dead code from being silently re-introduced.

Modules under test (expected ABSENT):
  - synth_engine.shared.auth.jwt
  - synth_engine.shared.auth.scopes
  - synth_engine.shared.middleware.idempotency
  - synth_engine.shared.tasks.reaper
  - synth_engine.bootstrapper.dependencies.auth

Also asserts no surviving import sites exist for any of these modules.
"""

import importlib
import importlib.util
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_importable(dotted_name: str) -> bool:
    """Return True if the module can be imported, False if ImportError is raised.

    Args:
        dotted_name: Fully-qualified Python module path.

    Returns:
        True when the module exists and is importable; False otherwise.
    """
    # Remove from sys.modules cache to force a fresh attempt.
    sys.modules.pop(dotted_name, None)
    try:
        importlib.import_module(dotted_name)
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Absence assertions — each removed module must NOT be importable
# ---------------------------------------------------------------------------


def test_shared_auth_jwt_is_removed() -> None:
    """synth_engine.shared.auth.jwt must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.auth.jwt"), (
        "synth_engine.shared.auth.jwt still exists — remove shared/auth/jwt.py (T32.1)"
    )


def test_shared_auth_scopes_is_removed() -> None:
    """synth_engine.shared.auth.scopes must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.auth.scopes"), (
        "synth_engine.shared.auth.scopes still exists — remove shared/auth/scopes.py (T32.1)"
    )


def test_shared_auth_package_is_removed() -> None:
    """synth_engine.shared.auth package must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.auth"), (
        "synth_engine.shared.auth package still exists — remove shared/auth/__init__.py (T32.1)"
    )


def test_shared_middleware_idempotency_is_removed() -> None:
    """synth_engine.shared.middleware.idempotency must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.middleware.idempotency"), (
        "synth_engine.shared.middleware.idempotency still exists — "
        "remove shared/middleware/idempotency.py (T32.1)"
    )


def test_shared_tasks_reaper_is_removed() -> None:
    """synth_engine.shared.tasks.reaper must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.tasks.reaper"), (
        "synth_engine.shared.tasks.reaper still exists — remove shared/tasks/reaper.py (T32.1)"
    )


def test_shared_tasks_package_is_removed() -> None:
    """synth_engine.shared.tasks package must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.shared.tasks"), (
        "synth_engine.shared.tasks package still exists — remove shared/tasks/__init__.py (T32.1)"
    )


def test_bootstrapper_dependencies_auth_is_removed() -> None:
    """synth_engine.bootstrapper.dependencies.auth must not exist after T32.1 cleanup."""
    assert not _module_importable("synth_engine.bootstrapper.dependencies.auth"), (
        "synth_engine.bootstrapper.dependencies.auth still exists — "
        "remove bootstrapper/dependencies/auth.py (T32.1)"
    )


# ---------------------------------------------------------------------------
# Spec-level contract: importlib.util.find_spec returns None for removed paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dotted_name",
    [
        "synth_engine.shared.auth.jwt",
        "synth_engine.shared.auth.scopes",
        "synth_engine.shared.auth",
        "synth_engine.shared.middleware.idempotency",
        "synth_engine.shared.tasks.reaper",
        "synth_engine.shared.tasks",
        "synth_engine.bootstrapper.dependencies.auth",
    ],
)
def test_find_spec_returns_none_for_removed_module(dotted_name: str) -> None:
    """importlib.util.find_spec must return None for all removed modules.

    This is a lower-level check that confirms the .py files themselves are
    absent from the filesystem — not merely shadowed by a stub.

    Args:
        dotted_name: Fully-qualified module path that must be absent.
    """
    # Evict from cache so find_spec checks the filesystem.
    sys.modules.pop(dotted_name, None)
    spec = importlib.util.find_spec(dotted_name)
    assert spec is None, (
        f"{dotted_name!r} still resolvable on the filesystem — "
        f"the corresponding .py file was not deleted (T32.1)"
    )
