"""Webhook delivery engine for synthesis job lifecycle events (T45.3).

Responsible for:
- SSRF-safe HTTP delivery of webhook payloads to registered callbacks.
- HMAC-SHA256 payload signing.
- Non-blocking retry with 15-second total time budget (T62.2).
- Circuit breaker: trips after N consecutive failures per URL (T62.2).
- Redis-backed circuit breaker for multi-worker deployments (T75.1).
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

Circuit breaker (T62.2, T75.1)
--------------------------------
A per-URL circuit breaker prevents continued delivery attempts to a failing
endpoint.  After ``webhook_circuit_breaker_threshold`` consecutive failures
to the same URL, the circuit trips and deliveries are skipped for
``webhook_circuit_breaker_cooldown_seconds``.

Redis-backed circuit breaker (T75.1)
--------------------------------------
When Redis is available at startup, :class:`RedisCircuitBreaker` is used
instead of the process-local :class:`WebhookCircuitBreaker`.  The Redis
implementation uses the key prefix ``conclave:cb:`` to avoid collisions
with ``ratelimit:`` and ``huey.*`` keys.  All Redis keys carry TTL equal
to ``cooldown_seconds`` so keys cannot become permanent on worker crash.

Half-open probe: ``SET conclave:cb:{url_hash}:probe NX EX {cooldown}``
ensures only one worker among N fires the probe attempt.

Fallback: if Redis is unavailable at first ``_get_circuit_breaker()`` call,
a process-local :class:`WebhookCircuitBreaker` singleton is stored and reused
for the process lifetime — no per-call Redis re-attempts.

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
# Redis key prefix constants (T75.1)
# ---------------------------------------------------------------------------

#: Key prefix for all circuit breaker Redis keys.
#: Scoped to avoid collision with ``ratelimit:`` and ``huey.*`` namespaces.
_CB_KEY_PREFIX: str = "conclave:cb:"

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
# URL hash helper for Redis key scoping (T75.1)
# ---------------------------------------------------------------------------


def _url_hash(url: str) -> str:
    """Return a short SHA-256 hex digest of ``url`` for use as a Redis key component.

    Hashing the URL avoids embedding arbitrary user-controlled strings directly
    into Redis key names (defense-in-depth against key-injection via crafted
    callback URLs).

    Args:
        url: The callback URL to hash.

    Returns:
        First 16 hex characters of the SHA-256 digest of ``url``.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


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
# Circuit breaker — process-local (original implementation)
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
    In multi-worker deployments, use :class:`RedisCircuitBreaker` instead.

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
# Circuit breaker — Redis-backed (T75.1, multi-worker safe)
# ---------------------------------------------------------------------------


