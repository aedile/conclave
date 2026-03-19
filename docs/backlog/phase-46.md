# Phase 46 — mTLS Inter-Container Communication

**Goal**: Implement mutual TLS for all inter-container communication paths,
enabling secure multi-host and Kubernetes deployments where traffic traverses
shared network infrastructure.

**Prerequisite**: Phase 45 merged. Zero open advisories.

**ADR**: ADR-0042 — mTLS Inter-Container Communication Architecture (new, required).
Must document: certificate issuance strategy, rotation mechanism, deployment
topology requirements, and fallback behavior during certificate renewal.

**Source**: Deferred Items (ADR-0029 Gap Analysis) — TBD-03.

---

## T46.1 — Internal Certificate Authority & Certificate Issuance

**Priority**: P1 — Infrastructure prerequisite for all mTLS connections.

### Context & Constraints

1. `mTLS Inter-Container Communication` was deferred in ADR-0029 (Gap 7) because
   single-host Docker Compose deployments use kernel-level network isolation.
   Multi-host deployments (Kubernetes, Docker Swarm) require mTLS to protect
   traffic traversing shared infrastructure.

2. Implement an internal CA or integrate with cert-manager for automatic
   certificate issuance to each container identity:
   - API server (synth-engine)
   - PostgreSQL (via PgBouncer)
   - Redis
   - Huey worker(s)

3. Certificates must include SAN entries matching container hostnames used in
   Docker Compose and Kubernetes service names.

4. Certificate storage must use the existing `secrets/` directory pattern
   (gitignored, operator-provisioned) for Docker Compose, and Kubernetes
   Secrets or cert-manager for K8s deployments.

5. The CA private key must be protected with the same security posture as
   the vault KEK — never committed, never logged, operator-provisioned.

### Acceptance Criteria

1. Internal CA script or cert-manager integration generates per-container certs.
2. CA root certificate distributed to all containers as a trust anchor.
3. Certificates include correct SANs for both Docker Compose and K8s hostnames.
4. Certificate generation documented in operator manual.
5. Unit tests: certificate generation, SAN validation.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `scripts/generate-mtls-certs.sh` (internal CA + cert generation)
- Create: `src/synth_engine/shared/tls/` (TLS configuration helpers)
- Modify: `docs/OPERATOR_MANUAL.md` (mTLS setup section)
- Modify: `docs/PRODUCTION_DEPLOYMENT.md` (certificate provisioning steps)
- Create: `tests/unit/test_tls_config.py`

---

## T46.2 — Wire mTLS on All Container-to-Container Connections

**Priority**: P1 — Core mTLS implementation.

### Context & Constraints

1. All container-to-container connections must use mutual TLS:
   - **API → PostgreSQL** (via PgBouncer): Configure `sslmode=verify-full`
     in SQLAlchemy connection string. PgBouncer must present a server cert
     and verify the API client cert.
   - **API → Redis**: Configure Redis TLS with client certificate
     authentication. Update Huey and idempotency middleware (Phase 45)
     Redis clients.
   - **API → Huey worker**: Huey uses Redis as the message broker — this
     path is covered by Redis mTLS.

2. Docker Compose must support both plaintext (development) and mTLS
   (production) modes via environment variable toggle:
   `MTLS_ENABLED=true|false` (default: `false` for backward compatibility).

3. PgBouncer configuration (`pgbouncer/pgbouncer.ini`) must be updated to
   support TLS server and client certificate verification.

4. Redis configuration must be updated to require TLS when `MTLS_ENABLED=true`.

5. Connection string construction in `ConclaveSettings` must conditionally
   include TLS parameters based on the `MTLS_ENABLED` flag.

### Acceptance Criteria

