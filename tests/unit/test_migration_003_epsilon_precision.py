"""Unit tests for Alembic migration 003: epsilon column precision fix (P16-T16.1).

Verifies that:
  1. Migration 003 exists in alembic/versions/.
  2. It chains from revision 002 (down_revision = "002").
  3. It contains ALTER COLUMN (op.alter_column) calls for the three epsilon columns:
     - ``total_allocated_epsilon`` (privacy_ledger table)
     - ``total_spent_epsilon``     (privacy_ledger table)
     - ``epsilon_spent``           (privacy_transaction table)
  4. The ALTER targets NUMERIC(20, 10) in the upgrade direction.
  5. The ALTER reverts to Float in the downgrade direction.
  6. The ledger.py module-level docstring no longer contains the stale
     "Alembic not yet initialised" / T8.4 debt note; it references migration 003.
  7. ADR-0030 exists documenting the Float → Numeric precision decision.

Known failure patterns guarded:
  - ADV-050 / P14-T14.1: epsilon assertions must use Decimal not float.
  - ADV-074: NUMERIC(20,10) precision limits sub-1e-10 DB storage — expected behaviour.

No external services are required.  These are pure file-inspection tests.

CONSTITUTION Priority 4: Correctness — Float8 vs NUMERIC(20,10) mismatch is a P0 risk.
Task: P16-T16.1 — Alembic Migration 003: Epsilon Column Precision Fix
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
ALEMBIC_VERSIONS = REPO_ROOT / "alembic" / "versions"
LEDGER_MODULE = REPO_ROOT / "src" / "synth_engine" / "modules" / "privacy" / "ledger.py"
ADR_DIR = REPO_ROOT / "docs" / "adr"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_migration_003() -> Path | None:
    """Return the Path of the migration 003 file, or None if absent."""
    for f in ALEMBIC_VERSIONS.glob("*.py"):
        if f.name.startswith("__"):
            continue
        if "003" in f.name:
            return f
    return None


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestMigration003Exists:
    """Migration 003 file must be present in alembic/versions/."""

    def test_migration_003_file_exists(self) -> None:
        """A migration file whose name contains '003' must exist."""
        migration = _find_migration_003()
        assert migration is not None, (
            "No alembic version file with '003' in its name was found under "
            f"{ALEMBIC_VERSIONS}. P16-T16.1 requires migration 003 to ALTER "
            "epsilon columns from FLOAT8 to NUMERIC(20,10)."
        )


class TestMigration003RevisionChain:
    """Migration 003 must chain correctly from 002."""

    def test_revision_is_003(self) -> None:
        """migration 003 file must declare revision = '003'."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found — cannot verify revision ID."
        content = migration.read_text(encoding="utf-8")
        assert 'revision: str = "003"' in content or "revision = '003'" in content, (
            f"{migration.name}: must declare revision = '003'."
        )

    def test_down_revision_is_002(self) -> None:
        """migration 003 must set down_revision = '002' to chain after connection tables."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found — cannot verify down_revision."
        content = migration.read_text(encoding="utf-8")
        assert '"002"' in content or "'002'" in content, (
            f"{migration.name}: down_revision must reference '002' (the connection/setting "
            "tables migration). The epsilon precision fix must be applied after those tables "
            "exist."
        )
        assert "down_revision" in content, f"{migration.name}: must declare down_revision."


class TestMigration003AlterColumns:
    """Migration 003 upgrade must ALTER the three epsilon columns to NUMERIC(20,10)."""

    def test_upgrade_alters_total_allocated_epsilon(self) -> None:
        """Upgrade must alter total_allocated_epsilon on privacy_ledger."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "op.alter_column" in content, (
            f"{migration.name}: must use op.alter_column to change column types."
        )
        assert "total_allocated_epsilon" in content, (
            f"{migration.name}: must alter 'total_allocated_epsilon'."
        )

    def test_upgrade_alters_total_spent_epsilon(self) -> None:
        """Upgrade must alter total_spent_epsilon on privacy_ledger."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "total_spent_epsilon" in content, (
            f"{migration.name}: must alter 'total_spent_epsilon'."
        )

    def test_upgrade_alters_epsilon_spent(self) -> None:
        """Upgrade must alter epsilon_spent on privacy_transaction."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "epsilon_spent" in content, (
            f"{migration.name}: must alter 'epsilon_spent' on privacy_transaction."
        )

    def test_upgrade_targets_numeric_20_10(self) -> None:
        """Upgrade must reference Numeric(20, 10) as the target column type."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        # Accept both sa.Numeric and Numeric references with precision 20, scale 10
        assert "Numeric" in content or "NUMERIC" in content, (
            f"{migration.name}: upgrade must target Numeric / NUMERIC type."
        )
        assert "20" in content, f"{migration.name}: upgrade must reference precision=20."
        assert "10" in content, f"{migration.name}: upgrade must reference scale=10."

    def test_downgrade_reverts_to_float(self) -> None:
        """Downgrade must revert epsilon columns back to Float (FLOAT8)."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "Float" in content or "FLOAT" in content, (
            f"{migration.name}: downgrade must revert to Float / FLOAT type."
        )

    def test_privacy_ledger_referenced_in_upgrade(self) -> None:
        """privacy_ledger table name must appear in the migration."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "privacy_ledger" in content, (
            f"{migration.name}: must reference 'privacy_ledger' table."
        )

    def test_privacy_transaction_referenced_in_upgrade(self) -> None:
        """privacy_transaction table name must appear in the migration."""
        migration = _find_migration_003()
        assert migration is not None, "Migration 003 not found."
        content = migration.read_text(encoding="utf-8")
        assert "privacy_transaction" in content, (
            f"{migration.name}: must reference 'privacy_transaction' table."
        )


class TestLedgerDocstringUpdated:
    """ledger.py docstring must reference migration 003, not the stale T8.4 debt note."""

    def test_stale_t8_4_migration_note_removed(self) -> None:
        """The 'Alembic not yet initialised — T8.4' migration debt note must be gone."""
        content = LEDGER_MODULE.read_text(encoding="utf-8")
        assert "Alembic not yet initialised" not in content, (
            "ledger.py still contains the stale migration debt note "
            "'Alembic not yet initialised — T8.4'. "
            "P16-T16.1 requires updating the docstring to reference migration 003."
        )

    def test_migration_003_referenced_in_docstring(self) -> None:
        """ledger.py docstring must reference migration 003."""
        content = LEDGER_MODULE.read_text(encoding="utf-8")
        assert "migration 003" in content or "Migration 003" in content, (
            "ledger.py docstring must reference 'migration 003' so readers know "
            "the migration debt has been resolved."
        )


class TestADR0030Exists:
    """ADR-0030 documenting the Float → Numeric precision decision must exist."""

    def test_adr_0030_file_exists(self) -> None:
        """ADR-0030 file must exist in docs/adr/."""
        adr_files = list(ADR_DIR.glob("ADR-0030*.md"))
        assert adr_files, (
            f"No ADR-0030 file found under {ADR_DIR}. "
            "CLAUDE.md Rule 6 requires an ADR for the Float → Numeric "
            "technology substitution (ADV-050)."
        )

    def test_adr_0030_mentions_adv_050(self) -> None:
        """ADR-0030 must reference ADV-050 (the floating-point drift finding)."""
        adr_files = list(ADR_DIR.glob("ADR-0030*.md"))
        assert adr_files, "ADR-0030 not found."
        content = adr_files[0].read_text(encoding="utf-8")
        assert "ADV-050" in content, (
            "ADR-0030 must reference ADV-050 as the original rationale for "
            "the Float → Numeric change."
        )

    def test_adr_0030_mentions_numeric(self) -> None:
        """ADR-0030 must mention NUMERIC(20, 10) as the chosen type."""
        adr_files = list(ADR_DIR.glob("ADR-0030*.md"))
        assert adr_files, "ADR-0030 not found."
        content = adr_files[0].read_text(encoding="utf-8")
        assert "NUMERIC" in content or "Numeric" in content, (
            "ADR-0030 must describe NUMERIC(20,10) as the chosen column type."
        )

    def test_adr_0030_mentions_migration(self) -> None:
        """ADR-0030 must mention the migration path (migration 003)."""
        adr_files = list(ADR_DIR.glob("ADR-0030*.md"))
        assert adr_files, "ADR-0030 not found."
        content = adr_files[0].read_text(encoding="utf-8")
        assert "migration" in content.lower(), (
            "ADR-0030 must describe the migration path for existing deployments."
        )