class RedisCircuitBreaker:
    """Redis-backed per-URL circuit breaker for multi-worker webhook delivery.

    Stores failure counts and trip timestamps in Redis so that N uvicorn
    workers share a single circuit state.  All Redis keys use the prefix
    ``conclave:cb:`` to avoid collision with ``ratelimit:`` and ``huey.*``
    namespaces.  Every key carries a TTL equal to ``cooldown_seconds`` so
    stale keys cannot become permanent on worker crash.

    Half-open probe coordination
    ----------------------------
    When the circuit is in half-open state (cooldown expired), only ONE
    worker may fire the probe.  This is coordinated atomically via::

        SET conclave:cb:{url_hash}:probe NX EX {cooldown_seconds}

    The worker that successfully sets the probe key fires the probe.
    All others see ``is_open() → True`` (probe key exists, SET NX fails).

    Graceful degradation
    --------------------
    All Redis operations are wrapped in ``try/except redis.RedisError`` (and
    also catch ``ValueError`` for corrupt non-integer INCR responses).  On any
    Redis failure, the method logs a warning and performs the safe fallback
    action (treat as open / treat as not tripped).

    Args:
        redis_client: Sync ``redis.Redis`` client (the shared singleton from
            ``bootstrapper/dependencies/redis.py``).
        threshold: Consecutive failures before tripping. Default: 3.
        cooldown_seconds: Seconds to remain open after tripping. Default: 300.
    """

    def __init__(
        self,
        redis_client: Any,  # redis.Redis — imported lazily to avoid circular imports
        threshold: int = 3,
        cooldown_seconds: int = 300,
    ) -> None:
        self._redis = redis_client
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _failures_key(self, url: str) -> str:
        """Return the Redis key for the consecutive failure counter.

        Args:
            url: Callback URL.

        Returns:
            Redis key string with ``conclave:cb:`` prefix.
        """
        return f"{_CB_KEY_PREFIX}{_url_hash(url)}:failures"

    def _tripped_at_key(self, url: str) -> str:
        """Return the Redis key for the circuit trip timestamp.

        Args:
            url: Callback URL.

        Returns:
            Redis key string with ``conclave:cb:`` prefix.
        """
        return f"{_CB_KEY_PREFIX}{_url_hash(url)}:tripped_at"

    def _probe_key(self, url: str) -> str:
        """Return the Redis key for the half-open probe coordination lock.

        Args:
            url: Callback URL.

        Returns:
            Redis key string with ``conclave:cb:`` prefix.
        """
        return f"{_CB_KEY_PREFIX}{_url_hash(url)}:probe"

    # ------------------------------------------------------------------
    # Public interface (same as WebhookCircuitBreaker)
    # ------------------------------------------------------------------

    def is_open(self, url: str) -> bool:
        """Return True if the circuit is open for the given URL.

        Reads the ``tripped_at`` key from Redis.  If the key exists and the
        trip time is within the cooldown window, the circuit is open.

        When the cooldown has expired (half-open state), attempts an atomic
        ``SET NX EX`` to claim the probe slot.  The worker that claims the
        slot returns ``False`` (allowed to probe).  All others return ``True``
        (still blocked until the probe result is known).

        On any Redis error, returns ``False`` (fail-open for is_open to avoid
        permanently blocking delivery when Redis is unstable).

        Args:
            url: Callback URL to check.

        Returns:
            True if deliveries to ``url`` should be skipped.
        """
        import redis as redis_lib

        try:
            raw = self._redis.get(self._tripped_at_key(url))
            if raw is None:
                return False
            tripped_at = float(raw)
            elapsed = time.time() - tripped_at
            if elapsed < self.cooldown_seconds:
                # Circuit is open (within cooldown window)
                return True
            # Half-open: try to claim the probe slot atomically (SET NX EX)
            # Only one worker among N will succeed; others stay blocked
            probe_claimed = self._redis.set(
                self._probe_key(url),
                "1",
                nx=True,
                ex=self.cooldown_seconds,
            )
            if probe_claimed:
                # This worker won the probe slot — allow it to proceed
                return False
            # Another worker already has the probe slot — stay blocked
            return True
        except (redis_lib.RedisError, ValueError, TypeError) as exc:
            _logger.warning(
                "RedisCircuitBreaker.is_open() Redis error for url_hash=%s: %s — "
                "treating circuit as closed (fail-open for is_open).",
                _url_hash(url),
                type(exc).__name__,
            )
            return False

    def record_failure(self, url: str) -> None:
        """Record a delivery failure for the given URL.

        Increments the failure counter in Redis (INCR + EXPIRE).  If the
        counter reaches ``threshold``, writes the trip timestamp and emits
        the Prometheus counter.

        On any Redis error (including corrupt non-integer INCR response),
        logs a warning and returns without propagating.

        Args:
            url: Callback URL that failed.
        """
        import redis as redis_lib

        try:
            failures_key = self._failures_key(url)
            raw_count = self._redis.incr(failures_key)
            # Guard against corrupt values (e.g. bytes that aren't a valid int)
            try:
                count = int(raw_count)
            except (ValueError, TypeError) as corrupt_exc:
                _logger.warning(
                    "RedisCircuitBreaker.record_failure(): corrupt INCR value %r for "
                    "url_hash=%s: %s — treating as RedisError fallback.",
                    raw_count,
                    _url_hash(url),
                    corrupt_exc,
                )
                return
            self._redis.expire(failures_key, self.cooldown_seconds)

            if count >= self.threshold:
                tripped_at_key = self._tripped_at_key(url)
                already_tripped = self._redis.get(tripped_at_key) is not None
                self._redis.set(tripped_at_key, str(time.time()), ex=self.cooldown_seconds)
                _circuit_breaker_trips_total.labels(reason="consecutive_failures").inc()
                if already_tripped:
                    _logger.warning(
                        "Webhook circuit breaker RE-TRIPPED (Redis) for url=%s "
                        "after probe failure.",
                        _sanitize_url_for_log(url),
                    )
                else:
                    _logger.warning(
                        "Webhook circuit breaker TRIPPED (Redis) for url=%s "
                        "after %d consecutive failures. Cooldown: %ds.",
                        _sanitize_url_for_log(url),
                        count,
                        self.cooldown_seconds,
                    )
        except redis_lib.RedisError as exc:
            _logger.warning(
                "RedisCircuitBreaker.record_failure() Redis error for url_hash=%s: %s — "
                "circuit breaker state not updated.",
                _url_hash(url),
                type(exc).__name__,
            )

    def record_success(self, url: str) -> None:
        """Record a successful delivery for the given URL.

        Deletes the failure counter, trip timestamp, and probe keys from Redis
        to reset the circuit to a clean closed state.

        On any Redis error, logs a warning and returns without propagating.

        Args:
            url: Callback URL that succeeded.
        """
        import redis as redis_lib

        try:
            self._redis.delete(
                self._failures_key(url),
                self._tripped_at_key(url),
                self._probe_key(url),
            )
        except redis_lib.RedisError as exc:
            _logger.warning(
                "RedisCircuitBreaker.record_success() Redis error for url_hash=%s: %s — "
                "circuit breaker state not cleared.",
                _url_hash(url),
                type(exc).__name__,
            )


