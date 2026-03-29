"""Unit tests for bootstrapper/schemas/connections.py — port range validation.

Attack tests for P70 review finding: port field in ConnectionCreateRequest
must reject out-of-range values (0, 65536+) at the schema layer.

Task: P70 — Add port range validation to ConnectionCreateRequest (review finding)
CONSTITUTION Priority 0: Security — reject invalid input at the boundary
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synth_engine.bootstrapper.schemas.connections import ConnectionCreateRequest

pytestmark = pytest.mark.unit

_VALID_BASE = {
    "name": "test-connection",
    "host": "localhost",
    "database": "testdb",
}


class TestConnectionCreateRequestPortValidation:
    """Attack tests: port field must reject values outside [1, 65535]."""

    def test_valid_port_is_accepted(self) -> None:
        """A normal port number (5432) must be accepted."""
        req = ConnectionCreateRequest(**_VALID_BASE, port=5432)
        assert req.port == 5432

    def test_port_lower_boundary_accepted(self) -> None:
        """Port == 1 is the lower boundary and must be accepted."""
        req = ConnectionCreateRequest(**_VALID_BASE, port=1)
        assert req.port == 1

    def test_port_upper_boundary_accepted(self) -> None:
        """Port == 65535 is the upper boundary and must be accepted."""
        req = ConnectionCreateRequest(**_VALID_BASE, port=65535)
        assert req.port == 65535

    def test_port_zero_out_of_range_returns_422(self) -> None:
        """port=0 must raise ValidationError (maps to HTTP 422).

        Port 0 is not a valid TCP port for user services.  An attacker
        or misconfigured client supplying port=0 must be rejected at the
        schema layer before any database connection attempt is made.
        """
        with pytest.raises(ValidationError):
            ConnectionCreateRequest(**_VALID_BASE, port=0)

    def test_port_above_max_out_of_range_returns_422(self) -> None:
        """port=65536 must raise ValidationError (maps to HTTP 422).

        TCP port numbers are unsigned 16-bit integers: valid range is 1-65535.
        Supplying 65536 must be rejected at the schema layer.
        """
        with pytest.raises(ValidationError):
            ConnectionCreateRequest(**_VALID_BASE, port=65536)

    def test_port_negative_returns_422(self) -> None:
        """A negative port number must raise ValidationError (maps to HTTP 422)."""
        with pytest.raises(ValidationError):
            ConnectionCreateRequest(**_VALID_BASE, port=-1)

    def test_port_very_large_returns_422(self) -> None:
        """An extremely large port number must raise ValidationError."""
        with pytest.raises(ValidationError):
            ConnectionCreateRequest(**_VALID_BASE, port=999_999)
