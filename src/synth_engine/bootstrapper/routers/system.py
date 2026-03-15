"""FastAPI router for system-level licensing endpoints.

Implements:
- GET /license/challenge — generates a hardware-bound challenge payload
  and renders it as a QR code (base64 PNG) for offline activation.
- POST /license/activate — accepts an RS256-signed JWT from the licensing
  server, validates signature and hardware_id binding, and activates
  :class:`~synth_engine.shared.security.licensing.LicenseState`.

Both endpoints are exempt from :class:`SealGateMiddleware` and
:class:`LicenseGateMiddleware` — they must be reachable in any state.

All 403 responses use RFC 7807 Problem Details format.

CONSTITUTION Priority 0: Security
Task: P5-T5.2 — Offline License Activation Protocol
"""

from __future__ import annotations

import base64
import io
import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from synth_engine.bootstrapper.errors import problem_detail
from synth_engine.bootstrapper.schemas.licensing import (
    LicenseActivateRequest,
    LicenseActivateResponse,
    LicenseChallengeResponse,
)
from synth_engine.shared.security.licensing import (
    LicenseError,
    LicenseState,
    _get_active_public_key,
    generate_challenge,
    verify_license_jwt,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/license", tags=["license"])


def _render_qr_code(payload: dict[str, str]) -> str:
    """Render the challenge payload as a base64-encoded PNG QR code.

    Falls back to a base64-encoded JSON string if the ``qrcode`` library
    or Pillow is unavailable (the spec permits a text-only fallback).

    Args:
        payload: The challenge dictionary to encode in the QR code.

    Returns:
        Base64-encoded PNG string, or base64-encoded JSON fallback.
    """
    try:
        import qrcode  # type: ignore[import-untyped]
        from qrcode.image.pil import PilImage  # type: ignore[import-untyped]

        qr: qrcode.QRCode[PilImage] = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(json.dumps(payload, separators=(",", ":")))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        # Fallback: base64-encode the JSON payload as a text token.
        # An operator can decode this with `base64 -d` on the command line.
        _logger.warning(
            "qrcode/Pillow rendering failed; returning text fallback for challenge QR code."
        )
        return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode("ascii")


@router.get("/challenge", response_model=LicenseChallengeResponse)
def get_license_challenge() -> LicenseChallengeResponse:
    """Generate a hardware-bound challenge payload for offline activation.

    The returned ``hardware_id`` uniquely identifies this machine.  The
    operator copies it to an internet-connected device, submits it to the
    central licensing server, and receives a signed JWT in return.

    The ``qr_code`` field contains a base64-encoded PNG of the challenge
    payload rendered as a QR code for convenient visual transfer.  If the
    QR code library is unavailable, a base64-encoded JSON fallback is
    returned instead.

    Returns:
        :class:`LicenseChallengeResponse` with the challenge fields.
    """
    payload = generate_challenge()
    qr_data = _render_qr_code(payload)
    return LicenseChallengeResponse(
        hardware_id=payload["hardware_id"],
        app_version=payload["app_version"],
        timestamp=payload["timestamp"],
        qr_code=qr_data,
    )


@router.post("/activate", response_model=LicenseActivateResponse)
def post_license_activate(body: LicenseActivateRequest) -> LicenseActivateResponse | JSONResponse:
    """Activate the software license using a signed JWT.

    Validates the RS256 signature against the embedded public key (or the
    ``LICENSE_PUBLIC_KEY`` environment variable override), then asserts that
    the JWT's ``hardware_id`` claim matches this machine.  On success,
    :class:`~synth_engine.shared.security.licensing.LicenseState` is
    transitioned to the LICENSED state.

    Args:
        body: JSON body containing the ``token`` field (compact JWT string).

    Returns:
        :class:`LicenseActivateResponse` with ``status="activated"`` on
        success, or an RFC 7807 403 response on any validation failure.
    """
    public_key = _get_active_public_key()
    try:
        claims = verify_license_jwt(body.token, public_key)
    except LicenseError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=problem_detail(
                status=exc.status_code,
                title="License Activation Failed",
                detail=exc.detail,
            ),
        )

    LicenseState.activate(claims)

    return LicenseActivateResponse(
        status="activated",
        licensee=str(claims["licensee"]) if "licensee" in claims else None,
        tier=str(claims["tier"]) if "tier" in claims else None,
    )
