"""Webhook delivery engine for synthesis job lifecycle events (T45.3).

Responsible for:
- SSRF-safe HTTP delivery of webhook payloads to registered callbacks.
- HMAC-SHA256 payload signing.
- Non-blocking retry with 15-second total time budget (T62.2).
- Circuit breaker: trips after N consecutive failures per URL (T62.2).
- Returning a :class:`DeliveryResult` describing the outcome.

This module purposely contains NO FastAPI, SQLModel, or bootstrapper imports.
It is called by ``job_orchestration.py`` via an IoC callback registered by
the bootstrapper at startup.  The session/DB writes for the delivery log are
performed by the bootstrapper layer, not here.

SSRF protection model
---------------------
``validate_callback_url()`` (from ``shared/ssrf``) is called both at:
1. Registration time (in the webhooks router) — rejects bad URLs upfront,
   using ``strict=True`` so DNS failures cause rejection (fail-closed).
2. Delivery time (here) — DNS-rebinding protection via ``validate_delivery_ips``
   (T69.1).  Fail-closed: DNS failures return FAILED so operators are notified.

Private IP ranges blocked: see ``shared/ssrf.BLOCKED_NETWORKS``.

Circuit breaker (T62.2)
-----------------------
A per-URL circuit breaker prevents continued delivery attempts to a failing
endpoint.  After ``webhook_circuit_breaker_threshold`` consecutive failures
to the same URL, the circuit trips and deliveries are skipped for
``webhook_circuit_breaker_cooldown_seconds``.

State: in-memory only.  State is NOT shared across workers or persisted.
This is intentional for the single-worker deployment model.  In a
multi-worker deployment, circuit state would need to be stored in Redis.
This constraint is documented in this module's docstring.

After the cooldown expires, one probe delivery is attempted.  If it
succeeds the circuit resets; if it fails the circuit re-trips.

Total time budget: 15 seconds per delivery chain.  The retry loop checks
``time.monotonic()`` before each attempt.  ``time.sleep()`` is removed
from the retry loop — backoff is enforced by the budget check only.
This prevents Huey worker starvation on retries to slow endpoints.

Prometheus counters:
- ``webhook_circuit_breaker_trips_total`` — incremented when circuit trips.
  Labels: ``{reason: "consecutive_failures"}``.
- ``webhook_deliveries_skipped_total`` — incremented when delivery is skipped
  because the circuit breaker is open. Labels: ``{reason}``.
  No ``registration_id`` label (unbounded cardinality).

Boundary constraints (import-linter enforced):
    - Must NOT import from bootstrapper/.
    - Must NOT import from modules/ingestion/, masking/, privacy/, profiler/.

CONSTITUTION Priority 0: Security — SSRF, no redirect following, key hygiene
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T45.3 — Implement Webhook Callbacks for Task Completion
Task: P45 review — F4, F5, F6, F11
Task: T55.4 — SSRF registration fail-closed on DNS failure
Task: T62.2 — Circuit Breaker for Webhook Delivery
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter

from synth_engine.shared.protocols import WebhookRegistrationProtocol
from synth_engine.shared.ssrf import validate_delivery_ips

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / time-budget constants
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS: int = 3

#: Total time budget for all delivery attempts (including retries) in seconds.
#: If the budget is exhausted before all attempts are made, remaining
#: attempts are aborted without sleep.  This prevents Huey worker starvation.
_DEFAULT_TIME_BUDGET_SECONDS: float = 15.0

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

#: Counter incremented when the circuit breaker trips for a URL.
#: Labels: {reason} — only "consecutive_failures" is used.
#: No registration_id label: unbounded cardinality would overwhelm Prometheus.
_circuit_breaker_trips_total: Counter = Counter(
    "webhook_circuit_breaker_trips_total",
    "Number of times the webhook delivery circuit breaker has tripped.",
    ["reason"],
)

#: Counter incremented when a delivery is skipped because the circuit is open.
#: Labels: {reason} — "circuit_open" is the only value currently used.
#: ADV-P62-04: surfaced in Prometheus dashboards to quantify skipped deliveries
#: without requiring log parsing.
WEBHOOK_DELIVERIES_SKIPPED_TOTAL: Counter = Counter(
    "webhook_deliveries_skipped_total",
    "Webhook deliveries skipped due to open circuit breaker",
    ["reason"],
)

# ---------------------------------------------------------------------------
# URL sanitization helper
# ---------------------------------------------------------------------------


def _sanitize_url_for_log(url: str) -> str:
    """Strip query string and fragment from a URL before writing it to logs.

    Operators may register callback URLs that contain authentication tokens
    in query parameters (e.g. ``?token=abc123``).  Logging the raw URL would
    expose those credentials in operator-accessible log streams.  This helper
    returns only the scheme + authority + path components.

    Args:
        url: Raw callback URL string.

    Returns:
        URL with ``query`` and ``fragment`` components removed.
        Falls back to the original string if ``urlparse`` raises.
    """
    try:
        return urlparse(url)._replace(query="", fragment="").geturl()
    except Exception:  # broad catch intentional: log helper must never raise
        return "<unparseable-url>"


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
# Circuit breaker
# ---------------------------------------------------------------------------


class WebhookCircuitBreaker:
    """Per-URL circuit breaker for webhook delivery.

    Tracks consecutive failures per callback URL.  After ``threshold``
    consecutive failures the circuit "trips" (opens) for ``cooldown_seconds``.
    During cooldown, deliveries to that URL are skipped.

    After cooldown, one probe is allowed.  If it succeeds, the circuit closes
    (resets).  If it fails, the circuit re-trips.

    Thread safety: uses a reentrant lock so multiple Huey task threads can
    share a single circuit breaker instance safely.

    State: in-memory only.  Not shared across workers; not persisted.
    In multi-worker deployments, use Redis-backed state instead.

    Args:
        threshold: Consecutive failures before tripping.
        cooldown_seconds: Seconds to wait after tripping before allowing probe.
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: int = 300) -> None:
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.RLock()
        # _failure_counts: {url -> consecutive_failure_count}
        self._failure_counts: dict[str, int] = {}
        # _trip_times: {url -> monotonic time when circuit was tripped}
        self._trip_times: dict[str, float] = {}

    def is_open(self, url: str) -> bool:
        """Return True if the circuit is open (tripped) for the given URL.

        After the cooldown period expires, the circuit is considered half-open
        (probe allowed) and this returns False.

        Args:
            url: Callback URL to check.

        Returns:
            True if deliveries to ``url`` should be skipped (circuit open).
        """
        with self._lock:
            trip_time = self._trip_times.get(url)
            if trip_time is None:
                return False
            # Check if cooldown has expired
            elapsed = time.monotonic() - trip_time
            if elapsed >= self.cooldown_seconds:
                # Cooldown expired — allow probe attempt (half-open state)
                return False
            return True

    def record_failure(self, url: str) -> None:
        """Record a delivery failure for the given URL.

        If consecutive failures reach ``threshold``, the circuit trips.

        Args:
            url: Callback URL that failed.
        """
        with self._lock:
            self._failure_counts[url] = self._failure_counts.get(url, 0) + 1
            if self._failure_counts[url] >= self.threshold:
                if url not in self._trip_times:
                    # Circuit just tripped — record trip time and emit counter
                    self._trip_times[url] = time.monotonic()
                    _circuit_breaker_trips_total.labels(reason="consecutive_failures").inc()
                    _logger.warning(
                        "Webhook circuit breaker TRIPPED for url=%s "
                        "after %d consecutive failures. "
                        "Cooldown: %ds.",
                        _sanitize_url_for_log(url),
                        self._failure_counts[url],
                        self.cooldown_seconds,
                    )
                else:
                    # Already tripped — reset timer (re-trip after probe failure)
                    self._trip_times[url] = time.monotonic()
                    _circuit_breaker_trips_total.labels(reason="consecutive_failures").inc()
                    _logger.warning(
                        "Webhook circuit breaker RE-TRIPPED for url=%s after probe failure.",
                        _sanitize_url_for_log(url),
                    )

    def record_success(self, url: str) -> None:
        """Record a successful delivery for the given URL.

        Resets the consecutive failure counter and clears any trip state.

        Args:
            url: Callback URL that succeeded.
        """
        with self._lock:
            self._failure_counts.pop(url, None)
            self._trip_times.pop(url, None)

    def _set_trip_time(self, url: str, monotonic_time: float) -> None:
        """Override the trip time for a URL (test helper).

        Allows tests to simulate cooldown expiry by backdating the trip time.

        Args:
            url: Callback URL.
            monotonic_time: The ``time.monotonic()`` value to set as trip time.
        """
        with self._lock:
            if url in self._trip_times:
                self._trip_times[url] = monotonic_time


