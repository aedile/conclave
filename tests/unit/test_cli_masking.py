"""Unit tests for CLI masking transformer and column masks configuration.

Tests cover the _build_masking_transformer() factory function and the
_COLUMN_MASKS configuration that drives deterministic PII masking.

Covers:
  - Factory returns a callable
  - Non-PII tables pass through unchanged
  - Input dict is never mutated (pure function contract)
  - PII columns in 'customers' table are masked
  - None-valued PII columns pass through unchanged
  - Correct masking algorithm functions are used per column
  - 'persons' table PII columns are masked (E2E integration schema)
  - Missing configured columns do not raise KeyError

CONSTITUTION Priority 0: Security — PII masking correctness tests.
Task: P21-T21.1 — Fix CLI masking config to match sample data schema
Task: P21-T21.2 — Masking algorithm split: first_name, last_name, address
Task: P26-T26.6 — Split from test_cli.py for maintainability
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Masking transformer builder
# ---------------------------------------------------------------------------


class TestBuildMaskingTransformer:
    """Tests for the _build_masking_transformer() factory function.

    Covers:
    - Factory returns a callable (smoke test).
    - Non-PII tables pass through unchanged (no-mask path).
    - Input dict is never mutated (pure function contract).
    - PII columns in the 'customers' table are replaced with masked values.
    - None-valued PII columns in 'customers' pass through unchanged (null guard).
    - Unknown tables ('persons', 'transactions') pass through unchanged.
    """

    def test_build_masking_transformer_transforms_unknown_table_to_passthrough(self) -> None:
        """_build_masking_transformer() returns a transformer that passes unknown tables through.

        A callable() check proves nothing about the transformer behaviour.
        This test asserts the transformer is a working function: calling it with
        an unknown table name must return the row unchanged.
        """
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row = {"id": 1, "amount": 100}
        result = transformer("nonexistent_table", row)
        assert result == row, (
            f"Transformer must pass unknown tables through unchanged, got {result!r}"
        )

    def test_masking_transformer_passthrough_for_unknown_table(self) -> None:
        """Transformer returns row unchanged for tables not in masking config."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row = {"id": 1, "amount": 100}
        result = transformer("transactions", row)
        assert result == row

    def test_masking_transformer_does_not_modify_input_dict(self) -> None:
        """Transformer must not mutate the input row dict (pure function contract)."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        original_row = {"id": 1, "amount": 100}
        original_copy = dict(original_row)
        transformer("transactions", original_row)
        assert original_row == original_copy

    def test_masking_transformer_masks_pii_columns_for_customers_table(self) -> None:
        """Transformer replaces PII column values for the 'customers' table.

        P21-T21.1: masking config was updated from 'persons'/'full_name' to
        'customers' with the correct columns from the sample data schema.
        """
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "ssn": "123-45-6789",
            "phone": "555-867-5309",
            "address": "123 Main St",
        }
        result = transformer("customers", row)
        assert result["first_name"] != "Alice"
        assert result["last_name"] != "Smith"
        assert result["email"] != "alice@example.com"
        assert result["ssn"] != "123-45-6789"
        assert result["phone"] != "555-867-5309"
        assert result["address"] != "123 Main St"
        assert result["id"] == 1  # non-PII column unchanged

    def test_masking_transformer_masks_customers_pii_columns(self) -> None:
        """Transformer must produce single-word first_name and last_name (P21-T21.2).

        P21-T21.2: mask_name uses Faker.name() which produces "First Last" (two words).
        After the fix, first_name must use mask_first_name (Faker.first_name()) and
        last_name must use mask_last_name (Faker.last_name()) — both single words.
        """
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 42,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "ssn": "123-45-6789",
            "phone": "555-867-5309",
            "address": "123 Main St, Springfield",
        }
        result = transformer("customers", row)

        assert result["id"] == 42, "non-PII id column must be unchanged"
        assert result["first_name"] != "Alice", "first_name must be masked"
        assert result["last_name"] != "Smith", "last_name must be masked"
        assert " " not in result["first_name"], (
            f"first_name must be a single word after masking, got: '{result['first_name']}'. "
            "P21-T21.2: use mask_first_name (Faker.first_name()), not mask_name."
        )
        assert " " not in result["last_name"], (
            f"last_name must be a single word after masking, got: '{result['last_name']}'. "
            "P21-T21.2: use mask_last_name (Faker.last_name()), not mask_name."
        )

    def test_masking_transformer_masks_persons_table(self) -> None:
        """'persons' is a configured PII table — full_name/email/ssn are masked.

        P24-T24.2: added 'persons' entry to _COLUMN_MASKS to support the E2E
        integration test schema (persons/accounts/transactions).  A row from the
        'persons' table must have its PII columns replaced with masked values.
        """
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "full_name": "Alice Smith",
            "email": "alice@example.com",
            "ssn": "123-45-6789",
        }
        result = transformer("persons", row)
        assert result["id"] == 1, "non-PII id column must be unchanged"
        assert result["full_name"] != "Alice Smith", "full_name must be masked"
        assert result["email"] != "alice@example.com", "email must be masked"
        assert result["ssn"] != "123-45-6789", "ssn must be masked"

    def test_masking_transformer_passthrough_for_none_pii_values(self) -> None:
        """Transformer passes through None-valued PII columns in 'customers' unchanged."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "first_name": None,
            "last_name": None,
            "email": None,
            "ssn": None,
            "phone": None,
            "address": None,
        }
        result = transformer("customers", row)
        assert result["first_name"] is None
        assert str(result["first_name"]) == "None"
        assert result["last_name"] is None
        assert str(result["last_name"]) == "None"
        assert result["email"] is None
        assert str(result["email"]) == "None"
        assert result["ssn"] is None
        assert str(result["ssn"]) == "None"
        assert result["phone"] is None
        assert str(result["phone"]) == "None"
        assert result["address"] is None
        assert str(result["address"]) == "None"

    def test_masking_transformer_persons_row_missing_configured_column(self) -> None:
        """Transformer does not crash when a configured PII column is absent from row.

        T25.1 edge case: the _COLUMN_MASKS config for 'persons' includes 'ssn',
        but some rows may not carry that key.  The 'if col in result' guard in
        _build_masking_transformer() must silently skip missing columns.

        Arrange: a 'persons' row without the 'ssn' key.
        Act: invoke transformer("persons", row).
        Assert:
        - No KeyError is raised.
        - Present PII columns (full_name, email) are still masked.
        - The missing 'ssn' key is not present in the result.
        """
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 5,
            "full_name": "Bob Jones",
            "email": "bob@example.com",
            # ssn key is intentionally absent
        }
        result = transformer("persons", row)
        assert result["id"] == 5, "non-PII id column must be unchanged"
        assert result["full_name"] != "Bob Jones", "full_name must be masked"
        assert result["email"] != "bob@example.com", "email must be masked"
        assert "ssn" not in result, "absent ssn key must not appear in result"


