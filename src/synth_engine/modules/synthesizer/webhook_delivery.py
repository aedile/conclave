"""Webhook delivery engine for synthesis job lifecycle events (T45.3).

Responsible for:
- SSRF-safe HTTP delivery of webhook payloads to registered callbacks.
- HMAC-SHA256 payload signing.
- Exponential backoff retry (1s, 4s — 3 attempts max).
- Returning a :class:`DeliveryResult` describing the outcome.

This module purposely contains NO FastAPI, SQLModel, or bootstrapper imports.
It is called by ``job_orchestration.py`` via an IoC callback registered by
the bootstrapper at startup.  The session/DB writes for the delivery log are
performed by the bootstrapper layer, not here.

SSRF protection model
---------------------
``validate_callback_url()`` (from ``shared/ssrf``) is called both at:
1. Registration time (in the webhooks router) — rejects bad URLs upfront.
2. Delivery time (here) — DNS-rebinding protection: the host may have changed.

Private IP ranges blocked: see ``shared/ssrf.BLOCKED_NETWORKS``.

Boundary constraints (import-linter enforced):
    - Must NOT import from bootstrapper/.
    - Must NOT import from modules/ingestion/, masking/, privacy/, profiler/.

CONSTITUTION Priority 0: Security — SSRF, no redirect following, key hygiene
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.3 — Implement Webhook Callbacks for Task Completion
Task: P45 review — F4, F5, F6, F11
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from synth_engine.shared.protocols import WebhookRegistrationProtocol
from synth_engine.shared.ssrf import validate_callback_url

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS: int = 3
#: Backoff delays between attempts.  Index i = delay after attempt i+1.
#: Only _MAX_ATTEMPTS - 1 values are needed (no sleep after the final attempt).
_BACKOFF_DELAYS: list[float] = [1.0, 4.0]


# ---------------------------------------------------------------------------
# Delivery result value object
# ---------------------------------------------------------------------------


@dataclass
class DeliveryResult:
    """Outcome of a single webhook delivery execution.

    Attributes:
        status: ``"SUCCESS"`` | ``"FAILED"`` | ``"SKIPPED"``.
        attempt_number: Number of HTTP attempts made (0 for SKIPPED).
        delivery_id: UUID identifying the logical delivery (shared across retries).
        response_code: HTTP status code from the final attempt (``None`` on error).
        error_message: Error detail on failure (``None`` on success).
    """

    status: str
    attempt_number: int = 0
    delivery_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    response_code: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


def _canonicalize_payload(payload: dict[str, Any]) -> str:
    """Produce a canonical JSON string from ``payload``.

    Uses ``json.dumps(sort_keys=True, separators=(',', ':'))`` for
    deterministic output regardless of Python dict insertion order.

    Args:
        payload: Delivery payload dict.

    Returns:
        Compact, sorted JSON string.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_hmac_signature(payload: dict[str, Any], signing_key: str) -> str:
    """Compute the HMAC-SHA256 signature for ``payload`` using ``signing_key``.

    Args:
        payload: Delivery payload dict (will be canonicalized).
        signing_key: HMAC secret string.

    Returns:
        Signature string in format ``"sha256=<hex_digest>"``.
    """
    canonical = _canonicalize_payload(payload)
    digest = hmac.new(
        signing_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Core delivery function
# ---------------------------------------------------------------------------


def deliver_webhook(
    *,
    registration: WebhookRegistrationProtocol,
    job_id: int,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int = 10,
) -> DeliveryResult:
    """Deliver a webhook payload to the registered callback URL.

    Implements at-least-once delivery with 3 attempts and exponential
    backoff (1s, 4s between attempts).  Delivery is skipped for inactive
    registrations.

    SSRF protection: ``validate_callback_url()`` is called before each
    HTTP attempt (DNS-rebinding guard).

    The HTTP request uses ``follow_redirects=False`` to prevent SSRF via
    open redirects.

    Args:
        registration: Webhook registration satisfying
            :class:`~synth_engine.shared.protocols.WebhookRegistrationProtocol`.
        job_id: Integer PK of the synthesis job that triggered the delivery.
        event_type: Event type string (e.g. ``"job.completed"``).
        payload: Dict payload to deliver as JSON.
        timeout_seconds: HTTP timeout per attempt in seconds.

    Returns:
        :class:`DeliveryResult` describing the outcome.
    """
    if not registration.active:
        _logger.info(
            "Webhook registration %s is inactive — skipping delivery for job %d.",
            registration.id,
            job_id,
        )
        return DeliveryResult(status="SKIPPED", attempt_number=0)

    delivery_id = str(uuid.uuid4())
    signature = _compute_hmac_signature(payload, registration.signing_key)
    canonical_body = _canonicalize_payload(payload)

    headers = {
        "Content-Type": "application/json",
        "X-Conclave-Signature": signature,
        "X-Conclave-Event": event_type,
        "X-Conclave-Delivery-Id": delivery_id,
    }

    last_error: str | None = None
    last_status_code: int | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        # DNS-rebinding protection: re-validate before each attempt
        try:
            validate_callback_url(registration.callback_url)
        except ValueError as ssrf_exc:
            _logger.error(
                "SSRF validation failed for registration %s (attempt %d): %s",
                registration.id,
                attempt,
                ssrf_exc,
            )
            return DeliveryResult(
                status="FAILED",
                attempt_number=attempt,
                delivery_id=delivery_id,
                error_message=str(ssrf_exc),
            )

        try:
            response = httpx.post(
                registration.callback_url,
                content=canonical_body.encode("utf-8"),
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
            last_status_code = response.status_code
            response.raise_for_status()
            _logger.info(
                "Webhook delivery SUCCESS: registration=%s job=%d attempt=%d status=%d",
                registration.id,
                job_id,
                attempt,
                response.status_code,
            )
            return DeliveryResult(
                status="SUCCESS",
                attempt_number=attempt,
                delivery_id=delivery_id,
                response_code=last_status_code,
            )
        except Exception as exc:
            last_error = str(exc)
            _logger.warning(
                "Webhook delivery attempt %d failed for registration %s job %d: %s",
                attempt,
                registration.id,
                job_id,
                exc,
            )
            # Backoff before next retry (not after final attempt)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_DELAYS[attempt - 1])

    return DeliveryResult(
        status="FAILED",
        attempt_number=_MAX_ATTEMPTS,
        delivery_id=delivery_id,
        response_code=last_status_code,
        error_message=last_error,
    )