1. API → PostgreSQL connection uses `sslmode=verify-full` when mTLS enabled.
2. API → Redis connection uses TLS with client cert when mTLS enabled.
3. PgBouncer configured for TLS server cert and client cert verification.
4. Redis configured for TLS with client authentication.
5. `MTLS_ENABLED` toggle in `ConclaveSettings` with `false` default.
6. Docker Compose override file for mTLS-enabled deployment.
7. Plaintext connections rejected when mTLS is enabled (smoke test).
8. Unit tests: connection string construction with/without TLS parameters.
9. Integration test: full connection through mTLS-enabled PgBouncer.
10. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/settings.py` (mTLS settings)
- Modify: `src/synth_engine/bootstrapper/factories.py` (TLS connection params)
- Modify: `src/synth_engine/bootstrapper/dependencies/redis.py` (TLS Redis client)
- Modify: `pgbouncer/pgbouncer.ini` (TLS configuration)
- Create: `docker-compose.mtls.yml` (mTLS override)
- Modify: `docker-compose.yml` (conditional TLS volume mounts)
- Create: `tests/unit/test_mtls_settings.py`
- Create: `tests/integration/test_mtls_connections.py`

---

## T46.3 — Certificate Rotation Without Downtime

**Priority**: P1 — Operational requirement for production mTLS.

### Context & Constraints

1. Certificates have finite lifetimes (recommended: 90 days for leaf certs,
   1 year for internal CA). Rotation must not cause service downtime.

2. Implement certificate rotation strategy:
   - **Docker Compose**: Operator replaces cert files in `secrets/` and
     sends SIGHUP to containers (or restarts with rolling strategy).
   - **Kubernetes**: cert-manager handles automatic renewal; containers
     watch for cert file changes and reload.

3. The API server (uvicorn) must support TLS certificate reload without
   full process restart. If uvicorn doesn't support dynamic reload,
   document the rolling restart procedure.

4. PgBouncer supports `RELOAD` command for certificate refresh.

5. Redis supports `CONFIG SET tls-cert-file` for dynamic cert reload.

### Acceptance Criteria

1. Certificate rotation procedure documented for Docker Compose deployment.
2. Certificate rotation procedure documented for Kubernetes deployment.
3. PgBouncer cert reload verified via `RELOAD` command.
4. Redis cert reload verified via `CONFIG SET`.
5. No client connection drops during certificate rotation (or documented
   reconnection behavior with retry).
6. Monitoring: certificate expiry metric exposed via `/metrics` endpoint.
7. Unit tests: certificate expiry detection, metric emission.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `scripts/rotate-mtls-certs.sh` (rotation helper)
- Modify: `src/synth_engine/shared/telemetry.py` (cert expiry metric)
- Modify: `docs/OPERATOR_MANUAL.md` (rotation procedures)
- Modify: `docs/DISASTER_RECOVERY.md` (cert loss recovery)
- Create: `tests/unit/test_cert_expiry_metric.py`

---

## T46.4 — Network Policy Enforcement & Documentation

**Priority**: P2 — Defense-in-depth for Kubernetes deployments.

### Context & Constraints

1. For Kubernetes deployments, provide NetworkPolicy manifests that enforce
   mTLS-only communication paths between pods.

2. Document the threat model: what mTLS protects against (network sniffing,
   MITM on shared infrastructure) and what it does not (compromised container,
   kernel exploit).

3. Update ADR-0029 to mark Gap 7 (mTLS) as DELIVERED with Phase 46 reference.

### Acceptance Criteria

1. Kubernetes NetworkPolicy manifests for all inter-container paths.
2. Threat model documented in ADR-0042.
3. Smoke test: plaintext connections rejected when mTLS enforced.
4. ADR-0029 updated with Phase 46 assignment for Gap 7.
5. `docs/backlog/deferred-items.md` TBD-03 marked DELIVERED with Phase 46.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `k8s/network-policies/` (NetworkPolicy manifests)
- Create: `docs/adr/ADR-0042-mtls-inter-container-communication.md`
- Modify: `docs/adr/ADR-0029-architectural-requirements-gap-analysis.md`
- Modify: `docs/backlog/deferred-items.md`
- Modify: `docs/infrastructure_security.md` (mTLS section)

---

## Task Execution Order

```
T46.1 (Internal CA & Certs) ──────> first (prerequisite for all connections)
T46.2 (Wire mTLS connections) ───> after T46.1 (needs certificates)
T46.3 (Certificate rotation) ────> after T46.2 (needs working mTLS)
T46.4 (Network policy & docs) ──> LAST (documents everything)
```

Sequential execution — each task builds on the previous.

---

## Phase 46 Exit Criteria

1. Internal CA generates per-container certificates with correct SANs.
2. All container-to-container connections use mTLS when enabled.
3. Plaintext connections rejected when mTLS is enforced.
4. Certificate rotation documented and tested for both Docker Compose and K8s.
5. Certificate expiry metric exposed via `/metrics`.
6. Kubernetes NetworkPolicy manifests provided.
7. ADR-0042 documents the mTLS architecture and threat model.
8. ADR-0029 updated with Phase 46 assignment.
9. TBD-03 marked DELIVERED in deferred items.
10. All quality gates pass.
11. Zero open advisories in RETRO_LOG.
12. Review agents pass for all tasks.