# ---------------------------------------------------------------------------
# Module-level circuit breaker singleton
# ---------------------------------------------------------------------------

#: Module-level singleton.  May be a :class:`RedisCircuitBreaker` (when Redis
#: is available) or a :class:`WebhookCircuitBreaker` (process-local fallback).
#: Type annotation uses the base class to allow both; callers use duck typing.
_MODULE_CIRCUIT_BREAKER: WebhookCircuitBreaker | RedisCircuitBreaker | None = None
_CB_LOCK = threading.Lock()

#: Injected Redis client for the circuit breaker.  Set by the bootstrapper via
#: :func:`set_circuit_breaker_redis_client` during startup wiring (T75.1).
#: ``None`` means no Redis client has been injected; process-local fallback
#: :class:`WebhookCircuitBreaker` will be used.
_CB_REDIS_CLIENT: Any | None = None


def set_circuit_breaker_redis_client(client: Any) -> None:
    """Inject the Redis client for the circuit breaker (T75.1).

    Called by the bootstrapper during startup wiring so that the circuit
    breaker can use Redis for shared state across workers.  This module
    MUST NOT import from ``bootstrapper/`` directly (import-linter contract).
    The bootstrapper injects the client here at startup, following the same
    IoC pattern used for ``set_webhook_delivery_fn`` and
    ``set_dp_wrapper_factory``.

    Args:
        client: A ``redis.Redis`` instance (typed as ``Any`` because
            this module must not import redis directly from bootstrapper/).
            Pass ``None`` to clear the injected client (test teardown).
    """
    global _CB_REDIS_CLIENT, _MODULE_CIRCUIT_BREAKER
    with _CB_LOCK:
        _CB_REDIS_CLIENT = client
        # Reset the singleton so the next call to _get_circuit_breaker()
        # picks up the newly injected client.
        _MODULE_CIRCUIT_BREAKER = None


