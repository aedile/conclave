"""Pydantic request/response schemas for the license activation endpoints.

All schemas use strict validation to prevent unexpected field injection.

CONSTITUTION Priority 5: Code Quality
Task: P5-T5.2 — Offline License Activation Protocol
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LicenseChallengeResponse(BaseModel):
    """Response body for GET /license/challenge.

    Attributes:
        hardware_id: SHA-256 hex digest of MAC + app seed for this machine.
        app_version: Current application version string.
        timestamp: ISO-8601 UTC timestamp of when the challenge was generated.
        qr_code: Base64-encoded PNG of the challenge QR code, or a plain-text
            fallback if Pillow / qrcode rendering is unavailable.
        alt_text: Accessibility description of the QR code for screen readers
            and other assistive technology (WCAG 2.1 AA).
    """

    hardware_id: str = Field(..., description="SHA-256 hardware identifier for this machine.")
    app_version: str = Field(..., description="Application version string.")
    timestamp: str = Field(..., description="ISO-8601 UTC generation timestamp.")
    qr_code: str = Field(
        ...,
        description=(
            "Base64-encoded PNG QR code of the challenge payload, "
            "or a plain-text fallback if image rendering is unavailable."
        ),
    )
    alt_text: str = Field(
        ...,
        description=("Accessibility description of the QR code content for screen readers."),
    )


class LicenseActivateRequest(BaseModel):
    """Request body for POST /license/activate.

    Attributes:
        token: RS256-signed JWT issued by the central licensing server.
    """

    token: str = Field(..., description="RS256-signed license JWT from the licensing server.")


class LicenseActivateResponse(BaseModel):
    """Response body for POST /license/activate.

    Attributes:
        status: Activation status — ``"activated"`` on success.
        licensee: The ``licensee`` claim extracted from the JWT, if present.
        tier: The ``tier`` claim extracted from the JWT, if present.
    """

    status: str = Field(..., description="Activation status.")
    licensee: str | None = Field(None, description="Licensee name from the JWT claims.")
    tier: str | None = Field(None, description="License tier from the JWT claims.")