# ---------------------------------------------------------------------------
# P21-T21.1: Masking config must match sample data schema (customers table)
# P21-T21.2: Masking algorithm split — function references must be pinned
# ---------------------------------------------------------------------------


class TestColumnMasksConfig:
    """P21-T21.1/T21.2 — _COLUMN_MASKS must reference the 'customers' table with
    the correct algorithm functions for each PII column.

    The sample data schema uses a 'customers' table with columns:
    first_name, last_name, email, ssn, phone, address.

    T21.1: The previous config incorrectly referenced 'persons' with 'full_name'.
    T21.2: first_name/last_name/address must use dedicated functions, not mask_name.
    """

    def test_column_masks_has_customers_key(self) -> None:
        """_COLUMN_MASKS must contain a 'customers' key for the sample data schema."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        assert "customers" in _COLUMN_MASKS, (
            "_COLUMN_MASKS must have a 'customers' key to match sample data schema. "
            "Previous config incorrectly used 'persons'."
        )

    def test_column_masks_has_persons_key(self) -> None:
        """_COLUMN_MASKS must contain a 'persons' key for the E2E integration schema.

        P24-T24.2: added 'persons' entry to _COLUMN_MASKS so the CLI masking
        transformer handles the integration test schema (persons/accounts/transactions).
        The 'persons' table has full_name, email, and ssn PII columns.
        """
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        assert "persons" in _COLUMN_MASKS, (
            "_COLUMN_MASKS must contain a 'persons' key for E2E integration test schema. "
            "Without it, masking silently skips all rows in the persons table."
        )

    def test_customers_config_has_first_name(self) -> None:
        """customers masking config must include 'first_name' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "first_name" in customers_masks, (
            "customers masking config must include 'first_name'. "
            "Sample data has separate first_name/last_name columns (not full_name)."
        )

    def test_customers_config_has_last_name(self) -> None:
        """customers masking config must include 'last_name' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "last_name" in customers_masks, (
            "customers masking config must include 'last_name'. "
            "Sample data has separate first_name/last_name columns (not full_name)."
        )

    def test_customers_config_has_email(self) -> None:
        """customers masking config must include 'email' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "email" in customers_masks, "customers masking config must include 'email'."

    def test_customers_config_has_ssn(self) -> None:
        """customers masking config must include 'ssn' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "ssn" in customers_masks, "customers masking config must include 'ssn'."

    def test_customers_config_has_phone(self) -> None:
        """customers masking config must include 'phone' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "phone" in customers_masks, "customers masking config must include 'phone'."

    def test_customers_config_has_address(self) -> None:
        """customers masking config must include 'address' column."""
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert "address" in customers_masks, "customers masking config must include 'address'."

    def test_customers_first_name_uses_mask_first_name(self) -> None:
        """customers.first_name must use mask_first_name, not mask_name.

        P21-T21.2: Pins the actual function object reference.  mask_name uses
        Faker.name() which produces "First Last" (two words) — wrong for first_name.
        """
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS
        from synth_engine.modules.masking.algorithms import mask_first_name

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert customers_masks.get("first_name") is mask_first_name, (
            "customers.first_name must reference mask_first_name (not mask_name). "
            "mask_name produces 'First Last'; mask_first_name produces a single word."
        )

    def test_customers_last_name_uses_mask_last_name(self) -> None:
        """customers.last_name must use mask_last_name, not mask_name.

        P21-T21.2: Pins the actual function object reference.  mask_name uses
        Faker.name() which produces "First Last" (two words) — wrong for last_name.
        """
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS
        from synth_engine.modules.masking.algorithms import mask_last_name

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert customers_masks.get("last_name") is mask_last_name, (
            "customers.last_name must reference mask_last_name (not mask_name). "
            "mask_name produces 'First Last'; mask_last_name produces a single word."
        )

    def test_customers_address_uses_mask_address(self) -> None:
        """customers.address must use mask_address, not mask_name.

        P21-T21.2: Pins the actual function object reference.  mask_name uses
        Faker.name() which produces a person's name — wrong for a street address.
        """
        from synth_engine.bootstrapper.cli import _COLUMN_MASKS
        from synth_engine.modules.masking.algorithms import mask_address

        customers_masks = _COLUMN_MASKS.get("customers", {})
        assert customers_masks.get("address") is mask_address, (
            "customers.address must reference mask_address (not mask_name). "
            "mask_name produces a person's name; mask_address produces a street address."
        )

    def test_masking_transformer_masks_customers_pii_columns(self) -> None:
        """Transformer must replace PII column values for the 'customers' table."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 42,
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "ssn": "123-45-6789",
            "phone": "555-867-5309",
            "address": "123 Main St, Springfield",
        }
        result = transformer("customers", row)

        assert result["id"] == 42, "non-PII id column must be unchanged"
        assert result["first_name"] != "Alice", "first_name must be masked"
        assert result["last_name"] != "Smith", "last_name must be masked"
        assert result["email"] != "alice@example.com", "email must be masked"
        assert result["ssn"] != "123-45-6789", "ssn must be masked"
        assert result["phone"] != "555-867-5309", "phone must be masked"
        assert result["address"] != "123 Main St, Springfield", "address must be masked"
        assert " " not in result["first_name"], (
            f"first_name must be a single word, got: '{result['first_name']}' (P21-T21.2)"
        )
        assert " " not in result["last_name"], (
            f"last_name must be a single word, got: '{result['last_name']}' (P21-T21.2)"
        )

    def test_masking_transformer_customers_is_deterministic(self) -> None:
        """Masking for 'customers' rows must be deterministic — same input yields same output."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 1,
            "first_name": "Bob",
            "last_name": "Jones",
            "email": "bob@example.com",
            "ssn": "987-65-4321",
            "phone": "555-123-4567",
            "address": "456 Oak Ave",
        }
        result_a = transformer("customers", row)
        result_b = transformer("customers", row)

        assert result_a == result_b, (
            "Masking transformer must be deterministic: same input must always produce "
            "the same output. Got different results on two consecutive calls."
        )

    def test_masking_transformer_customers_none_values_pass_through(self) -> None:
        """None-valued PII columns in 'customers' must pass through unchanged."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        row: dict[str, Any] = {
            "id": 7,
            "first_name": None,
            "last_name": None,
            "email": None,
            "ssn": None,
            "phone": None,
            "address": None,
        }
        result = transformer("customers", row)

        for col in ("first_name", "last_name", "email", "ssn", "phone", "address"):
            assert result[col] is None, f"None value for '{col}' must pass through unchanged"
            assert str(result[col]) == "None"

    def test_masking_transformer_does_not_mutate_customers_row(self) -> None:
        """Transformer must not mutate the customers row input dict."""
        from synth_engine.bootstrapper.cli import _build_masking_transformer

        transformer = _build_masking_transformer()
        original_row: dict[str, Any] = {
            "id": 3,
            "first_name": "Carol",
            "last_name": "White",
            "email": "carol@example.com",
            "ssn": "111-22-3333",
            "phone": "555-000-0000",
            "address": "789 Pine Rd",
        }
        original_copy = dict(original_row)
        transformer("customers", original_row)
        assert original_row == original_copy, "transformer must not mutate the input dict"
