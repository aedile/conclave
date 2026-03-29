"""Shared Prometheus metrics for the Conclave Engine.

All Prometheus counters that are used across multiple modules must be
defined here to ensure:

1. **No duplicate metric registration**: Prometheus raises ``ValueError`` if
   the same metric name is registered more than once within a process.  A
   single module-level definition is the canonical guard against this.
2. **Consistent labelling**: All audit failure counters use the same
   ``(router, endpoint)`` label schema, enabling unified Grafana queries.

Usage::

    from synth_engine.shared.observability import AUDIT_WRITE_FAILURE_TOTAL

    # In a router exception handler:
    AUDIT_WRITE_FAILURE_TOTAL.labels(
        router="connections", endpoint="/connections/{id}"
    ).inc()

Boundary constraints
--------------------
``shared/`` must not import from ``modules/`` or ``bootstrapper/``.
This module imports only ``prometheus_client`` — no violation.

CONSTITUTION Priority 0: Security — unified audit failure visibility
CONSTITUTION Priority 5: Code Quality — strict typing, Google docstrings
Task: T71.5 — Unify audit failure Prometheus counter (ADV-P70-03)
"""

from __future__ import annotations

from prometheus_client import Counter

#: Unified counter for all WORM audit-write failures across routers.
#:
#: Labels:
#:   ``router``: The FastAPI router name (e.g., ``"connections"``,
#:       ``"settings"``, ``"webhooks"``, ``"admin"``, ``"jobs"``,
#:       ``"security"``, ``"privacy"``).
#:   ``endpoint``: The URL pattern of the failing endpoint (e.g.,
#:       ``"/connections/{id}"``, ``"/settings/{key}"``).  Use the
#:       **route template**, never the resolved path, to keep Prometheus
#:       cardinality bounded.
#:
#: Usage::
#:
#:     AUDIT_WRITE_FAILURE_TOTAL.labels(
#:         router="connections", endpoint="/connections/{id}"
#:     ).inc()
AUDIT_WRITE_FAILURE_TOTAL: Counter = Counter(
    "audit_write_failure_total",
    "Total number of WORM audit write failures across all routers.",
    ["router", "endpoint"],
)
