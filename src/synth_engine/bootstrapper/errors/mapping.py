"""Operator-friendly RFC 7807 exception-to-HTTP mapping for domain exceptions.

Defines :class:`OperatorErrorEntry` and :data:`OPERATOR_ERROR_MAP`, which map
domain exception classes to presentation-layer tuples consumed by exception
handlers in :mod:`synth_engine.bootstrapper.router_registry` and the
``/unseal`` route in :mod:`synth_engine.bootstrapper.lifecycle`.

Security rule: :exc:`PrivilegeEscalationError` and
:exc:`ArtifactTamperingError` are mapped with FIXED, STATIC detail strings
that contain no security-sensitive context (no role names, no artifact paths,
no HMAC hints).  The raw exception message is logged at WARNING level but
must never appear verbatim in the HTTP response body (ADV-036+044).

Task: P29-T29.3 — Error Message Audience Differentiation
Task: T34.3 — Complete OPERATOR_ERROR_MAP for All Domain Exceptions
Task: T36.2 — Split bootstrapper/errors.py Into Focused Modules
Task: P36 review — Import CycleDetectionError and CollisionError from shared.exceptions (ADR-0037)
Task: T37.1 — Add EpsilonMeasurementError to OPERATOR_ERROR_MAP
Task: T38.1 — Add AuditWriteError to OPERATOR_ERROR_MAP (status 500)
"""

from __future__ import annotations

from typing import TypedDict

from synth_engine.shared.exceptions import (
    ArtifactTamperingError,
    AuditWriteError,
    BudgetExhaustionError,
    CollisionError,
    CycleDetectionError,
    EpsilonMeasurementError,
    LicenseError,
    OOMGuardrailError,
    PrivilegeEscalationError,
    VaultAlreadyUnsealedError,
    VaultConfigError,
    VaultEmptyPassphraseError,
    VaultSealedError,
)


class OperatorErrorEntry(TypedDict):
    """Presentation-layer mapping for a domain exception.

    Each entry in :data:`OPERATOR_ERROR_MAP` must supply all four fields so
    that any exception handler can build a complete RFC 7807 response without
    falling back to defaults.

    Attributes:
        title: Short, plain-language summary shown as the error heading
            in the frontend ``RFC7807Toast`` component.
        detail: Operator-facing explanation with a concrete remediation
            action.  Must NOT contain raw exception messages, epsilon values,
            internal paths, or any other developer-only technical details.
        status_code: HTTP status code for this error class.
        type_uri: RFC 7807 ``type`` field — either ``"about:blank"`` or a
            URI identifying the specific problem type.
    """

    title: str
    detail: str
    status_code: int
    type_uri: str