# ---------------------------------------------------------------------------
# Module-level circuit breaker singleton
# ---------------------------------------------------------------------------

#: Module-level singleton.  Single Huey worker per deployment — in-memory
#: state is sufficient.  See module docstring for multi-worker constraint.
_MODULE_CIRCUIT_BREAKER: WebhookCircuitBreaker | None = None
_CB_LOCK = threading.Lock()


def _get_circuit_breaker() -> WebhookCircuitBreaker:
    """Return the module-level :class:`WebhookCircuitBreaker` singleton.

    Lazily initialised on first call using settings for threshold and cooldown.
    Thread-safe via a module-level lock.

    Returns:
        The singleton circuit breaker instance.
    """
    global _MODULE_CIRCUIT_BREAKER
    with _CB_LOCK:
        if _MODULE_CIRCUIT_BREAKER is None:
            from synth_engine.shared.settings import get_settings  # late import

            s = get_settings()
            _MODULE_CIRCUIT_BREAKER = WebhookCircuitBreaker(
                threshold=s.webhook_circuit_breaker_threshold,
                cooldown_seconds=s.webhook_circuit_breaker_cooldown_seconds,
            )
        return _MODULE_CIRCUIT_BREAKER


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
# Safe error message helper
# ---------------------------------------------------------------------------


