"""Unit tests for Alembic migrations 005 and 006, and JobCreateRequest/JobResponse
schema fields introduced in T23.1.

Split from test_synthesizer_tasks_lifecycle.py (P56 review finding — file exceeded 600 LOC).
Zero test deletion. All test logic is preserved verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# T23.1 — Migration 005: default PrivacyLedger seeding (AC1)
# ---------------------------------------------------------------------------

_ALEMBIC_VERSIONS = Path(__file__).parent.parent.parent / "alembic" / "versions"


def _find_migration_005() -> Path | None:
    """Return the Path of the migration 005 file, or None if absent."""
    for f in _ALEMBIC_VERSIONS.glob("*.py"):
        if f.name.startswith("__"):
            continue
        if "005" in f.name:
            return f
    return None


class TestMigration005:
    """Tests for Alembic migration 005 — default PrivacyLedger seeding (AC1).

    These are structural file-inspection tests — they verify the migration file
    has the correct revision chain and SQL patterns without running a live database.
    Follows the pattern established in test_migration_003_epsilon_precision.py.
    """

    def test_migration_005_file_exists(self) -> None:
        """Migration 005 file must exist in alembic/versions/."""
        path = _find_migration_005()
        assert path is not None, (
            "Migration 005 not found in alembic/versions/. Expected a file matching '005*.py'."
        )
        assert path.is_file(), f"Migration 005 path {path} is not a regular file"

    def test_migration_005_revision_is_005(self) -> None:
        """Migration 005 must have revision='005'."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'revision: str = "005"' in content, f"Expected revision='005' in {path.name}"

    def test_migration_005_down_revision_is_004(self) -> None:
        """Migration 005 must depend on revision 004."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'down_revision: str | None = "004"' in content, (
            f"Expected down_revision='004' in {path.name}"
        )

    def test_migration_005_seeds_privacy_ledger_row(self) -> None:
        """Migration 005 upgrade() must INSERT a privacy_ledger row."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert "privacy_ledger" in content, "Expected 'privacy_ledger' INSERT in migration 005"
        assert "INSERT" in content.upper(), "Expected INSERT statement in migration 005 upgrade()"

    def test_migration_005_downgrade_deletes_seeded_row(self) -> None:
        """Migration 005 downgrade() must DELETE the seeded row."""
        path = _find_migration_005()
        assert path is not None, "Migration 005 file not found"
        content = path.read_text(encoding="utf-8")
        assert "DELETE" in content.upper(), "Expected DELETE statement in migration 005 downgrade()"


# ---------------------------------------------------------------------------
# T23.1 — Migration 006: num_rows and output_path columns (RED)
# ---------------------------------------------------------------------------

_ALEMBIC_VERSIONS_T23 = Path(__file__).parent.parent.parent / "alembic" / "versions"


def _find_migration_006() -> Path | None:
    """Return the Path of migration 006 file, or None if absent."""
    for f in _ALEMBIC_VERSIONS_T23.glob("*.py"):
        if f.name.startswith("__"):
            continue
        if "006" in f.name:
            return f
    return None


class TestMigration006:
    """Alembic migration 006 must add num_rows and output_path to synthesis_job (AC schema)."""

    def test_migration_006_file_exists(self) -> None:
        """Migration 006 file must exist in alembic/versions/."""
        path = _find_migration_006()
        assert path is not None, (
            "Migration 006 not found in alembic/versions/. Expected a file matching '006*.py'."
        )
        assert path.is_file(), f"Migration 006 path {path} is not a regular file"

    def test_migration_006_revision_is_006(self) -> None:
        """Migration 006 must have revision='006'."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'revision: str = "006"' in content, f"Expected revision='006' in {path.name}"

    def test_migration_006_down_revision_is_005(self) -> None:
        """Migration 006 must depend on revision 005."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert 'down_revision: str | None = "005"' in content, (
            f"Expected down_revision='005' in {path.name}"
        )

    def test_migration_006_adds_num_rows_column(self) -> None:
        """Migration 006 upgrade() must add a num_rows column."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert "num_rows" in content, (
            f"Expected 'num_rows' column in migration 006; not found in {path.name}"
        )

    def test_migration_006_adds_output_path_column(self) -> None:
        """Migration 006 upgrade() must add an output_path column."""
        path = _find_migration_006()
        assert path is not None, "Migration 006 file not found"
        content = path.read_text(encoding="utf-8")
        assert "output_path" in content, (
            f"Expected 'output_path' column in migration 006; not found in {path.name}"
        )


# ---------------------------------------------------------------------------
# T23.1 — JobCreateRequest and JobResponse schema (RED)
# ---------------------------------------------------------------------------


class TestJobSchemaNumRows:
    """JobCreateRequest and JobResponse must include num_rows (P23-T23.1 schema AC)."""

    def test_job_create_request_has_num_rows_field(self) -> None:
        """JobCreateRequest must accept num_rows as a required positive integer."""
        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        req = JobCreateRequest(
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            total_epochs=10,
            num_rows=500,
        )
        assert req.num_rows == 500

    def test_job_create_request_num_rows_must_be_positive(self) -> None:
        """JobCreateRequest must reject num_rows <= 0."""
        from pydantic import ValidationError

        from synth_engine.bootstrapper.schemas.jobs import JobCreateRequest

        with pytest.raises(ValidationError):
            JobCreateRequest(
                table_name="customers",
                parquet_path="/tmp/customers.parquet",
                total_epochs=10,
                num_rows=0,
            )

    def test_job_response_has_num_rows_field(self) -> None:
        """JobResponse must include a num_rows field."""
        from synth_engine.bootstrapper.schemas.jobs import JobResponse

        resp = JobResponse(
            id=1,
            status="QUEUED",
            current_epoch=0,
            total_epochs=10,
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            artifact_path=None,
            error_msg=None,
            checkpoint_every_n=5,
            enable_dp=True,
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            actual_epsilon=None,
            num_rows=500,
            output_path=None,
        )
        assert resp.num_rows == 500

    def test_job_response_has_output_path_field(self) -> None:
        """JobResponse must include an output_path field (None until generation completes)."""
        from synth_engine.bootstrapper.schemas.jobs import JobResponse

        resp = JobResponse(
            id=1,
            status="COMPLETE",
            current_epoch=10,
            total_epochs=10,
            table_name="customers",
            parquet_path="/tmp/customers.parquet",
            artifact_path="/output/job_1_epoch_10.pkl",
            error_msg=None,
            checkpoint_every_n=5,
            enable_dp=False,
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            actual_epsilon=None,
            num_rows=500,
            output_path="/output/job_1_synthetic.parquet",
        )
        assert resp.output_path == "/output/job_1_synthetic.parquet"
