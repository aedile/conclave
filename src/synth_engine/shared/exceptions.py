"""Shared exception hierarchy for the Conclave Synthetic Data Engine.

All domain exceptions inherit from :exc:`SynthEngineError` so that callers
can catch the entire family with a single ``except SynthEngineError`` clause,
or narrow to a specific subclass when precise handling is required.

Placing the hierarchy here in ``shared/`` solves the ADR-0033 cross-boundary
detection problem: ``modules/synthesizer`` previously had to detect
``BudgetExhaustionError`` (defined in ``modules/privacy``) via
string-matching on the class name because a direct import would violate the
import-linter ``independence`` contract.  Now both modules import from
``shared/``, which is accessible to all modules.

Exception taxonomy
------------------
- :exc:`SynthEngineError` — base for all engine exceptions
  - :exc:`AuditWriteError` — WORM audit trail write failed after budget deduction
  - :exc:`BudgetExhaustionError` — epsilon budget exceeded (DP accounting)
  - :exc:`EpsilonMeasurementError` — privacy cost of a training run could not be measured
  - :exc:`CollisionError` — masking registry collision guard
  - :exc:`CycleDetectionError` — circular FK dependency in schema graph
  - :exc:`OOMGuardrailError` — training job rejected by memory pre-flight
  - :exc:`PrivilegeEscalationError` — ingestion user has write privileges
  - :exc:`ArtifactTamperingError` — HMAC verification failure on a model artifact
  - :exc:`VaultSealedError` — sensitive operation attempted on a sealed vault
  - :exc:`VaultEmptyPassphraseError` — unseal passphrase is empty
  - :exc:`VaultAlreadyUnsealedError` — unseal attempted on an already-unsealed vault
  - :exc:`VaultConfigError` — VAULT_SEAL_SALT missing or too short
  - :exc:`LicenseError` — license validation failed
  - :exc:`TLSCertificateError` — TLS certificate failed a security or validity check
  - :exc:`DatasetTooLargeError` — Parquet file or row count exceeds configured memory bound

HTTP-safety classification
--------------------------
Exceptions are classified as HTTP-safe or logged-only:

- **HTTP-safe** (safe to include sanitized message in 4xx/5xx response body):
  :exc:`AuditWriteError`, :exc:`BudgetExhaustionError`, :exc:`EpsilonMeasurementError`,
  :exc:`CollisionError`, :exc:`CycleDetectionError`, :exc:`OOMGuardrailError`,
  :exc:`VaultSealedError`, :exc:`VaultEmptyPassphraseError`,
  :exc:`VaultConfigError`, :exc:`VaultAlreadyUnsealedError`,
  :exc:`LicenseError`, :exc:`TLSCertificateError`, :exc:`DatasetTooLargeError`

- **Logged-only** (must NOT appear in HTTP response body — log only):
  :exc:`PrivilegeEscalationError`, :exc:`ArtifactTamperingError`

  These carry security-sensitive context (credential hints, internal paths).
  They must be sanitized via :func:`synth_engine.shared.errors.safe_error_msg`
  before any HTTP exposure and the original message must only go to the audit
  log.

Boundary constraints (import-linter enforced)
---------------------------------------------
- This file lives in ``shared/`` and MUST NOT import from ``modules/`` or
  ``bootstrapper/``.
- All modules MUST import exception classes from here (or re-export from here)
  rather than defining their own root exception classes.

Task: P26-T26.2 — Exception Hierarchy + Error Sanitization + Type Tightening
Task: T34.1 — Unify Vault Exceptions Under SynthEngineError
Task: T34.2 — Consolidate module-local exceptions into shared hierarchy
Task: P36 review — Add CycleDetectionError and CollisionError to shared hierarchy (ADR-0037)
Task: T37.1 — Add EpsilonMeasurementError; update OPERATOR_ERROR_MAP mapping
Task: T38.1 — Add AuditWriteError; fail job on WORM audit write failure after budget deduction
Task: T46.1 — Add TLSCertificateError; move from shared/tls/config.py (ARCH-F1)
Task: T47.7 — Add DatasetTooLargeError; enforce Parquet memory bounds
Task: T47.9 — BudgetExhaustionError: scrub epsilon from message; add structured attributes
"""

from __future__ import annotations

from decimal import Decimal

