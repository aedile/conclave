"""Unit tests for input validation and artifact signature helper on the download endpoint.

Covers:
  - JobCreateRequest table_name field validation (alphanumeric+underscore only)
  - _verify_artifact_signature unit tests (incremental HMAC, OSError sentinel)

CONSTITUTION Priority 3: TDD RED Phase.
Task: P23-T23.2 — /jobs/{id}/download Endpoint
Task: P26-T26.6 — Split from test_download_endpoint.py for maintainability
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class TestJobCreateRequestTableNameValidation:
    """Tests for table_name field validation on JobCreateRequest (BLOCKER Finding 1)."""

    def test_valid_table_name_alphanumeric_underscore(self) -> None:
        """JobCreateRequest accepts alphanumeric and underscore table names."""
        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        req = JobCreateRequest(
            table_name="my_table_123",
            parquet_path="/tmp/x.parquet",
            total_epochs=1,
            num_rows=1,
        )
        assert req.table_name == "my_table_123"

    def test_invalid_table_name_with_double_quote_raises(self) -> None:
        """JobCreateRequest rejects table_name containing a double-quote character."""
        import pydantic

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name='bad"name',
                parquet_path="/tmp/x.parquet",
                total_epochs=1,
                num_rows=1,
            )

    def test_invalid_table_name_with_newline_raises(self) -> None:
        """JobCreateRequest rejects table_name containing a newline character."""
        import pydantic

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="bad\nname",
                parquet_path="/tmp/x.parquet",
                total_epochs=1,
                num_rows=1,
            )

    def test_invalid_table_name_with_space_raises(self) -> None:
        """JobCreateRequest rejects table_name containing a space."""
        import pydantic

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="bad name",
                parquet_path="/tmp/x.parquet",
                total_epochs=1,
                num_rows=1,
            )

    def test_invalid_table_name_with_semicolon_raises(self) -> None:
        """JobCreateRequest rejects table_name containing a semicolon (SQL injection vector)."""
        import pydantic

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="bad;name",
                parquet_path="/tmp/x.parquet",
                total_epochs=1,
                num_rows=1,
            )

    def test_invalid_table_name_empty_raises(self) -> None:
        """JobCreateRequest rejects an empty table_name."""
        import pydantic

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="",
                parquet_path="/tmp/x.parquet",
                total_epochs=1,
                num_rows=1,
            )


class TestVerifyArtifactSignatureUnit:
    """Unit tests for _verify_artifact_signature helper (incremental HMAC, OSError sentinel)."""

    def test_oserror_on_sidecar_read_returns_none(self, tmp_path: Path) -> None:
        """_verify_artifact_signature returns None (not False) when the sidecar raises OSError."""
        from synth_engine.bootstrapper.routers.jobs_streaming import _verify_artifact_signature

        parquet_path = tmp_path / "artifact.parquet"
        parquet_path.write_bytes(b"data")

        sig_path = tmp_path / "artifact.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        signing_key = b"\xab" * 32

        original_read_bytes = Path.read_bytes

        def _failing_read(self: Path) -> bytes:
            if str(self).endswith(".sig"):
                raise OSError("simulated read failure")
            return original_read_bytes(self)

        with (
            patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}),
            patch.object(Path, "read_bytes", _failing_read),
        ):
            result = _verify_artifact_signature(str(parquet_path))

        assert result is None

    def test_incremental_hmac_matches_single_pass(self, tmp_path: Path) -> None:
        """_verify_artifact_signature result matches a reference HMAC over the full file bytes.

        Confirms that the incremental chunked-read HMAC is equivalent to
        computing HMAC over the complete file in one pass.
        """
        from synth_engine.bootstrapper.routers.jobs_streaming import _verify_artifact_signature

        parquet_bytes = b"A" * 200_000  # 200 KiB — forces multiple 64 KiB chunks
        parquet_path = tmp_path / "big.parquet"
        parquet_path.write_bytes(parquet_bytes)

        signing_key = b"\x42" * 32
        reference_digest = hmac.new(signing_key, parquet_bytes, hashlib.sha256).digest()
        sig_path = tmp_path / "big.parquet.sig"
        sig_path.write_bytes(reference_digest)

        with patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}):
            result = _verify_artifact_signature(str(parquet_path))

        assert result == True
        assert result

    def test_oserror_on_artifact_read_returns_none(self, tmp_path: Path) -> None:
        """_verify_artifact_signature returns None when the artifact file raises OSError on read."""
        from synth_engine.bootstrapper.routers.jobs_streaming import _verify_artifact_signature

        parquet_path = tmp_path / "artifact2.parquet"
        parquet_path.write_bytes(b"data")

        sig_path = tmp_path / "artifact2.parquet.sig"
        sig_path.write_bytes(b"\x00" * 32)

        signing_key = b"\xab" * 32

        with (
            patch.dict(os.environ, {"ARTIFACT_SIGNING_KEY": signing_key.hex()}),
            patch("builtins.open", side_effect=OSError("cannot open artifact")),
        ):
            result = _verify_artifact_signature(str(parquet_path))

        assert result is None
        assert str(result) == "None"
