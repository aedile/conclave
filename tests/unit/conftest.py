"""Unit-test specific pytest configuration.

This conftest is scoped to ``tests/unit/`` and adds guardrails that ensure
unit tests are fully isolated from the developer's ``.env`` file.

Fix: P47 — Suppress .env file reading in unit tests so that
     ``monkeypatch.setenv`` / ``monkeypatch.delenv`` have full, reliable effect
     on every ``ConclaveSettings`` construction — regardless of whether the
     field's value is also present in the local ``.env`` file.

T49.3: Added ``jwt_secret_key_env`` opt-in fixture for tests that need a
       valid ``JWT_SECRET_KEY`` set in the environment via monkeypatch.
"""

from __future__ import annotations

from typing import Any

import pytest

#: A test-safe JWT secret long enough to satisfy HS256 requirements.
#: Used by the opt-in ``jwt_secret_key_env`` fixture.
#: This is a deterministic test credential — not a production secret.
_TEST_JWT_SECRET: str = "test-secret-key-that-is-long-enough-for-hs256"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _suppress_env_file_in_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ConclaveSettings to never read from the .env file.

    Unit tests exercise isolated code paths using ``monkeypatch.setenv`` and
    ``monkeypatch.delenv`` to control environment variables.  However,
    pydantic-settings' ``BaseSettings`` reads from the ``.env`` file **in
    addition** to ``os.environ``, with env-var values taking precedence over
    ``.env`` values.  When a test uses ``monkeypatch.delenv("DATABASE_URL")``,
    pydantic falls back to the ``.env`` file and returns the real development
    value — breaking assertions that expect an empty / default value.

    This fixture patches ``ConclaveSettings.__init__`` so that every call
    (whether from test code or from the production code under test) is
    equivalent to ``ConclaveSettings(_env_file=None)``.  Passing
    ``_env_file=None`` disables the ``.env`` file loader entirely, ensuring
    the test's monkeypatched environment is the single source of truth.

    This patch is applied BEFORE each test and rolled back AFTER each test by
    pytest's monkeypatch machinery.

    Args:
        monkeypatch: The pytest monkeypatch fixture for reversible patching.
    """
    try:
        from synth_engine.shared.settings import ConclaveSettings

        _original_init = ConclaveSettings.__init__

        def _init_no_env_file(self: Any, **kwargs: Any) -> None:
            kwargs.setdefault("_env_file", None)
            _original_init(self, **kwargs)

        monkeypatch.setattr(ConclaveSettings, "__init__", _init_no_env_file)
    except ImportError:
        pass  # Module not yet loaded during discovery


@pytest.fixture
def jwt_secret_key_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set JWT_SECRET_KEY to a test-safe value for the duration of the test.

    This is an opt-in fixture — tests must request it explicitly by name.
    It is NOT autouse, preventing unexpected side effects on tests that
    exercise the absent-JWT_SECRET_KEY code path.

    The fixture uses ``monkeypatch`` only — no direct ``os.environ`` mutation.
    The monkeypatch machinery guarantees the value is removed after the test.

    Args:
        monkeypatch: The pytest monkeypatch fixture for reversible env mutation.

    Returns:
        The JWT secret string injected into the environment, so tests can use
        it to construct valid tokens.

    Example::

        def test_valid_token_accepted(jwt_secret_key_env: str) -> None:
            token = build_token(secret=jwt_secret_key_env)
            ...
    """
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_JWT_SECRET)
    return _TEST_JWT_SECRET
