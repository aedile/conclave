# Phase 2: Foundational Architecture & Shared Services

**Goal:** Scaffold the Python modular monolith structure, the PostgreSQL database, and cross-cutting security/utility services.

---

## Task 2.1: Implement Core Bootstrapper & Background Defenses
**Assignee:** [Dev A]
**Priority:** Blocked by 1.7
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a Backend Architect, I want the foundational FastAPI bootstrapper and background task middleware configured, so that all subsequent modules can safely register dependencies and background tasks (Huey) with strict idempotency and self-healing (OOM Reaper) protections.

### Context & Constraints
*   System is a Modular Monolith. Do not use global state; rely on FastAPI native `Depends()`.
*   Idempotency is critical for preventing duplicate privacy budget (Epsilon) deductions on network retries.
*   "Orphan Task Reaper" must handle Out-Of-Memory (OOM) failures from Hueys workers and unlock stalled records.

### Acceptance Criteria
*   [ ] Create the core FastAPI application factory (`src/synth_engine/main.py`).
*   [ ] Implement OpenTelemetry (OTEL) context injection middleware.
*   [ ] Create a Redis-based API Idempotency dependency/middleware using TTLs.
*   [ ] Implement the Huey task queue configuration.
*   [ ] Create the "Orphan Task Reaper" cron job that scans the DB for "In Progress" tasks older than 1 hour, marks them failed, and releases associated pessimistic locks.

### Testing & Quality Gates
*   Write unit tests demonstrating Idempotency Key rejection for duplicate identical requests within 5 seconds.
*   Write an integration test where a mocked `Task` record is intentionally left `IN_PROGRESS` and verify the `OrphanTaskReaper` correctly fails it.

### Files to Create/Modify
*   [NEW] `src/synth_engine/main.py`
*   [NEW] `src/synth_engine/shared/middleware/idempotency.py`
*   [NEW] `src/synth_engine/shared/tasks/reaper.py`
*   [NEW] `src/synth_engine/shared/telemetry.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** FastAPI `Depends` used exclusively.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 2.2: Establish Secure Database Layer
**Assignee:** [Dev B]
**Priority:** Blocked by 1.7
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a Data Engineer, I want the PostgreSQL infrastructure and SQLModel ORM configured with Application-Level Encryption (ALE), so that sensitive PII fields are encrypted *before* they even hit the database engine.

### Context & Constraints
*   Database schema must be version-controlled (Alembic).
*   Use `PgBouncer` to manage connection limits efficiently.
*   Application-Level Encryption (ALE) must utilize a robust library (e.g., `cryptography.fernet` or `aws-encryption-sdk` mock) and tie into the `Vault Unseal` state.

### Acceptance Criteria
*   [ ] Configure `docker-compose.yml` with PostgreSQL and PgBouncer images.
*   [ ] Configure Alembic for database migrations.
*   [ ] Create foundational `SQLModel` base classes.
*   [ ] Implement a custom SQLAlchemy Type decorator (or similar interceptor) that automatically Fernet-encrypts string fields marked as `PII` on `INSERT`/`UPDATE` and decrypts on `SELECT`.

### Testing & Quality Gates
*   Integration test using `pytest-postgresql`: Insert a PII record, query the raw database via pure string execution (bypassing the ORM), and `assert` the raw value is encrypted cipher-text.
*   Query via ORM and `assert` the value is seamlessly decrypted.

### Files to Create/Modify
*   [MODIFY] `docker-compose.yml`
*   [NEW] `alembic.ini`
*   [NEW] `src/synth_engine/shared/db.py`
*   [NEW] `src/synth_engine/shared/security/ale.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Database connections pooled, ALE applied.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 2.3: Implement Zero-Trust Authentication
**Assignee:** [Dev C]
**Priority:** Blocked by 1.7
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As a Security Engineer, I want a strict JWT middleware that binds tokens to specific client IPs or mTLS certificates, so that if a JWT is stolen, it cannot be replayed from a different machine.

### Context & Constraints
*   Follow Zero Trust architectural principles.

### Acceptance Criteria
*   [ ] Implement a FastAPI Dependency for JWT validation.
*   [ ] The JWT validation must explicitly extract the client IP or `X-Forwarded-For` (from a trusted proxy) or the client's mTLS Subject Alternative Name.
*   [ ] The validator must hash this client identifier and verify it matches the `bound_client_hash` claim embedded in the JWT payload.
*   [ ] Implement strict Role-Based Access Control (RBAC) scopes.

### Testing & Quality Gates
*   Write unit tests that mock an incoming request with a valid JWT but a mismatched Client IP; the request must immediately return a 401 Unauthorized.

### Files to Create/Modify
*   [NEW] `src/synth_engine/shared/auth/jwt.py`
*   [NEW] `src/synth_engine/shared/auth/scopes.py`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Zero Trust token binding enforced.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.

---

## Task 2.4: Vault Unseal & Infrastructure Observability
**Assignee:** [Dev D]
**Priority:** Blocked by 1.7
**Estimated Effort:** 4 days

### User Story / Agentic Goal
As an SRE, I want the system to cryptographically lock itself upon boot and generate immutable audit logs, so that we can guarantee to auditors that data relies on human-provided, unstored keys and that all actions are traceable.

### Context & Constraints
*   Application must boot into a `SEALED` state. No regular APIs work until unsealed.
*   Logs must be WORM (Write Once, Read Many) compliant or cryptographically signed.

### Acceptance Criteria
*   [ ] Implement a `/unseal` API endpoint that accepts a High-Entropy passphrase and derives the master encryption Key Encryption Key (KEK) into ephemeral memory (NOT disk).
*   [ ] Create a global state manager that blocks all non-unseal routes if `is_sealed == True`.
*   [ ] Implement a central Audit Logger that cryptographically signs every audit event (e.g., using a separate audit key) before writing to `stdout` or a dedicated audit database table.
*   [ ] Set up Prometheus `/metrics` endpoint.
*   [ ] Add Alertmanager and a basic Grafana dashboard to `docker-compose.yml`.

### Testing & Quality Gates
*   Test that hitting ANY standard API endpoint returns `503 Service Unavailable` or `423 Locked` while the vault is sealed.
*   Test that the Audit Logger generates a verifiable digital signature for a log entry.

### Files to Create/Modify
*   [NEW] `src/synth_engine/shared/security/vault.py`
*   [NEW] `src/synth_engine/shared/security/audit.py`
*   [MODIFY] `src/synth_engine/main.py`
*   [MODIFY] `docker-compose.yml`
*   [NEW] `grafana/provisioning/dashboards/synth_engine.json`

### Definition of Done (DoD) Checklist
1. **Architectural Compliance:** Vault pattern fully implemented.
2. **Coverage Gate:** >= 90%.
3. **Pipeline Green:** CI passes.
4. **Peer Review:** Reviewed.
5. **Acceptance Verification:** Acceptance criteria met.
