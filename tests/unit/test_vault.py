"""Unit tests for VaultState, derive_kek, and SealGateMiddleware.

RED Phase — all tests must fail before implementation exists.

CONSTITUTION Priority 3: TDD
Task: P2-T2.4 — Vault Observability
Task: T36.4 — Edge-case: very long passphrase (>1 MB)
Task: fix/review-critical-issues — thread-safety test for VaultState
"""

from __future__ import annotations

import base64
import os
import threading
from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_vault_state() -> Generator[None]:
    """Reset VaultState class-level state after each test for isolation."""
    yield
    # Teardown: import lazily so the fixture works even before impl exists
    try:
        from synth_engine.shared.security.vault import VaultState

        VaultState.reset()
    except ImportError:
        pass


@pytest.fixture
def vault_salt_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision VAULT_SEAL_SALT in the environment and return the raw value."""
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)
    return salt


# ---------------------------------------------------------------------------
# VaultState core tests
# ---------------------------------------------------------------------------


def test_vault_starts_sealed() -> None:
    """VaultState.is_sealed() is True without any unseal call."""
    from synth_engine.shared.security.vault import VaultState

    assert VaultState.is_sealed() is True


def test_sealed_vault_raises_on_get_kek() -> None:
    """get_kek() raises VaultSealedError when the vault is sealed."""
    from synth_engine.shared.security.vault import VaultSealedError, VaultState

    with pytest.raises(VaultSealedError) as exc_info:
        VaultState.get_kek()

    assert exc_info.value.status_code == 423


def test_unseal_stores_kek_in_memory(vault_salt_env: str) -> None:
    """After unseal, get_kek() returns a 32-byte key."""
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal(bytearray(b"correct-horse-battery-staple"))  # nosec B105 # pragma: allowlist secret
    kek = VaultState.get_kek()

    assert isinstance(kek, bytes)
    assert len(kek) == 32


def test_seal_clears_kek(vault_salt_env: str) -> None:
    """After unseal then seal, get_kek() raises VaultSealedError again."""
    from synth_engine.shared.security.vault import VaultSealedError, VaultState

    VaultState.unseal(bytearray(b"correct-horse-battery-staple"))  # nosec B105 # pragma: allowlist secret
    VaultState.seal()

    assert VaultState.is_sealed() is True
    with pytest.raises(VaultSealedError):
        VaultState.get_kek()


def test_derive_kek_is_deterministic(vault_salt_env: str) -> None:
    """derive_kek returns the same bytes for the same passphrase and salt."""
    from synth_engine.shared.security.vault import derive_kek

    salt = base64.urlsafe_b64decode(vault_salt_env + "==")
    passphrase = b"deterministic-passphrase"  # nosec B105 # pragma: allowlist secret

    kek1 = derive_kek(passphrase, salt)
    kek2 = derive_kek(passphrase, salt)

    assert kek1 == kek2


def test_different_passphrase_produces_different_kek(vault_salt_env: str) -> None:
    """derive_kek returns distinct bytes for different passphrases."""
    from synth_engine.shared.security.vault import derive_kek

    salt = base64.urlsafe_b64decode(vault_salt_env + "==")

    kek1 = derive_kek(b"passphrase-alpha", salt)  # nosec B105 # pragma: allowlist secret
    kek2 = derive_kek(b"passphrase-beta", salt)  # nosec B105 # pragma: allowlist secret

    assert kek1 != kek2


def test_missing_vault_salt_raises_vault_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """unseal() raises VaultConfigError when VAULT_SEAL_SALT is not set."""
    monkeypatch.delenv("VAULT_SEAL_SALT", raising=False)

    from synth_engine.shared.security.vault import VaultConfigError, VaultState

    with pytest.raises(VaultConfigError, match="VAULT_SEAL_SALT"):
        VaultState.unseal(bytearray(b"any-passphrase"))  # nosec B105 # pragma: allowlist secret


def test_short_vault_salt_raises_vault_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """unseal() raises VaultConfigError when VAULT_SEAL_SALT decodes to fewer than 16 bytes."""
    # base64url-encode a 4-byte value — too short
    short_salt = base64.urlsafe_b64encode(b"\x00" * 4).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", short_salt)

    from synth_engine.shared.security.vault import VaultConfigError, VaultState

    with pytest.raises(VaultConfigError, match="16 bytes"):
        VaultState.unseal(bytearray(b"any-passphrase"))  # nosec B105 # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Edge-case guard tests (QA review finding P2-T2.4)
# ---------------------------------------------------------------------------


def test_empty_passphrase_raises_vault_empty_passphrase_error(vault_salt_env: str) -> None:
    """unseal() raises VaultEmptyPassphraseError when passphrase is empty bytes/bytearray."""
    from synth_engine.shared.security.vault import VaultEmptyPassphraseError, VaultState

    with pytest.raises(VaultEmptyPassphraseError, match="[Pp]assphrase"):
        VaultState.unseal(bytearray(b""))  # nosec B105 # pragma: allowlist secret


def test_re_unseal_while_unsealed_raises_vault_already_unsealed_error(vault_salt_env: str) -> None:
    """unseal() raises VaultAlreadyUnsealedError when the vault is already unsealed."""
    from synth_engine.shared.security.vault import VaultAlreadyUnsealedError, VaultState

    VaultState.unseal(bytearray(b"first-passphrase"))  # nosec B105 # pragma: allowlist secret

    with pytest.raises(VaultAlreadyUnsealedError, match="already unsealed"):
        VaultState.unseal(bytearray(b"second-passphrase"))  # nosec B105 # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Edge-case: very long passphrase (T36.4)
# ---------------------------------------------------------------------------


def test_unseal_with_very_long_passphrase_succeeds(vault_salt_env: str) -> None:
    """unseal() with a passphrase exceeding 1 MB must not raise and must produce a 32-byte KEK.

    PBKDF2-HMAC-SHA256 accepts passphrases of arbitrary length.  A pathological
    1 MB passphrase must not cause a buffer overflow, silent truncation, or an
    unexpected exception.  The function must complete and leave the vault unsealed
    with a valid 32-byte KEK in memory.

    Note: PBKDF2 with 600_000 iterations on a large passphrase is intentionally
    slow — this is a correctness test, not a performance benchmark.  The passphrase
    is ASCII-only to keep the encoding fast.
    """
    from synth_engine.shared.security.vault import VaultState

    # 1 MB + 1 byte of ASCII 'a' — well beyond any internal buffer assumption
    long_passphrase = bytearray(b"a" * (1024 * 1024 + 1))  # nosec B105 # pragma: allowlist secret

    VaultState.unseal(long_passphrase)

    assert VaultState.is_sealed() is False, "Vault must be unsealed after unseal() call"
    kek = VaultState.get_kek()
    assert isinstance(kek, bytes), "KEK must be bytes"
    assert len(kek) == 32, f"KEK must be exactly 32 bytes, got {len(kek)}"


# ---------------------------------------------------------------------------
# SealGateMiddleware and /unseal endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sealed_app_returns_423_for_regular_routes() -> None:
    """While sealed, any non-exempt route returns 423."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/some-protected-route")

    assert response.status_code == 423
    assert "sealed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_sealed_app_allows_unseal_post() -> None:
    """POST /unseal is exempt from the seal gate (must not return 423)."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # We expect something other than 423 — 422 (validation error) or 400
        response = await client.post("/unseal", json={})

    assert response.status_code != 423


@pytest.mark.asyncio
async def test_sealed_app_allows_health() -> None:
    """GET /health returns 200 even when the vault is sealed."""
    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_unseal_endpoint_unseals_vault(
    vault_salt_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /unseal with a valid passphrase returns 200 and unseals the vault."""
    from synth_engine.bootstrapper.main import create_app
    from synth_engine.shared.security.vault import VaultState

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/unseal",
            json={"passphrase": "test-passphrase"},  # nosec B105 # pragma: allowlist secret
        )

    assert response.status_code == 200
    assert response.json() == {"status": "unsealed"}
    assert VaultState.is_sealed() is False


