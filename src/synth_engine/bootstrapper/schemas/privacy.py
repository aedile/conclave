"""Pydantic request/response schemas for Privacy Budget endpoints.

These schemas sit at the API boundary.  They are distinct from the
``PrivacyLedger`` SQLModel table model in ``modules/privacy/ledger.py``
to maintain the one-way dependency flow: bootstrapper → modules.

Task: P22-T22.4 — Budget Management API
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BudgetResponse(BaseModel):
    """Response body for GET /privacy/budget and POST /privacy/budget/refresh.

    Attributes:
        total_allocated_epsilon: Maximum cumulative epsilon allowed across all
            synthesis jobs.
        total_spent_epsilon: Running total epsilon spent by completed jobs.
        remaining_epsilon: Computed difference between allocated and spent.
        is_exhausted: True when remaining_epsilon is zero or negative; further
            generation requests must be blocked until an operator refreshes the
            budget.
    """

    total_allocated_epsilon: float
    total_spent_epsilon: float
    remaining_epsilon: float
    is_exhausted: bool


class BudgetRefreshRequest(BaseModel):
    """Request body for POST /privacy/budget/refresh.

    Attributes:
        justification: Human-readable reason for the budget refresh.  Required
            by the WORM audit log.  Must be at least 10 characters to discourage
            trivial or empty explanations.
        new_allocated_epsilon: Optional new total budget ceiling.  When provided,
            the ledger's ``total_allocated_epsilon`` is set to this value in
            addition to resetting ``total_spent_epsilon`` to zero.  When omitted,
            only the spent counter is reset and the current allocation is kept.
    """

    justification: str = Field(
        ...,
        min_length=10,
        description="Reason for the budget refresh (minimum 10 characters).",
    )
    new_allocated_epsilon: float | None = Field(
        default=None,
        gt=0,
        description="Optional new total epsilon allocation ceiling (must be > 0).",
    )
