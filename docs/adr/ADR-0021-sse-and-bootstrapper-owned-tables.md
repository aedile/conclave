# ADR-0021 — SSE Streaming Protocol and Bootstrapper-Owned SQLModel Tables

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** Advisory Drain Sprint (ADV-051)

---

## Context

Two architectural patterns were introduced during Phase 5 (T5.1) without a
corresponding decision record:

1. **Streaming protocol selection** — The job progress endpoint uses
   Server-Sent Events (SSE) rather than WebSockets. An inline comment in
   `shared/sse.py` explains the rationale informally, but a decision record
   is required per project standards.

2. **API-layer-owned SQLModel tables** — `Connection`, `Setting`, and
   `SynthesisJob` tables are defined under `bootstrapper/schemas/` rather than
   inside their respective domain modules. This is a new pattern that deviates
   from how domain-owned tables (e.g., `PrivacyLedger` in `modules/privacy/`)
   are placed. The rule governing which tables belong where has not been
   documented.

Both patterns are correct. This ADR documents the decisions so that future
contributors understand the reasoning and apply the rules consistently.

---

## Decision

### 1. SSE over WebSockets for job progress streaming

**Server-Sent Events (SSE) is the selected protocol for streaming job
progress from server to client.**

The implementation uses the `sse-starlette` library, which wraps FastAPI's
`StreamingResponse` with the `text/event-stream` MIME type.

**Why SSE:**

| Property | SSE | WebSockets |
|----------|-----|------------|
| Transport | HTTP/1.1 and HTTP/2 | Requires upgrade handshake |
| Direction | Unidirectional (server → client) | Bidirectional |
| Proxy compatibility | Works through standard HTTP proxies | Requires proxy WebSocket support |
| Air-gap compatibility | High — standard HTTP | Lower — upgrade may be blocked |
| Reconnection | Automatic (built into the protocol) | Must be implemented manually |
| Implementation complexity | Low (`sse-starlette`) | Higher (handshake, ping/pong) |
| Use case fit | Progress streaming is unidirectional | Overkill for one-way data |

Job progress reporting is inherently unidirectional: the server emits events
(started, progress %, completed, failed) and the client only listens.
WebSocket bidirectionality would be unused and introduces unnecessary
complexity and proxy risk in air-gapped environments.

The `sse-starlette` library provides:
- `text/event-stream` content type negotiation
- `EventSourceResponse` that handles chunked transfer encoding
- Automatic `Last-Event-ID` header support for client reconnection
- Compatibility with FastAPI's async generator pattern

**When WebSockets would be appropriate:**
WebSockets should be adopted if a future requirement introduces bidirectional
communication — for example, job cancellation triggered from the browser
during an active stream, or interactive query refinement. Until such a
requirement exists, SSE remains the selected protocol.

---

### 2. Bootstrapper-owned SQLModel tables

**Tables whose sole purpose is to serve an API CRUD endpoint with no domain
logic belong in `bootstrapper/schemas/`. Tables that have domain invariants
or business rules belong in their domain module.**

#### The rule

| Table placement | When to use |
|-----------------|-------------|
| `bootstrapper/schemas/<name>.py` | Table exists only to persist/retrieve data via CRUD endpoints; no domain validation, no business rules, no domain events |
| `modules/<domain>/models.py` (or equivalent) | Table has domain invariants, participates in domain logic, emits domain events, or is referenced by domain services |

#### Applied examples

| Table | Location | Justification |
|-------|----------|---------------|
| `Connection` | `bootstrapper/schemas/connection.py` | Pure CRUD: name, engine, URL, credentials. No domain logic — the ingestion module receives a connection string, not a `Connection` object. |
| `Setting` | `bootstrapper/schemas/setting.py` | Pure CRUD: key/value store for operator configuration. No domain rules. |
| `SynthesisJob` | `bootstrapper/schemas/job.py` | API-layer job tracking: status, created_at, result reference. Domain logic (training, synthesis) lives in `modules/synthesizer/`. The job record is an API concern, not a domain concern. |
| `PrivacyLedger` | `modules/privacy/models.py` | Domain-owned: epsilon budget tracking has domain invariants (budget cannot exceed allocated epsilon), participates in domain logic in `modules/privacy/accountant.py`. |

#### Rationale

Placing API-layer tables in `bootstrapper/schemas/` enforces the separation
between the HTTP layer and domain logic. Domain modules remain unaware of
HTTP concerns. The bootstrapper assembles the application but does not own
domain logic.

This is consistent with ADR-0001 (modular monolith topology): the
bootstrapper is the composition root, not a domain. Tables that exist because
an HTTP client needs to create, read, update, or delete a record are HTTP
concerns.

---

## Rationale

**Why document both decisions in a single ADR?**

Both decisions emerged from the same task (T5.1) and share a common theme:
architectural patterns introduced without documentation. A joint ADR is
cleaner than two separate thin ADRs. Future ADRs should document these
patterns at the time the first instance is introduced.

**Why not move `Connection`/`Setting`/`SynthesisJob` into domain modules?**

These tables have no domain logic. Placing them in domain modules would
create artificial coupling: the ingestion module would gain knowledge of
the HTTP layer's persistence concerns. The bootstrapper owning them is
correct.

**Why `sse-starlette` rather than raw `StreamingResponse`?**

`sse-starlette` handles the SSE protocol details (event framing,
`Last-Event-ID` support, disconnect detection) that would otherwise require
manual implementation. It is a thin, focused library with no transitive
dependencies beyond `starlette`, which is already a FastAPI dependency.

---

## Consequences

- All future streaming endpoints that are unidirectional (server → client)
  should use SSE via `sse-starlette`. Deviating from this requires an ADR
  amendment with justification.
- All future API-layer-only tables belong in `bootstrapper/schemas/`. Domain
  tables belong in their module. The table placement rule documented above is
  the authoritative reference.
- If job cancellation from the client becomes a requirement, a WebSocket
  implementation ADR must be created before implementation begins.
- The `sse-starlette` version is pinned in `pyproject.toml`. Upgrades must
  be reviewed for breaking changes to `EventSourceResponse`.