__all__ = [
    "ArtifactTamperingError",
    "AuditWriteError",
    "BudgetExhaustionError",
    "CollisionError",
    "CycleDetectionError",
    "DatasetTooLargeError",
    "EpsilonMeasurementError",
    "LicenseError",
    "OOMGuardrailError",
    "PrivilegeEscalationError",
    "SynthEngineError",
    "TLSCertificateError",
    "VaultAlreadyUnsealedError",
    "VaultConfigError",
    "VaultEmptyPassphraseError",
    "VaultSealedError",
]


class SynthEngineError(Exception):
    """Base exception for all Conclave Synthetic Data Engine errors.

    All domain-specific exceptions in this engine inherit from this class.
    Catching ``SynthEngineError`` catches any intentional engine error;
    unexpected errors (e.g. ``TypeError``, ``IOError``) remain unaffected.

    Example::

        try:
            run_synthesis_job(job_id)
        except SynthEngineError as exc:
            logger.error("Engine error: %s", exc)
    """


class AuditWriteError(SynthEngineError):
    """Raised when the WORM audit trail write fails after a privacy budget deduction.

    Constitution Priority 0 (Security): every privacy budget spend MUST have an
    immutable WORM audit entry.  If the audit infrastructure is broken, the job
    output must NOT be delivered — the operator must reconcile the spend manually.

    The budget has already been deducted (irreversible) when this error is raised,
    so the FAILED job status alerts operators that reconciliation is required.

    HTTP-safe: yes — the message is safe for HTTP 500 response bodies.
    The bootstrapper maps this to HTTP 500 Internal Server Error.

    Example::

        raise AuditWriteError(
            "Budget deducted but audit trail write failed — manual reconciliation required"
        ) from original_exc
    """


class BudgetExhaustionError(SynthEngineError):
    """Raised when cumulative Epsilon spend reaches or exceeds the allocated budget.

    Replaces the ADR-0033 duck-typing pattern
    ``"BudgetExhaustion" in type(exc).__name__``.

    Now that this class lives in ``shared/``, both ``modules/privacy``
    (which raises it) and ``modules/synthesizer`` (which catches it) can
    import it directly without violating the import-linter independence
    contract.

    HTTP-safe: yes — the ``str()`` of this exception is a generic message safe
    for operator-facing HTTP responses.  Epsilon values are deliberately excluded
    from the message to prevent privacy budget state from leaking into API
    responses or log aggregation systems (T47.9 — security scrubbing).

    The detailed epsilon context is stored as structured attributes on the
    exception instance for use by internal audit logging code only.  Callers
    that need the epsilon values must read the attributes, not parse the message.

    Attributes:
        requested_epsilon: The epsilon amount that was requested but could
            not be satisfied.
        total_spent: The total epsilon already spent on this ledger at the
            time the error was raised.
        total_allocated: The total epsilon allocation ceiling for this ledger.
        remaining_epsilon: Convenience attribute — ``total_allocated - total_spent``,
            i.e. how much epsilon remained before the exhaustion was triggered.

    Args:
        requested_epsilon: The epsilon amount that triggered exhaustion.
        total_spent: The total epsilon spent on this ledger.
        total_allocated: The total epsilon allocation for this ledger.

    Example::

        raise BudgetExhaustionError(
            requested_epsilon=decimal_amount,
            total_spent=ledger.total_spent_epsilon,
            total_allocated=ledger.total_allocated_epsilon,
        )
    """

    #: Generic safe message — contains no epsilon values.
    _GENERIC_MESSAGE: str = "Differential privacy budget exhausted. Synthesis job cannot proceed."

    def __init__(
        self,
        requested_epsilon: Decimal,
        total_spent: Decimal,
        total_allocated: Decimal,
    ) -> None:
        super().__init__(self._GENERIC_MESSAGE)
        self.requested_epsilon: Decimal = requested_epsilon
        self.total_spent: Decimal = total_spent
        self.total_allocated: Decimal = total_allocated
        self.remaining_epsilon: Decimal = total_allocated - total_spent


class EpsilonMeasurementError(SynthEngineError):
    """Raised when dp_wrapper.epsilon_spent() cannot produce a value.

    If the DP engine cannot measure the privacy cost of a training run,
    the synthesis job must be marked FAILED — delivering output without a
    verified epsilon bound would violate Constitution Priority 0 (security).

    HTTP-safe: yes — the message is safe for HTTP 500/422 response bodies.

    Example::

        raise EpsilonMeasurementError(
            "DP epsilon measurement failed — privacy budget cannot be verified"
        ) from original_exc
    """