def _safe_error_msg(exc: BaseException) -> str:
    """Return a sanitized error description that omits hostnames and paths.

    Raw ``str(exc)`` for network exceptions (e.g. ``httpx.ConnectError``,
    ``ssl.SSLError``) can expose internal hostnames, TLS handshake details,
    or file-system paths.  This helper returns only the exception *type*
    name plus a generic message, preventing operator-visible log scraping.

    Args:
        exc: The exception to describe.

    Returns:
        A sanitized string of the form ``"<ExceptionTypeName>: delivery failed"``.
    """
    return f"{type(exc).__name__}: delivery failed"


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
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
) -> DeliveryResult:
    """Deliver a webhook payload to the registered callback URL.

    Implements at-least-once delivery with up to 3 attempts within a 15-second
    total time budget.  Delivery is skipped for inactive registrations and for
    URLs whose circuit breaker is open.

    No ``time.sleep()`` between retries: the retry loop checks the remaining
    time budget before each attempt.  If the budget is exhausted, the loop
    exits without sleeping, preventing Huey worker starvation.

    SSRF protection: ``validate_delivery_ips()`` is called before each
    HTTP attempt (fail-closed: DNS resolution failure or SSRF violation
    immediately fails the delivery attempt).

    The HTTP request uses ``follow_redirects=False`` to prevent SSRF via
    open redirects.

    Args:
        registration: Webhook registration satisfying
            :class:`~synth_engine.shared.protocols.WebhookRegistrationProtocol`.
        job_id: Integer PK of the synthesis job that triggered the delivery.
        event_type: Event type string (e.g. ``"job.completed"``).
        payload: Dict payload to deliver as JSON.
        timeout_seconds: HTTP timeout per attempt in seconds.
        time_budget_seconds: Total wall-clock budget for all attempts.
            Defaults to 15 seconds.  After the budget is exhausted no further
            attempts are made (no sleep, no block).

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

    # Circuit breaker check — skip delivery if circuit is open for this URL.
    cb = _get_circuit_breaker()
    if cb.is_open(registration.callback_url):
        WEBHOOK_DELIVERIES_SKIPPED_TOTAL.labels(reason="circuit_open").inc()
        _logger.warning(
            "Webhook circuit breaker is OPEN for url=%s — skipping delivery "
            "for registration=%s job=%d.",
            _sanitize_url_for_log(registration.callback_url),
            registration.id,
            job_id,
        )
        return DeliveryResult(
            status="SKIPPED",
            attempt_number=0,
            error_message=(
                f"Circuit breaker open for {_sanitize_url_for_log(registration.callback_url)}. "
                f"Delivery skipped during cooldown period."
            ),
        )

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
    budget_start = time.monotonic()

    # T72.5: Use httpx.Client as a context manager so the connection pool is
    # closed after all retry attempts complete.  This prevents file-descriptor
    # leaks when ConnectError or other network exceptions are raised mid-loop.
    # timeout and follow_redirects are set at the Client level (not per-request)
    # to ensure they apply consistently across all attempts in the retry loop.
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as _client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            # Time budget check: abort if budget is exhausted before this attempt.
            elapsed = time.monotonic() - budget_start
            if elapsed >= time_budget_seconds:
                _logger.warning(
                    "Webhook delivery time budget exhausted (%.1fs of %.1fs used) "
                    "for registration=%s job=%d after %d attempt(s).",
                    elapsed,
                    time_budget_seconds,
                    registration.id,
                    job_id,
                    attempt - 1,
                )
                break

            # DNS-rebinding protection: re-validate before each attempt (T69.1).
            # validate_delivery_ips() is fail-closed: DNS failures return FAILED
            # (not SKIPPED) so operators are notified of delivery failures.
            # This closes the TOCTOU gap where strict=False would silently pass
            # DNS resolution failures through to httpx.
            try:
                parsed_url = urlparse(registration.callback_url)
                delivery_hostname = parsed_url.hostname or ""
                pinned_ips_parsed: list[str] | None = None
                if registration.pinned_ips:
                    import json as _json_ips

                    try:
                        pinned_ips_parsed = _json_ips.loads(registration.pinned_ips)
                    except (ValueError, TypeError):
                        pinned_ips_parsed = None
                validate_delivery_ips(delivery_hostname, pinned_ips=pinned_ips_parsed)
            except ValueError as ssrf_exc:
                _logger.error(
                    "SSRF delivery validation failed for registration %s (attempt %d): %s",
                    registration.id,
                    attempt,
                    ssrf_exc,
                )
                cb.record_failure(registration.callback_url)
                return DeliveryResult(
                    status="FAILED",
                    attempt_number=attempt,
                    delivery_id=delivery_id,
                    error_message=(
                        "SSRF delivery validation failed: "
                        "callback URL resolves to a blocked address."
                    ),
                )

            # Circuit check again mid-loop: if a previous attempt just tripped the circuit, abort.
            if cb.is_open(registration.callback_url):
                _logger.warning(
                    "Circuit breaker tripped mid-loop for registration=%s — aborting.",
                    registration.id,
                )
                return DeliveryResult(
                    status="FAILED",
                    attempt_number=attempt - 1,
                    delivery_id=delivery_id,
                    error_message="Circuit breaker tripped during delivery.",
                )

            try:
                response = _client.post(
                    registration.callback_url,
                    content=canonical_body.encode("utf-8"),
                    headers=headers,
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
                cb.record_success(registration.callback_url)
                return DeliveryResult(
                    status="SUCCESS",
                    attempt_number=attempt,
                    delivery_id=delivery_id,
                    response_code=last_status_code,
                )
            except Exception as exc:
                # Broad catch intentional: httpx/network retry loop must absorb any
                # transport-level failure (ConnectError, ReadTimeout, HTTPStatusError,
                # SSLError, etc.) and attempt retry within the time budget.
                last_error = _safe_error_msg(exc)
                _logger.warning(
                    "Webhook delivery attempt %d failed for registration %s job %d: %s",
                    attempt,
                    registration.id,
                    job_id,
                    type(exc).__name__,
                )
                cb.record_failure(registration.callback_url)

                # Check if circuit just tripped — abort remaining attempts immediately.
                if cb.is_open(registration.callback_url):
                    _logger.warning(
                        "Circuit breaker tripped after attempt %d for registration=%s — aborting.",
                        attempt,
                        registration.id,
                    )
                    return DeliveryResult(
                        status="FAILED",
                        attempt_number=attempt,
                        delivery_id=delivery_id,
                        response_code=last_status_code,
                        error_message=last_error,
                    )

                # No time.sleep() — check budget before next attempt instead.
                # This prevents Huey worker starvation on retries.

    return DeliveryResult(
        status="FAILED",
        attempt_number=_MAX_ATTEMPTS,
        delivery_id=delivery_id,
        response_code=last_status_code,
        error_message=last_error,
    )
