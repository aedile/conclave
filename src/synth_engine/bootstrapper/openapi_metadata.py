"""OpenAPI metadata definitions for the Conclave Engine API (T59.3).

Centralises:
- :data:`TAGS_METADATA` — tag descriptions for the FastAPI ``openapi_tags`` parameter.
- :data:`RFC7807_ERROR_SCHEMA` — reusable RFC 7807 Problem Details JSON Schema.
- :data:`COMMON_ERROR_RESPONSES` — standard error response definitions for all
  business endpoints.

Usage in route decorators::

    from synth_engine.bootstrapper.openapi_metadata import COMMON_ERROR_RESPONSES

    @router.get(
        "",
        summary="List synthesis jobs",
        responses=COMMON_ERROR_RESPONSES,
    )

RFC 7807 schema reference: https://www.rfc-editor.org/rfc/rfc7807

CONSTITUTION Priority 6: Documentation
Task: T59.3 — OpenAPI Documentation Enrichment
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# RFC 7807 Problem Details JSON Schema
# ---------------------------------------------------------------------------

#: Reusable RFC 7807 Problem Details schema for all error responses.
#:
#: Fields:
#:   type: URI reference identifying the problem type.
#:   title: Short human-readable summary of the problem type.
#:   status: HTTP status code.
#:   detail: Human-readable explanation specific to this occurrence.
RFC7807_PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "description": "URI reference identifying the problem type.",
            "example": "about:blank",
        },
        "title": {
            "type": "string",
            "description": "Short human-readable summary of the problem type.",
            "example": "Unauthorized",
        },
        "status": {
            "type": "integer",
            "description": "HTTP status code.",
            "example": 401,
        },
        "detail": {
            "type": "string",
            "description": "Human-readable explanation specific to this occurrence.",
            "example": "Bearer token missing or invalid.",
        },
    },
    "required": ["status", "title", "detail"],
}

# ---------------------------------------------------------------------------
# Standard error response definitions for business endpoints
# ---------------------------------------------------------------------------

#: Standard error responses shared by all authenticated business endpoints.
#: Each entry follows the OpenAPI ``responses`` object format.
#: Use as: ``@router.get("", responses=COMMON_ERROR_RESPONSES)``.
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Unauthorized — Bearer token missing, invalid, or expired.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "Bearer token missing or invalid.",
                },
            }
        },
    },
    403: {
        "description": "Forbidden — authenticated operator lacks required scope.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": "This operation requires the 'settings:write' scope.",
                },
            }
        },
    },
    404: {
        "description": "Not found — resource does not exist or belongs to another operator.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Not Found",
                    "status": 404,
                    "detail": "Job 42 not found.",
                },
            }
        },
    },
    422: {
        "description": "Unprocessable entity — request body failed validation.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Validation Error",
                    "status": 422,
                    "detail": "epsilon must be > 0.",
                },
            }
        },
    },
    423: {
        "description": "Locked — vault is sealed; unseal before making this request.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Vault Is Sealed",
                    "status": 423,
                    "detail": "Call POST /unseal with the vault passphrase to proceed.",
                },
            }
        },
    },
    503: {
        "description": "Service unavailable — a required dependency (database, Redis) is down.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Service Unavailable",
                    "status": 503,
                    "detail": "Database connection unavailable.",
                },
            }
        },
    },
}

#: Extends COMMON_ERROR_RESPONSES with a 409 Conflict for endpoints that may
#: produce budget exhaustion or resource collision errors.
CONFLICT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON_ERROR_RESPONSES,
    409: {
        "description": "Conflict — privacy budget exhausted, or resource already exists.",
        "content": {
            "application/json": {
                "schema": RFC7807_PROBLEM_SCHEMA,
                "example": {
                    "type": "about:blank",
                    "title": "Privacy Budget Exceeded",
                    "status": 409,
                    "detail": (
                        "The total epsilon budget has been exhausted. "
                        "Reset the budget via POST /api/v1/privacy/budget/refresh."
                    ),
                },
            }
        },
    },
}

# ---------------------------------------------------------------------------
# Tags metadata for FastAPI app
# ---------------------------------------------------------------------------

#: Tag descriptions for the FastAPI ``openapi_tags`` parameter.
#: Appears in the /docs Swagger UI as grouped sections with descriptions.
TAGS_METADATA: list[dict[str, Any]] = [
    {
        "name": "jobs",
        "description": (
            "Synthesis job lifecycle management. "
            "Create, list, start, and monitor synthesis jobs. "
            "Jobs produce privacy-preserving synthetic datasets using DP-SGD."
        ),
    },
    {
        "name": "connections",
        "description": (
            "Database connection configuration. "
            "Manage PostgreSQL connection URLs used as data sources for synthesis jobs. "
            "Credentials are encrypted at rest using AES-256-GCM (ALE)."
        ),
    },
    {
        "name": "settings",
        "description": (
            "Application settings key-value store. "
            "Read and write runtime configuration parameters. "
            "Write operations require the 'settings:write' scope."
        ),
    },
    {
        "name": "webhooks",
        "description": (
            "Webhook endpoint registration. "
            "Register callback URLs to receive job lifecycle events "
            "(COMPLETED, FAILED, SHREDDED). "
            "Payloads are HMAC-signed for delivery verification."
        ),
    },
    {
        "name": "privacy",
        "description": (
            "Differential privacy budget management. "
            "Monitor epsilon/delta consumption and refresh the budget. "
            "All refresh operations are WORM-audited."
        ),
    },
    {
        "name": "admin",
        "description": (
            "Administrative operations. "
            "Apply legal hold flags to jobs to prevent data retention cleanup."
        ),
    },
    {
        "name": "compliance",
        "description": (
            "Compliance and data subject rights. "
            "Implement GDPR Right-to-Erasure and CCPA deletion workflows. "
            "All erasure operations are WORM-audited."
        ),
    },
    {
        "name": "auth",
        "description": ("Authentication. Exchange operator credentials for a JWT Bearer token."),
    },
    {
        "name": "security",
        "description": (
            "Security operations. "
            "Emergency cryptographic shredding and key rotation. "
            "POST /security/shred is reachable even when the vault is sealed."
        ),
    },
    {
        "name": "license",
        "description": (
            "License management. "
            "Activate and verify the operator license for air-gapped deployments. "
            "Challenge-response protocol supports offline activation via QR code."
        ),
    },
    {
        "name": "ops",
        "description": (
            "Infrastructure operations. "
            "Health probes, readiness checks, vault unsealing, and Prometheus metrics."
        ),
    },
]
