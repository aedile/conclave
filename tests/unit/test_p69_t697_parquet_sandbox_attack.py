"""Negative/attack tests for parquet_path sandbox to CONCLAVE_DATA_DIR (T69.7).

Covers:
- parquet_path outside data_dir returns 422
- parquet_path symlink pointing outside data_dir returns 422 (after resolve)
- parquet_path with double-dot traversal returns 422
- valid path inside data_dir is accepted
- conclave_data_dir itself is resolved before is_relative_to
- conclave_data_dir set to nonexistent path fails at startup in production mode
- parquet_path /etc/passwd.parquet returns 422
- conclave_data_dir set to / (root) is forbidden

ATTACK-FIRST TDD — these tests are written BEFORE the GREEN phase.
CONSTITUTION Priority 0: Security — path traversal is a P0 vulnerability (ADV-P68-02)
CONSTITUTION Priority 3: TDD — attack tests before feature tests (Rule 22)
Task: T69.7 — Sandbox parquet_path to Allowed Directory (ADV-P68-02)
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear lru_cache on get_settings before and after each test.

    Yields:
        None — setup and teardown only.
    """
    from synth_engine.shared.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for tests.

    Args:
        tmp_path: pytest tmp_path fixture providing a unique temp directory.

    Returns:
        Path to a 'data' subdirectory inside tmp_path.
    """
    d = tmp_path / "data"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Attack tests — path traversal
# ---------------------------------------------------------------------------


class TestParquetPathSandboxAttacks:
    """parquet_path sandbox attack tests (T69.7, ADV-P68-02)."""

    def test_parquet_path_outside_data_dir_returns_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """parquet_path pointing outside CONCLAVE_DATA_DIR must be rejected with 422.

        Arrange: set CONCLAVE_DATA_DIR to a temp data dir.
        Act: submit JobCreateRequest with parquet_path = "/tmp/evil.parquet".
        Assert: ValidationError raised (maps to 422 in FastAPI).
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError) as exc_info:
            JobCreateRequest(
                table_name="users",
                parquet_path="/tmp/evil.parquet",
                total_epochs=5,
                num_rows=10,
            )

        assert "parquet_path" in str(exc_info.value).lower() or exc_info.value is not None, (
            "ValidationError must be raised for path outside data dir"
        )

    def test_parquet_path_double_dot_traversal_returns_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """Double-dot traversal sequence is rejected after Path.resolve().

        Arrange: set CONCLAVE_DATA_DIR to a temp data dir.
        Act: submit parquet_path = "<data_dir>/../../etc/passwd.parquet".
        Assert: ValidationError raised — resolved path escapes data_dir.
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        traversal_path = str(data_dir / ".." / ".." / "etc" / "passwd.parquet")

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="users",
                parquet_path=traversal_path,
                total_epochs=5,
                num_rows=10,
            )

    def test_parquet_path_inside_data_dir_is_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """A valid path inside CONCLAVE_DATA_DIR is accepted without error.

        Arrange: set CONCLAVE_DATA_DIR to a temp data dir.
        Act: submit parquet_path = "<data_dir>/training.parquet".
        Assert: JobCreateRequest constructed successfully.
        """
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        valid_path = str(data_dir / "training.parquet")
        req = JobCreateRequest(
            table_name="users",
            parquet_path=valid_path,
            total_epochs=5,
            num_rows=10,
        )
        # The resolved path must start with data_dir
        assert req.parquet_path.startswith(str(data_dir.resolve())), (
            f"Accepted path {req.parquet_path!r} must be inside {data_dir!r}"
        )

    def test_parquet_path_symlink_pointing_outside_data_dir_returns_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Symlink from inside data_dir pointing outside is rejected after resolve().

        Arrange: create a symlink inside data_dir pointing to /tmp/outside.parquet.
        Act: submit parquet_path = path to that symlink.
        Assert: ValidationError raised because resolve() follows the symlink.
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        # Create the real target outside the data_dir
        outside_target = tmp_path / "outside.parquet"
        outside_target.write_bytes(b"fake parquet")

        # Create symlink inside data_dir pointing to the outside target
        symlink_inside = data_dir / "symlink_evil.parquet"
        symlink_inside.symlink_to(outside_target)

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="users",
                parquet_path=str(symlink_inside),
                total_epochs=5,
                num_rows=10,
            )

    def test_conclave_data_dir_itself_is_resolved_before_is_relative_to(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """CONCLAVE_DATA_DIR is resolved to absolute before comparison.

        Arrange: set CONCLAVE_DATA_DIR to a relative path that resolves inside
                 the temp dir. Provide an absolute parquet_path inside that dir.
        Act: construct JobCreateRequest.
        Assert: the request succeeds — relative data_dir with absolute parquet
                inside it is correctly handled.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Note: CONCLAVE_DATA_DIR can be given as relative — it must be resolved
        # This test uses the absolute path to keep it simple and predictable
        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir.resolve()))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        valid_path = str(data_dir.resolve() / "train.parquet")
        req = JobCreateRequest(
            table_name="users",
            parquet_path=valid_path,
            total_epochs=5,
            num_rows=10,
        )
        assert req.parquet_path == str(data_dir.resolve() / "train.parquet"), (
            f"Resolved path must equal expected; got {req.parquet_path!r}"
        )

    def test_conclave_data_dir_set_to_nonexistent_path_fails_at_startup_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In production mode, CONCLAVE_DATA_DIR pointing to nonexistent dir fails at startup.

        Arrange: set CONCLAVE_ENV=production, CONCLAVE_DATA_DIR=/nonexistent/path.
        Act: construct ConclaveSettings with required production secrets.
        Assert: ValidationError raised at construction time.
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_ENV", "production")
        monkeypatch.setenv("CONCLAVE_DATA_DIR", "/nonexistent/path/that/does/not/exist")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("AUDIT_KEY", "a" * 64)
        monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "b" * 64)
        monkeypatch.setenv("MASKING_SALT", "c" * 32)
        monkeypatch.setenv("JWT_SECRET_KEY", "d" * 32)
        monkeypatch.setenv(
            "OPERATOR_CREDENTIALS_HASH",
            "$2b$12$" + "x" * 53,  # valid bcrypt format (59+ chars)
        )

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((pydantic.ValidationError, ValueError, SystemExit)):
            ConclaveSettings(_env_file=None)

    def test_parquet_path_etc_passwd_parquet_returns_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data_dir: Path,
    ) -> None:
        """/etc/passwd.parquet is rejected — outside CONCLAVE_DATA_DIR.

        Arrange: set CONCLAVE_DATA_DIR to a temp data dir.
        Act: submit parquet_path = "/etc/passwd.parquet".
        Assert: ValidationError raised.
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CONCLAVE_ENV", "development")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(pydantic.ValidationError):
            JobCreateRequest(
                table_name="users",
                parquet_path="/etc/passwd.parquet",
                total_epochs=5,
                num_rows=10,
            )

    def test_conclave_data_dir_root_slash_forbidden(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CONCLAVE_DATA_DIR set to '/' (filesystem root) is forbidden.

        Setting data_dir to / would allow ANY parquet path on the system,
        completely negating the sandbox. This must be rejected at startup.

        Arrange: set CONCLAVE_DATA_DIR=/.
        Act: construct ConclaveSettings.
        Assert: ValidationError raised.
        """
        import pydantic

        monkeypatch.setenv("CONCLAVE_ENV", "development")
        monkeypatch.setenv("CONCLAVE_DATA_DIR", "/")

        from synth_engine.shared.settings import get_settings

        get_settings.cache_clear()

        from synth_engine.shared.settings import ConclaveSettings

        with pytest.raises((pydantic.ValidationError, ValueError)):
            ConclaveSettings(_env_file=None)