#: Operator-friendly RFC 7807 presentation mapping for all domain exceptions.
#:
#: Keys are exception *classes* (not instances).  Values are
#: :class:`OperatorErrorEntry` dicts consumed by exception handlers in
#: :mod:`synth_engine.bootstrapper.router_registry` and the ``/unseal`` route
#: in :mod:`synth_engine.bootstrapper.lifecycle`.
#:
#: Security rule: :exc:`PrivilegeEscalationError` and
#: :exc:`ArtifactTamperingError` are mapped with FIXED, STATIC detail strings
#: that contain no security-sensitive context (no role names, no artifact paths,
#: no HMAC hints).  The raw exception message is logged at WARNING level but
#: must never appear verbatim in the HTTP response body (ADV-036+044).
#: The ``detail`` field in these entries is a safe, sanitized constant — not
#: derived from ``str(exc)``.
OPERATOR_ERROR_MAP: dict[type[Exception], OperatorErrorEntry] = {
    AuditWriteError: OperatorErrorEntry(
        title="Audit Trail Write Failure",
        detail=(
            "The privacy budget was deducted but the audit trail could not be written. "
            "Manual reconciliation of the privacy ledger is required. "
            "Contact your administrator immediately."
        ),
        status_code=500,
        type_uri="/problems/audit-write-failure",
    ),
    BudgetExhaustionError: OperatorErrorEntry(
        title="Privacy Budget Exceeded",
        detail=(
            "The privacy budget for this dataset has been exhausted. "
            "Reset the privacy budget via POST /privacy/budget/reset "
            "or contact your administrator."
        ),
        status_code=409,
        type_uri="about:blank",
    ),
    EpsilonMeasurementError: OperatorErrorEntry(
        title="Privacy Measurement Failure",
        detail=(
            "The privacy cost of the training run could not be verified. "
            "The job has been marked FAILED. "
            "Retry the job or contact your administrator."
        ),
        status_code=500,
        type_uri="/problems/epsilon-measurement-failure",
    ),
    OOMGuardrailError: OperatorErrorEntry(
        title="Memory Limit Exceeded",
        detail=(
            "The synthesis job was rejected because the estimated memory "
            "requirement exceeds available system memory. "
            "Reduce the dataset size or the number of rows and retry."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
    VaultSealedError: OperatorErrorEntry(
        title="Vault Is Sealed",
        detail=(
            "Unseal the vault before performing data operations. POST /unseal with your passphrase."
        ),
        status_code=423,
        type_uri="about:blank",
    ),
    VaultEmptyPassphraseError: OperatorErrorEntry(
        title="Empty Passphrase",
        detail="Enter a non-empty passphrase to unseal the vault.",
        status_code=400,
        type_uri="about:blank",
    ),
    VaultConfigError: OperatorErrorEntry(
        title="Vault Configuration Error",
        detail=(
            "The vault cannot be unsealed due to a configuration error. "
            "Ensure the VAULT_SEAL_SALT environment variable is set and "
            "meets the 16-byte minimum length requirement."
        ),
        status_code=400,
        type_uri="about:blank",
    ),
    # HTTP 400 (not 409) is intentional: "already unsealed" means the operator's
    # desired state is already achieved — a bad request, not a resource conflict.
    # This matches the bespoke inline handler in bootstrapper/lifecycle.py which
    # also returns 400 for VaultAlreadyUnsealedError on POST /unseal.
    VaultAlreadyUnsealedError: OperatorErrorEntry(
        title="Vault Already Unsealed",
        detail=(
            "The vault is already unsealed. No action is required. "
            "To re-seal and rotate the key, call POST /seal first."
        ),
        status_code=400,
        type_uri="about:blank",
    ),
    LicenseError: OperatorErrorEntry(
        title="License Validation Failed",
        detail=(
            "The engine license could not be validated. "
            "Ensure a valid license token is configured and has not expired. "
            "Contact your administrator to renew or reconfigure the license."
        ),
        status_code=403,
        type_uri="about:blank",
    ),
    CollisionError: OperatorErrorEntry(
        title="Masking Collision Detected",
        detail=(
            "A collision was detected during deterministic masking. "
            "This indicates an unexpected state in the masking registry. "
            "Retry the operation or contact your administrator if the problem persists."
        ),
        status_code=409,
        type_uri="about:blank",
    ),
    CycleDetectionError: OperatorErrorEntry(
        title="Cycle Detected in Schema Graph",
        detail=(
            "A circular dependency was detected in the database schema foreign-key graph. "
            "Provide explicit cycle-breaking rules before ingestion can proceed."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
    # Security-sensitive exceptions: detail is a fixed static string.
    # The raw exception message (which may contain credential hints or internal
    # paths) is logged at WARNING level but MUST NOT appear in HTTP responses.
    PrivilegeEscalationError: OperatorErrorEntry(
        title="Insufficient Database Privileges",
        detail=(
            "The ingestion database user has write privileges on the source database. "
            "Configure a read-only database user for ingestion and retry."
        ),
        status_code=403,
        type_uri="about:blank",
    ),
    ArtifactTamperingError: OperatorErrorEntry(
        title="Model Artifact Integrity Failure",
        detail=(
            "A model artifact failed integrity verification. "
            "The artifact may have been modified or corrupted. "
            "Delete the affected artifact and re-run the synthesis job."
        ),
        status_code=422,
        type_uri="about:blank",
    ),
}