def _get_circuit_breaker() -> WebhookCircuitBreaker | RedisCircuitBreaker:
    """Return the module-level circuit breaker singleton.

    Lazily initialised on first call using settings for threshold and cooldown.
    Thread-safe via a module-level lock.

    Uses the Redis client injected by :func:`set_circuit_breaker_redis_client`
    (called by the bootstrapper at startup) to create a
    :class:`RedisCircuitBreaker`.  If no Redis client has been injected, or if
    the client is unavailable, falls back to a process-local
    :class:`WebhookCircuitBreaker`.

    This function MUST NOT import from ``bootstrapper/`` (import-linter
    contract: modules/synthesizer → bootstrapper is forbidden).

    Returns:
        The singleton circuit breaker instance (Redis-backed if Redis client
        was injected; process-local otherwise).
    """
    global _MODULE_CIRCUIT_BREAKER
    with _CB_LOCK:
        if _MODULE_CIRCUIT_BREAKER is None:
            from synth_engine.shared.settings import get_settings  # late import

            s = get_settings()
            threshold = s.webhook_circuit_breaker_threshold
            cooldown = s.webhook_circuit_breaker_cooldown_seconds

            if _CB_REDIS_CLIENT is not None:
                try:
                    _MODULE_CIRCUIT_BREAKER = RedisCircuitBreaker(
                        redis_client=_CB_REDIS_CLIENT,
                        threshold=threshold,
                        cooldown_seconds=cooldown,
                    )
                    _logger.debug(
                        "Circuit breaker: using Redis-backed RedisCircuitBreaker "
                        "(threshold=%d, cooldown=%ds).",
                        threshold,
                        cooldown,
                    )
                except Exception as exc:
                    # Redis unavailable at startup — fall back to process-local CB.
                    _logger.warning(
                        "Circuit breaker: Redis unavailable at startup (%s: %s). "
                        "Falling back to process-local WebhookCircuitBreaker. "
                        "Circuit state will NOT be shared across workers.",
                        type(exc).__name__,
                        exc,
                    )
                    _MODULE_CIRCUIT_BREAKER = WebhookCircuitBreaker(
                        threshold=threshold,
                        cooldown_seconds=cooldown,
                    )
            else:
                _logger.debug(
                    "Circuit breaker: no Redis client injected — "
                    "using process-local WebhookCircuitBreaker."
                )
                _MODULE_CIRCUIT_BREAKER = WebhookCircuitBreaker(
                    threshold=threshold,
                    cooldown_seconds=cooldown,
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
# Core delivery helpers
# ---------------------------------------------------------------------------


def _check_skip_conditions(
    registration: WebhookRegistrationProtocol,
    cb: WebhookCircuitBreaker | RedisCircuitBreaker,
    job_id: int,
) -> DeliveryResult | None:
    """Return a SKIPPED DeliveryResult if delivery should be skipped, else None.

    Checks two early-exit conditions in order:
    1. Registration is inactive.
    2. Circuit breaker is open for the callback URL.

    Args:
        registration: The webhook registration to check.
        cb: The circuit breaker singleton (process-local or Redis-backed).
        job_id: Synthesis job integer PK (for logging).

    Returns:
        A SKIPPED DeliveryResult if delivery should be aborted, else None.
    """
    if not registration.active:
        _logger.info(
            "Webhook registration %s is inactive — skipping delivery for job %d.",
            registration.id,
            job_id,
        )
        return DeliveryResult(status="SKIPPED", attempt_number=0)

    if cb.is_open(registration.callback_url):
        WEBHOOK_DELIVERIES_SKIPPED_TOTAL.labels(reason="circuit_open").inc()
        safe_url = _sanitize_url_for_log(registration.callback_url)
        _logger.warning(
            "Webhook circuit breaker is OPEN for url=%s — skipping delivery "
            "for registration=%s job=%d.",
            safe_url,
            registration.id,
            job_id,
        )
        return DeliveryResult(
            status="SKIPPED",
            attempt_number=0,
            error_message=(
                f"Circuit breaker open for {safe_url}. Delivery skipped during cooldown period."
            ),
        )
    return None


def _validate_ssrf_for_attempt(
    cb: object,
    registration: WebhookRegistrationProtocol,
    attempt: int,
    delivery_id: str,
) -> DeliveryResult | None:
    """Validate SSRF rules before each delivery attempt (T69.1 DNS-rebinding protection).

    Re-validates before every attempt to close the TOCTOU gap where the URL
    resolved safely at registration time but DNS rebinds to a private IP.
    Fail-closed: DNS failures return FAILED so operators are notified.

    Args:
        cb: The circuit breaker instance.
        registration: The webhook registration.
        attempt: Current attempt number (1-based, for DeliveryResult).
        delivery_id: Delivery UUID (for DeliveryResult).

    Returns:
        A FAILED DeliveryResult if SSRF validation fails or if the circuit
        breaker is open mid-loop; None if safe to proceed.
    """
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
        cb.record_failure(registration.callback_url)  # type: ignore[attr-defined]
        return DeliveryResult(
            status="FAILED",
            attempt_number=attempt,
            delivery_id=delivery_id,
            error_message=(
                "SSRF delivery validation failed: callback URL resolves to a blocked address."
            ),
        )

    # Mid-loop circuit check: a previous attempt may have just tripped the circuit.
    if cb.is_open(registration.callback_url):  # type: ignore[attr-defined]
        _logger.warning(
            "Circuit breaker tripped mid-loop for registration=%s — aborting.", registration.id
        )
        return DeliveryResult(
            status="FAILED",
            attempt_number=attempt - 1,
            delivery_id=delivery_id,
            error_message="Circuit breaker tripped during delivery.",
        )
    return None


def _attempt_http_post(
    client: object,
    cb: object,
    registration: WebhookRegistrationProtocol,
    canonical_body: str,
    headers: dict[str, str],
    job_id: int,
    attempt: int,
    delivery_id: str,
    last_status_code_ref: list[int | None],
    last_error_ref: list[str | None],
) -> DeliveryResult | None:
    """Perform one HTTP POST attempt and record success/failure on the circuit breaker.

    Updates ``last_status_code_ref[0]`` and ``last_error_ref[0]`` in-place
    on failure so the caller has context for the final FAILED result.

    Args:
        client: The httpx.Client instance.
        cb: The circuit breaker instance.
        registration: The webhook registration.
        canonical_body: Pre-serialized JSON body string.
        headers: Delivery headers dict.
        job_id: Synthesis job integer PK (for logging).
        attempt: Current attempt number (1-based).
        delivery_id: Delivery UUID.
        last_status_code_ref: Single-element list; updated on each call.
        last_error_ref: Single-element list; updated on failure.

    Returns:
        A SUCCESS or FAILED DeliveryResult when the attempt is conclusive;
        None when the attempt failed but the retry loop should continue.
    """
    try:
        response = client.post(  # type: ignore[attr-defined]
            registration.callback_url,
            content=canonical_body.encode("utf-8"),
            headers=headers,
        )
        last_status_code_ref[0] = response.status_code
        response.raise_for_status()
        _logger.info(
            "Webhook delivery SUCCESS: registration=%s job=%d attempt=%d status=%d",
            registration.id,
            job_id,
            attempt,
            response.status_code,
        )
        cb.record_success(registration.callback_url)  # type: ignore[attr-defined]
        return DeliveryResult(
            status="SUCCESS",
            attempt_number=attempt,
            delivery_id=delivery_id,
            response_code=last_status_code_ref[0],
        )
    except Exception as exc:
        # Broad catch intentional: retry loop must absorb any transport-level failure
        # (ConnectError, ReadTimeout, HTTPStatusError, SSLError, etc.).
        last_error_ref[0] = _safe_error_msg(exc)
        _logger.warning(
            "Webhook delivery attempt %d failed for registration %s job %d: %s",
            attempt,
            registration.id,
            job_id,
            type(exc).__name__,
        )
        cb.record_failure(registration.callback_url)  # type: ignore[attr-defined]
        if cb.is_open(registration.callback_url):  # type: ignore[attr-defined]
            _logger.warning(
                "Circuit breaker tripped after attempt %d for registration=%s — aborting.",
                attempt,
                registration.id,
            )
            return DeliveryResult(
                status="FAILED",
                attempt_number=attempt,
                delivery_id=delivery_id,
                response_code=last_status_code_ref[0],
                error_message=last_error_ref[0],
            )
        return None  # retry


def _execute_retry_loop(
    *,
    cb: WebhookCircuitBreaker | RedisCircuitBreaker,
    registration: WebhookRegistrationProtocol,
    canonical_body: str,
    headers: dict[str, str],
    job_id: int,
    delivery_id: str,
    timeout_seconds: int,
    time_budget_seconds: float,
) -> DeliveryResult:
    """Execute the at-most-3 retry loop within the httpx.Client context.

    Args:
        cb: Circuit breaker instance (process-local or Redis-backed).
        registration: Webhook registration.
        canonical_body: Pre-serialized JSON body string.
        headers: HTTP headers for each request.
        job_id: Synthesis job PK (for logging).
        delivery_id: UUID string for correlation.
        timeout_seconds: Per-attempt HTTP timeout.
        time_budget_seconds: Total wall-clock budget in seconds.

    Returns:
        DeliveryResult (SUCCESS, FAILED, or SKIPPED from mid-loop checks).
    """
    last_status_code_ref: list[int | None] = [None]
    last_error_ref: list[str | None] = [None]
    budget_start = time.monotonic()

    # T72.5: httpx.Client as a context manager — closes connection pool after all retries.
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as _client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
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
            ssrf_result = _validate_ssrf_for_attempt(cb, registration, attempt, delivery_id)
            if ssrf_result is not None:
                return ssrf_result
            result = _attempt_http_post(
                _client,
                cb,
                registration,
                canonical_body,
                headers,
                job_id,
                attempt,
                delivery_id,
                last_status_code_ref,
                last_error_ref,
            )
            if result is not None:
                return result

    return DeliveryResult(
        status="FAILED",
        attempt_number=_MAX_ATTEMPTS,
        delivery_id=delivery_id,
        response_code=last_status_code_ref[0],
        error_message=last_error_ref[0],
    )


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

    At-least-once delivery with up to 3 attempts within a 15-second total time
    budget.  Skips inactive registrations and open circuit breakers.
    SSRF-validates before each attempt (T69.1).  No time.sleep() — budget only.

    Args:
        registration: Webhook registration (WebhookRegistrationProtocol).
        job_id: Integer PK of the synthesis job.
        event_type: Event type string (e.g. ``"job.completed"``).
        payload: Dict payload to deliver as JSON.
        timeout_seconds: HTTP timeout per attempt in seconds.
        time_budget_seconds: Total wall-clock budget. Defaults to 15 seconds.

    Returns:
        DeliveryResult describing the outcome (SUCCESS, FAILED, or SKIPPED).
    """  # noqa: DOC503
    cb = _get_circuit_breaker()
    skip = _check_skip_conditions(registration, cb, job_id)
    if skip is not None:
        return skip

    delivery_id = str(uuid.uuid4())
    signature = _compute_hmac_signature(payload, registration.signing_key)
    canonical_body = _canonicalize_payload(payload)
    headers = {
        "Content-Type": "application/json",
        "X-Conclave-Signature": signature,
        "X-Conclave-Event": event_type,
        "X-Conclave-Delivery-Id": delivery_id,
    }
    return _execute_retry_loop(
        cb=cb,
        registration=registration,
        canonical_body=canonical_body,
        headers=headers,
        job_id=job_id,
        delivery_id=delivery_id,
        timeout_seconds=timeout_seconds,
        time_budget_seconds=time_budget_seconds,
    )