class CollisionError(SynthEngineError):
    """Raised when the masking registry collision prevention encounters an unexpected state.

    Under the current two-phase masking strategy (retry then suffix) this
    should never be raised in production.  It is kept as a defensive guard
    against implementation bugs.

    Moved from ``modules/masking/registry.py`` to ``shared/`` in P36 review
    so that the bootstrapper error-mapping layer can import it via
    ``synth_engine.shared.exceptions`` rather than crossing into a module
    internal (ADR-0037).

    HTTP-safe: yes — the message contains no security-sensitive context.
    The bootstrapper maps this to HTTP 409 Conflict.
    """


class CycleDetectionError(SynthEngineError):
    """Raised when a circular dependency is detected in the schema graph.

    The ``cycle`` attribute holds the sequence of table names forming the
    detected cycle, ordered so that ``cycle[i]`` has an edge to ``cycle[i+1]``
    and the last node has an edge back to a node earlier in the sequence.

    Moved from ``modules/mapping/graph.py`` to ``shared/`` in P36 review
    so that the bootstrapper error-mapping layer can import it via
    ``synth_engine.shared.exceptions`` rather than crossing into a module
    internal (ADR-0037).

    HTTP-safe: yes — the cycle path contains only table names, which are safe
    for operator consumption.  The bootstrapper maps this to HTTP 422.

    Args:
        cycle: Ordered list of table names that form the cycle.
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle: list[str] = cycle
        cycle_repr = " -> ".join(cycle)
        super().__init__(
            f"Circular dependency detected in schema graph: {cycle_repr}. "
            "Provide explicit cycle-breaking rules before ingestion can proceed."
        )


class OOMGuardrailError(SynthEngineError):
    """Raised when a training job's estimated memory exceeds available memory.

    Produced by
    :func:`synth_engine.modules.synthesizer.training.guardrails.check_memory_feasibility`
    before training begins.  The message always includes estimated bytes,
    available bytes, and the required reduction factor.

    HTTP-safe: yes — the message is safe for operator consumption and may
    appear in the HTTP 422/500 response body.

    Example::

        raise OOMGuardrailError(
            "6.8 GiB estimated, 8.0 GiB available -- reduce dataset by 1.00x"
        )
    """


class PrivilegeEscalationError(SynthEngineError):
    """Raised when the ingestion user has write privileges on the source database.

    Produced by the pre-flight privilege check in
    :class:`synth_engine.modules.ingestion.postgres_adapter.PostgresIngestionAdapter`.
    Raised immediately on detection of superuser status, INSERT, UPDATE, or
    DELETE privileges.

    HTTP-safe: no — messages may contain database role names or privilege
    details that must not appear in HTTP responses.  Log only; expose only a
    generic message to the HTTP caller.
    """


class ArtifactTamperingError(SynthEngineError):
    """Raised when HMAC verification fails on a model artifact.

    Indicates that a persisted model artifact has been modified, corrupted,
    or signed with a different key.  This is a security event and must be
    treated as a potential integrity breach.

    HTTP-safe: no — must not expose internal artifact paths or signing-key
    hints in HTTP responses.  Log only; raise a generic 500 to the caller.

    Note: :exc:`synth_engine.shared.security.hmac_signing.SecurityError`
    is an alias for this class for backward compatibility.
    """


class VaultSealedError(SynthEngineError):
    """Raised when a sensitive operation is attempted on a sealed vault.

    Indicates that the vault KEK has not been derived yet (the operator has
    not called ``POST /unseal``).  Operations requiring the KEK must check
    vault state before proceeding.

    HTTP-safe: yes — the message "Vault is sealed" is safe for HTTP 423
    responses.

    Attributes:
        detail: Human-readable explanation for API consumers.
        status_code: HTTP status code to return (423 Locked).

    Args:
        detail: Human-readable explanation.  Defaults to ``"Vault is sealed"``.

    Example::

        raise VaultSealedError()   # → "Vault is sealed"
        raise VaultSealedError("Vault is sealed — call POST /unseal first")
    """

    def __init__(self, detail: str = "Vault is sealed") -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code: int = 423


class VaultEmptyPassphraseError(SynthEngineError):
    """Raised when the unseal passphrase is empty.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching on exception messages (Architecture finding P5-T5.3).

    Previously inherited ``ValueError``; changed to ``SynthEngineError``
    in T34.1 to unify all vault exceptions under the domain hierarchy and
    ensure they are handled by the domain exception middleware.

    HTTP-safe: yes — the message "Passphrase must not be empty" is safe for
    HTTP 400 responses.
    """


class VaultAlreadyUnsealedError(SynthEngineError):
    """Raised when VaultState.unseal() is called on an already-unsealed vault.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching on exception messages (Architecture finding P5-T5.3).

    Previously inherited ``ValueError``; changed to ``SynthEngineError``
    in T34.1 to unify all vault exceptions under the domain hierarchy and
    ensure they are handled by the domain exception middleware.

    HTTP-safe: yes — the message indicates the vault is already unsealed,
    which is safe for HTTP 400 responses.
    """


class VaultConfigError(SynthEngineError):
    """Raised when VAULT_SEAL_SALT is missing or does not meet the 16-byte minimum.

    Allows the /unseal endpoint to catch this by type rather than by
    string-matching on exception messages (Architecture finding P5-T5.3).

    Previously inherited ``ValueError``; changed to ``SynthEngineError``
    in T34.1 to unify all vault exceptions under the domain hierarchy and
    ensure they are handled by the domain exception middleware.

    HTTP-safe: yes — the message describes a configuration problem without
    leaking internal paths or secrets, safe for HTTP 400 responses.
    """


class LicenseError(SynthEngineError):
    """Raised when license validation fails.

    This is a plain domain exception.  It does NOT carry HTTP status codes.
    HTTP status mapping is the responsibility of the bootstrapper
    middleware/exception handler layer, per ADR-0008.

    Previously inherited bare ``Exception``; changed to ``SynthEngineError``
    in T34.1 so that license failures are handled by the domain exception
    middleware rather than falling through to the catch-all 500 handler.

    HTTP-safe: yes — the ``detail`` message describes why license validation
    failed without leaking internal secrets.  The bootstrapper maps this to
    HTTP 403.

    Attributes:
        detail: Human-readable explanation for API consumers.

    Args:
        detail: Human-readable explanation for API consumers.

    Example::

        raise LicenseError("License token has expired.")
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TLSCertificateError(SynthEngineError):
    """Raised when a TLS certificate fails a security or validity check.

    Produced by the TLS helper functions in :mod:`synth_engine.shared.tls.config`
    for mTLS inter-container certificate management.

    HTTP-safe: yes — the message describes a certificate validation failure
    without leaking private key material or internal secrets.  The bootstrapper
    maps this to HTTP 500 or 503 depending on context (startup vs. health-check).

    Moved from ``shared/tls/config.py`` to ``shared/exceptions.py`` in T46.1
    review (ARCH-F1) to ensure all domain exceptions inherit from
    ``SynthEngineError`` per ADR-0037.

    Args:
        message: Human-readable description of the certificate failure.

    Attributes:
        message: Human-readable description of the failure.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DatasetTooLargeError(SynthEngineError):
    """Raised when a Parquet file or byte payload exceeds the configured memory bound.

    The size check fires before the row-count check so that oversized files
    are rejected before any data is loaded into memory.  Both checks use
    configurable limits from :class:`~synth_engine.shared.settings.ConclaveSettings`
    (``parquet_max_file_bytes`` and ``parquet_max_rows``).

    HTTP-safe: yes — the message describes a capacity limit without leaking
    internal paths or secrets.  The bootstrapper maps this to HTTP 413
    Payload Too Large.

    Added in T47.7 to prevent unbounded memory growth when processing
    user-supplied or operator-uploaded Parquet data.

    Args:
        actual_size: Measured size — bytes for a size check, rows for a row check.
        limit: Configured limit that was exceeded.
        limit_type: Either ``"bytes"`` (file/payload size) or ``"rows"``
            (row count after loading).

    Attributes:
        actual_size: Measured size that triggered the error.
        limit: Configured limit value.
        limit_type: ``"bytes"`` or ``"rows"``.

    Example::

        raise DatasetTooLargeError(
            actual_size=2_200_000_000,
            limit=2_147_483_648,
            limit_type="bytes",
        )
    """

    def __init__(self, actual_size: int, limit: int, limit_type: str) -> None:
        self.actual_size = actual_size
        self.limit = limit
        self.limit_type = limit_type
        super().__init__(
            f"Dataset exceeds configured {limit_type} limit: actual={actual_size}, limit={limit}"
        )