@pytest.mark.asyncio
async def test_require_unsealed_raises_when_sealed() -> None:
    """require_unsealed dependency raises HTTPException(423) while vault is sealed."""
    from fastapi import HTTPException

    from synth_engine.bootstrapper.dependencies.vault import require_unsealed

    with pytest.raises(HTTPException) as exc_info:
        await require_unsealed()

    assert exc_info.value.status_code == 423


@pytest.mark.asyncio
async def test_require_unsealed_returns_none_when_unsealed(vault_salt_env: str) -> None:
    """require_unsealed() returns None (no exception) when the vault is unsealed."""
    from synth_engine.bootstrapper.dependencies.vault import require_unsealed
    from synth_engine.shared.security.vault import VaultState

    VaultState.unseal(bytearray(b"any-valid-passphrase"))  # nosec B105 # pragma: allowlist secret

    # Should not raise; the return value is None
    result = await require_unsealed()
    assert result is None


@pytest.mark.asyncio
async def test_unseal_endpoint_returns_400_on_missing_salt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /unseal returns 400 when VAULT_SEAL_SALT is not set."""
    monkeypatch.delenv("VAULT_SEAL_SALT", raising=False)

    from synth_engine.bootstrapper.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/unseal",
            json={"passphrase": "test-passphrase"},  # nosec B105 # pragma: allowlist secret
        )

    assert response.status_code == 400
    assert "VAULT_SEAL_SALT" in response.json()["detail"]


# ---------------------------------------------------------------------------
# T38.2: Vault unseal timing side-channel tests
# ---------------------------------------------------------------------------


def test_derive_kek_called_even_for_empty_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """derive_kek must be called even when passphrase is empty (T38.2).

    Structural test: verifies constant-time behavior without relying on
    wall-clock measurements. An empty passphrase must still incur the full
    PBKDF2 cost — only the passphrase check happens AFTER key derivation.

    AC1 (T38.2): Proves the empty-passphrase path is not a timing oracle.
    AC3 (T38.2): Mock-based verification that derive_kek is called for empty input.
    """
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)

    from unittest.mock import patch

    from synth_engine.shared.security.vault import VaultEmptyPassphraseError, VaultState

    with patch(
        "synth_engine.shared.security.vault.derive_kek",
        wraps=__import__("synth_engine.shared.security.vault", fromlist=["derive_kek"]).derive_kek,
    ) as mock_derive_kek:
        with pytest.raises(VaultEmptyPassphraseError):
            VaultState.unseal(bytearray(b""))  # nosec B105 # pragma: allowlist secret

    mock_derive_kek.assert_called_once()


def test_vault_empty_passphrase_still_raises_vault_empty_passphrase_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VaultEmptyPassphraseError is still raised for empty passphrases after timing fix.

    AC2 (T38.2): The observable behaviour (exception type) must not change.
    """
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("VAULT_SEAL_SALT", salt)

    from synth_engine.shared.security.vault import VaultEmptyPassphraseError, VaultState

    with pytest.raises(VaultEmptyPassphraseError, match="[Pp]assphrase"):
        VaultState.unseal(bytearray(b""))  # nosec B105 # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Thread safety: concurrent unseal attempts (fix/review-critical-issues)
