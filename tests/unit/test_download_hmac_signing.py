"""Unit tests for HMAC signature enforcement on GET /jobs/{id}/download.

Tests verify that the download endpoint enforces artifact integrity when
ARTIFACT_SIGNING_KEY is set to a valid hex key:
  - 200 returned when signature matches
  - 409 returned when signature does not match (tampered file)
  - 409 returned when .sig sidecar file is missing
  - 409 response follows RFC 7807 Problem Details format

Also tests HMAC primitives directly (hmac_signing.py):
  - Signature forgery is rejected
  - Tampered payload is rejected
  - Replay attack (old signature on new data) is rejected
  - Key rotation: old key cannot verify new-key signature
  - Wrong hash algorithm (SHA-1 digest) fails verification
  - Empty payload: signing and verification both work
  - Oversized payload: signing and verification work (no truncation)
  - hmac.compare_digest is used (timing-safe comparison)

CONSTITUTION Priority 3: TDD RED Phase.
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.6 — Split from test_download_endpoint.py for maintainability
Task: T49.1 — Assertion Hardening: Security-Critical Tests
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

from synth_engine.shared.settings import get_settings

pytestmark = pytest.mark.unit


def _vault_license_patches() -> tuple[Any, Any]:
    """Return patches for vault sealed and license state."""
    return (
        patch(
            "synth_engine.bootstrapper.dependencies.vault.VaultState.is_sealed",
            return_value=False,
        ),
        patch(
            "synth_engine.bootstrapper.dependencies.licensing.LicenseState.is_licensed",
            return_value=True,
        ),
    )


class TestDownloadEndpointHMACSigningActive:
    """Tests for HMAC signature enforcement when ARTIFACT_SIGNING_KEY is set."""

    @pytest.mark.asyncio
    async def test_download_valid_signature_returns_200(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 200 when HMAC signature matches."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00real parquet bytes for signing test"
        parquet_path = tmp_path / "signed-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        # Create a 32-byte key and compute a valid signature
        signing_key = b"\xab" * 32
        digest = hmac.new(signing_key, parquet_bytes, hashlib.sha256).digest()
        sig_path = tmp_path / "signed-synthetic.parquet.sig"
        sig_path.write_bytes(digest)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="signed",
                parquet_path="/tmp/signed.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=str(parquet_path),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_invalid_signature_returns_409(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 409 when HMAC signature does not match."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00real parquet bytes for tamper test"
        parquet_path = tmp_path / "tampered-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        # Write a WRONG signature (all zeros)
        sig_path = tmp_path / "tampered-synthetic.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="tampered",
                parquet_path="/tmp/tampered.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=str(parquet_path),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        # Use a valid key — but the stored signature is wrong
        signing_key = b"\xab" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 409
        body = response.json()
        assert "tampered" in body["detail"].lower() or "signature" in body["detail"].lower()

    @pytest.mark.asyncio
    async def test_download_missing_sig_file_returns_409(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download returns 409 when signing key set but .sig file is missing."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1\x00parquet bytes no sig"
        parquet_path = tmp_path / "nosig-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)
        # Deliberately do NOT write the .sig file

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="nosig",
                parquet_path="/tmp/nosig.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=str(parquet_path),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        signing_key = b"\xcd" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_download_409_response_uses_problem_detail_format(self, tmp_path: Path) -> None:
        """GET /jobs/{id}/download 409 response must follow RFC 7807 Problem Details format."""
        from sqlalchemy.pool import StaticPool

        from synth_engine.bootstrapper.dependencies.db import get_db_session
        from synth_engine.bootstrapper.errors import register_error_handlers
        from synth_engine.bootstrapper.main import create_app
        from synth_engine.bootstrapper.routers.jobs import router as jobs_router
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        parquet_bytes = b"PAR1 tamper test bytes"
        parquet_path = tmp_path / "conflict-synthetic.parquet"
        parquet_path.write_bytes(parquet_bytes)

        sig_path = tmp_path / "conflict-synthetic.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            job = SynthesisJob(
                table_name="conflict",
                parquet_path="/tmp/conflict.parquet",
                total_epochs=10,
                num_rows=100,
                status="COMPLETE",
                output_path=str(parquet_path),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        app = create_app()
        register_error_handlers(app)
        app.include_router(jobs_router)

        def _override() -> Any:
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_db_session] = _override
        p1, p2 = _vault_license_patches()

        signing_key = b"\xef" * 32
        with p1, p2, patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/jobs/{job_id}/download")

        assert response.status_code == 409
        body = response.json()
        assert body["status"] == 409
        assert "type" in body
        assert "title" in body
        assert "detail" in body


# ---------------------------------------------------------------------------
# HMAC primitive tests — unit-level, no HTTP layer
# These test the shared/security/hmac_signing.py functions directly so that
# every security property is verifiable without a running web server.
# ---------------------------------------------------------------------------


class TestHMACPrimitiveSecurity:
    """Directly tests compute_hmac, verify_hmac, sign_versioned, verify_versioned."""

    def test_signature_forgery_rejected(self) -> None:
        """A forged (all-zeros) signature must not verify against real data.

        An attacker who does not know the signing key cannot produce a valid
        HMAC by guessing bytes. Any signature that is not derived from the
        correct key must be rejected.
        """
        from synth_engine.shared.security.hmac_signing import verify_hmac

        key = b"\xaa" * 32
        data = b"legitimate artifact payload"
        forged_digest = b"\x00" * 32  # attacker's guess

        assert verify_hmac(key, data, forged_digest) is False
        assert not verify_hmac(key, data, forged_digest)

    def test_tampered_payload_rejected(self) -> None:
        """A signature valid for original data must fail when the payload is modified.

        If an attacker modifies the artifact bytes after signing, verify_hmac
        must return False because the HMAC is computed over the content.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        key = b"\xbb" * 32
        original_data = b"PAR1\x00original artifact content"
        digest = compute_hmac(key, original_data)

        tampered_data = b"PAR1\x00TAMPERED artifact content"
        assert verify_hmac(key, tampered_data, digest) is False
        assert not verify_hmac(key, tampered_data, digest)

    def test_replay_attack_rejected(self) -> None:
        """A signature valid for old data must not verify against new data.

        A replay attack uses a previously valid signature on a different
        payload (e.g., substituting an older artifact version). The HMAC
        over the new data must not match the old signature.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        key = b"\xcc" * 32
        old_data = b"artifact v1 - epsilon=0.5"
        new_data = b"artifact v2 - epsilon=1.0"

        old_sig = compute_hmac(key, old_data)

        # Old signature must not validate the new data
        assert verify_hmac(key, new_data, old_sig) is False
        assert not verify_hmac(key, new_data, old_sig)
        # Old signature still validates old data (sanity check)
        assert verify_hmac(key, old_data, old_sig) is True
        assert verify_hmac(key, old_data, old_sig)

    def test_key_rotation_old_key_cannot_verify_new_signature(self) -> None:
        """A signature produced with the new key must not verify with the old key.

        After key rotation, callers must use the new key. The old key must
        be unable to validate any signature produced by the new key.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        old_key = b"\xdd" * 32
        new_key = b"\xee" * 32
        data = b"artifact content to sign after rotation"

        new_sig = compute_hmac(new_key, data)

        assert verify_hmac(old_key, data, new_sig) is False
        assert not verify_hmac(old_key, data, new_sig)
        assert verify_hmac(new_key, data, new_sig) is True
        assert verify_hmac(new_key, data, new_sig)

    def test_wrong_hash_algorithm_sha1_rejected(self) -> None:
        """A SHA-1 digest must not verify against an SHA-256 HMAC check.

        verify_hmac always computes SHA-256. A 20-byte SHA-1 digest can
        never equal a 32-byte SHA-256 digest, so constant-time comparison
        must return False.
        """
        from synth_engine.shared.security.hmac_signing import verify_hmac

        key = b"\xff" * 32
        data = b"artifact for sha1 vs sha256 test"
        sha1_digest = hmac.new(key, data, hashlib.sha1).digest()  # 20 bytes

        # 20-byte digest cannot match 32-byte SHA-256 result
        assert verify_hmac(key, data, sha1_digest) is False
        assert not verify_hmac(key, data, sha1_digest)

    def test_empty_payload_sign_and_verify(self) -> None:
        """Signing an empty payload must produce a 32-byte digest that verifies.

        Edge case: empty artifact (zero-byte file) must still be signable and
        verifiable without raising or returning garbage.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        key = b"\x01" * 32
        data = b""  # empty payload

        digest = compute_hmac(key, data)

        assert len(digest) == 32, "Empty-payload HMAC must be 32 bytes"
        assert verify_hmac(key, data, digest) is True

    def test_oversized_payload_sign_and_verify(self) -> None:
        """Signing a large payload must work without truncation or rejection.

        The production path reads parquet files that may be many megabytes.
        HMAC operates on a stream and should handle large inputs correctly.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        key = b"\x02" * 32
        # 1 MB of deterministic data (no secrets, just bulk)
        data = b"\xab\xcd" * 524288  # 1 048 576 bytes

        digest = compute_hmac(key, data)

        assert len(digest) == 32, "Oversized-payload HMAC must be 32 bytes"
        assert verify_hmac(key, data, digest) is True

    def test_verify_hmac_uses_compare_digest(self) -> None:
        """verify_hmac must delegate to hmac.compare_digest for constant-time safety.

        This test patches hmac.compare_digest and asserts it is called by
        verify_hmac, proving that the implementation does not fall back to
        a byte-equality check that would leak timing information.
        """
        from unittest.mock import MagicMock, patch

        import synth_engine.shared.security.hmac_signing as hmac_mod

        key = b"\x03" * 32
        data = b"timing-safe test payload"
        digest = hmac.new(key, data, hashlib.sha256).digest()

        sentinel = MagicMock(return_value=True)
        with patch.object(hmac_mod.hmac, "compare_digest", sentinel):
            hmac_mod.verify_hmac(key, data, digest)

        sentinel.assert_called_once()
        assert sentinel.call_count == 1

    def test_compute_hmac_output_is_exactly_32_bytes(self) -> None:
        """compute_hmac must always return exactly 32 bytes (SHA-256 digest size)."""
        from synth_engine.shared.security.hmac_signing import HMAC_DIGEST_SIZE, compute_hmac

        key = b"\x04" * 32
        for label, data in [
            ("empty", b""),
            ("short", b"hi"),
            ("long", b"x" * 10_000),
        ]:
            digest = compute_hmac(key, data)
            assert len(digest) == HMAC_DIGEST_SIZE, (
                f"compute_hmac({label!r}) returned {len(digest)} bytes, expected {HMAC_DIGEST_SIZE}"
            )

    def test_valid_signature_verifies_true(self) -> None:
        """compute_hmac then verify_hmac round-trip must return True.

        Basic sanity: signing and immediately verifying must succeed. This
        guards against any accidental negation in the verification path.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        key = b"\x05" * 32
        data = b"round-trip verification check"
        digest = compute_hmac(key, data)

        assert verify_hmac(key, data, digest) is True
        assert verify_hmac(key, data, digest)

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(b"", id="empty-bytes"),
            pytest.param(b"\x00", id="null-byte"),
            pytest.param(b"\xff" * 32, id="all-0xff-32-bytes"),
            pytest.param(b"normal ascii payload", id="ascii-text"),
        ],
    )
    def test_verify_rejects_wrong_key(self, data: bytes) -> None:
        """verify_hmac must return False when the wrong key is provided.

        Parameterised across several payload types to guard against any
        accidental key-independence in the HMAC computation.
        """
        from synth_engine.shared.security.hmac_signing import compute_hmac, verify_hmac

        correct_key = b"\x10" * 32
        wrong_key = b"\x11" * 32

        digest = compute_hmac(correct_key, data)
        assert verify_hmac(wrong_key, data, digest) is False
        assert not verify_hmac(wrong_key, data, digest)

    def test_versioned_verify_rejects_unknown_key_id(self) -> None:
        """verify_versioned must return False when the key_id is not in key_map.

        If an attacker crafts a versioned signature with a key ID that is not
        registered, the lookup must fail and the signature must be rejected
        without leaking timing information.
        """
        from synth_engine.shared.security.hmac_signing import (
            LEGACY_KEY_ID,
            sign_versioned,
            verify_versioned,
        )

        signing_key = b"\x20" * 32
        unknown_key_id = b"\xff\xff\xff\xff"
        data = b"versioned artifact"

        # Sign with the known key under an unknown key_id
        sig = sign_versioned(signing_key, unknown_key_id, data)

        # key_map only contains LEGACY_KEY_ID — unknown_key_id is absent
        key_map = {LEGACY_KEY_ID: signing_key}
        assert verify_versioned(key_map, data, sig) is False
        assert not verify_versioned(key_map, data, sig)

    def test_versioned_verify_rejects_empty_key_map(self) -> None:
        """verify_versioned must return False when key_map is empty.

        An empty key_map means no signing keys are configured. The function
        must not attempt to verify and must immediately return False.
        """
        from synth_engine.shared.security.hmac_signing import sign_versioned, verify_versioned

        key = b"\x30" * 32
        key_id = b"\x00\x00\x00\x01"
        data = b"test artifact"

        sig = sign_versioned(key, key_id, data)

        assert verify_versioned({}, data, sig) is False
        assert not verify_versioned({}, data, sig)
