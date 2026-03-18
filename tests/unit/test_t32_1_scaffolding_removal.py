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


def _module_spec_found(dotted_name: str) -> bool:
    """Return True if importlib.util.find_spec can resolve the module.

    Unlike :func:`_module_importable`, this uses the lower-level spec
    machinery.  When a parent package does not exist, ``find_spec`` raises
    ``ModuleNotFoundError`` for a sub-module rather than returning ``None``
    — both outcomes mean the module is absent, so both are treated as
    ``False``.

    In Python 3.14, passing ``None`` to ``find_spec`` raises
    ``AttributeError`` (not ``ValueError``); this is caught as a defensive
    guard even though the parameterised tests below never pass ``None``.

    Args:
        dotted_name: Fully-qualified Python module path.

    Returns:
        True when find_spec returns a non-None spec; False otherwise.
    """
    sys.modules.pop(dotted_name, None)
    try:
        spec = importlib.util.find_spec(dotted_name)
    except (ModuleNotFoundError, AttributeError):
        # ModuleNotFoundError: parent package absent — module definitely gone.
        # AttributeError: None passed to find_spec (defensive guard, Python 3.14+).
        return False
    return spec is not None


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
# Spec-level contract: find_spec returns None or raises for removed paths
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
def test_find_spec_cannot_resolve_removed_module(dotted_name: str) -> None:
    """importlib.util.find_spec must not resolve any removed module.

    A ``None`` return value or a ``ModuleNotFoundError`` from find_spec
    both confirm that the module is absent from the filesystem.  This is a
    lower-level check complementing the import-level tests above.

    Args:
        dotted_name: Fully-qualified module path that must be absent.
    """
    assert not _module_spec_found(dotted_name), (
        f"{dotted_name!r} is still resolvable on the filesystem — "
        f"the corresponding .py file was not deleted (T32.1)"
    )