# ---------------------------------------------------------------------------


def test_concurrent_unseal_only_one_succeeds(vault_salt_env: str) -> None:
    """Only one concurrent unseal call wins; the rest raise VaultAlreadyUnsealedError.

    Arrange: Start N threads that each attempt to unseal the vault
    simultaneously.
    Act: Collect successes and VaultAlreadyUnsealedError exceptions.
    Assert: Exactly one thread succeeded, the rest raised the idempotency error.
    This proves the _lock prevents the race where two callers both pass the
    _is_sealed check and overwrite each other's KEK.
    """
    from synth_engine.shared.security.vault import VaultAlreadyUnsealedError, VaultState

    n_threads = 10
    successes: list[int] = []
    already_unsealed_errors: list[int] = []
    other_errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt_unseal() -> None:
        try:
            VaultState.unseal(bytearray(b"concurrent-passphrase"))  # nosec B105 # pragma: allowlist secret
            with lock:
                successes.append(1)
        except VaultAlreadyUnsealedError:
            with lock:
                already_unsealed_errors.append(1)
        except Exception as exc:  # broad catch intentional
            with lock:
                other_errors.append(exc)

    threads = [threading.Thread(target=attempt_unseal) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not other_errors, f"Unexpected errors: {other_errors}"
    assert len(successes) == 1, (
        f"Expected exactly 1 successful unseal, got {len(successes)}. "
        f"This indicates a race condition in VaultState."
    )
    assert len(already_unsealed_errors) == n_threads - 1, (
        f"Expected {n_threads - 1} VaultAlreadyUnsealedError, got {len(already_unsealed_errors)}."
    )
    # Confirm vault is now unsealed with a valid KEK
    kek = VaultState.get_kek()
    assert isinstance(kek, bytes)
    assert len(kek) == 32


def test_vault_state_has_class_level_lock() -> None:
    """VaultState must expose a class-level _lock attribute of type threading.Lock."""
    from synth_engine.shared.security.vault import VaultState

    assert hasattr(VaultState, "_lock"), "VaultState must have a _lock class attribute"
    assert isinstance(VaultState._lock, type(threading.Lock())), (  # type: ignore[attr-defined]
        "_lock must be a threading.Lock instance"
    )


def test_vault_state_is_sealed_and_kek_have_classvar_annotations() -> None:
    """VaultState._is_sealed and _kek must be annotated as ClassVar.

    ClassVar annotation ensures mypy and static analysis tools correctly
    model these as class-level attributes, not instance attributes.
    This test inspects __annotations__ on the class to confirm both fields
    carry ClassVar-typed annotations.

    Task: P58 — Fix ClassVar annotations on VaultState._is_sealed and _kek
    """
    import typing

    from synth_engine.shared.security.vault import VaultState

    annotations = typing.get_type_hints(VaultState, include_extras=True)

    assert "_is_sealed" in annotations, "VaultState._is_sealed must have a type annotation"
    assert "_kek" in annotations, "VaultState._kek must have a type annotation"

    # Verify ClassVar wrapping — get_type_hints strips ClassVar on Python < 3.11,
    # so we inspect __annotations__ directly to confirm the raw string contains ClassVar.
    raw_annotations: dict[str, object] = VaultState.__annotations__
    is_sealed_annotation = str(raw_annotations.get("_is_sealed", ""))
    kek_annotation = str(raw_annotations.get("_kek", ""))

    assert "ClassVar" in is_sealed_annotation, (
        f"VaultState._is_sealed annotation must use ClassVar; got: {is_sealed_annotation!r}"
    )
    assert "ClassVar" in kek_annotation, (
        f"VaultState._kek annotation must use ClassVar; got: {kek_annotation!r}"
    )
