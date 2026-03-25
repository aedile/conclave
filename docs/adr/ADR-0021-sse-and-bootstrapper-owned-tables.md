# ADR-0021 — SSE Streaming Protocol and Bootstrapper-Owned SQLModel Tables

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

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
   `bootstrapper/sse.py` explains the rationale informally, but a decision record
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

### 1a. Amendment (P23-T23.2, 2026-03-17) — Binary artifact download uses raw StreamingResponse

The `GET /jobs/{id}/download` endpoint intentionally uses FastAPI's raw
`StreamingResponse` rather than `sse-starlette`'s `EventSourceResponse`.

**Rationale:** SSE is a text-only protocol (`text/event-stream` content type).
Binary Parquet artifacts cannot be encoded as SSE events without base64
wrapping, which would add encoding overhead, break byte-exact verification
(the HMAC is computed over raw bytes), and require the client to decode the
stream.  Raw `StreamingResponse` with `Content-Type: application/octet-stream`
is the correct mechanism for binary file downloads.

**This is not a deviation from the SSE protocol decision.**  SSE remains the
selected protocol for all *structured event streams* (progress, complete,
error events).  Binary artifact download is a distinct use case — a file
transfer, not an event stream — and is therefore served differently.

**Rule:** Use `sse-starlette` `EventSourceResponse` for structured unidirectional
event streams.  Use plain `StreamingResponse` for raw binary file transfers.
These are complementary, not competing, patterns.

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
| `Connection` | `bootstrapper/schemas/connections.py` | Pure CRUD: name, engine, URL, credentials. No domain logic — the ingestion module receives a connection string, not a `Connection` object. |
| `Setting` | `bootstrapper/schemas/settings.py` | Pure CRUD: key/value store for operator configuration. No domain rules. |
| `SynthesisJob` | `bootstrapper/schemas/jobs.py` | API-layer job tracking: status, created_at, result reference. Domain logic (training, synthesis) lives in `modules/synthesizer/`. The job record is an API concern, not a domain concern. |
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
- Binary artifact download endpoints use plain `StreamingResponse`; this is
  the correct and documented pattern (see Amendment 1a above).

---

### 2a. Amendment (Advisory Drain, 2026-03-21) — SynthesisJob placement accepted in synthesizer module

The original table in section 2 listed `SynthesisJob` as belonging in
`bootstrapper/schemas/job.py`. In practice, `SynthesisJob` resides in
`modules/synthesizer/jobs/job_models.py` and has remained there since Phase 5.

**Why accept the deviation:**

1. **Domain coupling has grown.** SynthesisJob is directly referenced by
   `modules/synthesizer/jobs/job_orchestration.py`, `jobs/job_finalization.py`,
   `jobs/job_steps.py`, and `training/engine.py`. These are domain services, not HTTP
   handlers. The model is a participant in domain logic, not merely a
   CRUD record.

2. **Domain-specific fields.** SynthesisJob now carries DP-SGD parameters
   (`epsilon`, `delta`, `noise_multiplier`), `owner_id` for authorization,
   and training configuration. These are domain concerns that bind the
   model to the synthesizer module.

3. **Migration risk exceeds value.** Moving the file would require updating
   25+ import sites, import-linter contracts, and every test that references
   `job_models`. The risk of breakage outweighs the architectural purity gain.

**Updated table row:**

| Table | Location | Justification |
|-------|----------|---------------|
| `SynthesisJob` | `modules/synthesizer/jobs/job_models.py` | Domain-coupled: referenced by domain services, carries DP-SGD parameters and training config. Accepted deviation from the CRUD-only rule. |

**Rule clarification:** The table placement rule in section 2 applies to
tables that are *exclusively* API CRUD concerns. When a table accrues domain
fields or is referenced by domain services, it legitimately belongs in its
domain module. The boundary is behavioral coupling, not original intent.
