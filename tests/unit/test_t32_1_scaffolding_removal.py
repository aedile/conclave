# SUNSET: Phase 38 — evaluate for removal. These tests permanently assert that
# deleted scaffolding modules remain absent. They pass forever and add no ongoing
# value once the deletion is sufficiently old. Reassess at Phase 38.
"""Removal guard tests for T32.1 — Dead Scaffolding Module Cleanup.

These tests exist to verify that the scaffolding modules identified in
Phase 32 as "unwired and never called" have been fully removed from the
codebase. Each test imports a module that should NOT exist and asserts that
the import raises an ImportError. This provides a permanent guard preventing
the dead code from being silently re-introduced.

Modules under test (expected ABSENT):
  - synth_engine.shared.auth.jwt
  - synth_engine.shared.auth.scopes

Modules that were originally absent guards but have been legitimately
re-introduced and are now PRESENT:
  - synth_engine.bootstrapper.dependencies.auth — re-created in T39.1
    as the production JWT authentication middleware.
  - synth_engine.shared.middleware.idempotency — re-created in T45.1
    as the production Redis-backed idempotency middleware (TBD-07).
  - synth_engine.shared.tasks.reaper — re-created in T45.2 as the
    production orphan task reaper (TBD-08).
  - synth_engine.shared.tasks — re-created in T45.2 as the tasks package
    housing the production reaper.

Also asserts no surviving import sites exist for any of these modules.
"""

import importlib
import importlib.util
import sys

import pytest

pytestmark = pytest.mark.sunset_phase_38

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


# NOTE: test_shared_middleware_idempotency_is_removed was removed from this file
# in T45.1 — synth_engine.shared.middleware.idempotency has been re-created as the
# production Redis-backed idempotency middleware (TBD-07).

# NOTE: test_shared_tasks_reaper_is_removed was removed from this file
# in T45.2 — synth_engine.shared.tasks.reaper has been re-created as the
# production orphan task reaper (TBD-08).

# NOTE: test_shared_tasks_package_is_removed was removed from this file
# in T45.2 — synth_engine.shared.tasks has been re-created as the tasks
# package housing the orphan task reaper (TBD-08).

# ---------------------------------------------------------------------------
# Spec-level contract: find_spec returns None or raises for removed paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dotted_name",
    [
        "synth_engine.shared.auth.jwt",
        "synth_engine.shared.auth.scopes",
        "synth_engine.shared.auth",
        # NOTE: The following modules were originally in this list but have been
        # legitimately re-introduced and removed from absence checks:
        # - synth_engine.bootstrapper.dependencies.auth → T39.1 (JWT auth middleware)
        # - synth_engine.shared.middleware.idempotency → T45.1 (idempotency middleware)
        # - synth_engine.shared.tasks.reaper → T45.2 (orphan task reaper)
        # - synth_engine.shared.tasks → T45.2 (tasks package)
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
