# ADR-0045 — mTLS Inter-Container Communication Architecture

**Date:** 2026-03-22
**Status:** Accepted
**Deciders:** PM + Architecture Reviewer + Security Reviewer
**Task:** P46-T46.4
**Resolves:** ADR-0029 Gap 7 — mTLS inter-container communication (previously Deferred,
now Implemented in Phase 46)

---

## Context

The Conclave Engine's architecture specification (§4.2 of
`docs/ARCHITECTURAL_REQUIREMENTS.md`) requires all inter-container communication
to use mutual TLS (mTLS). ADR-0029 Gap 7 documented this requirement as
**Deferred** during the Phase 11 gap analysis, with the rationale that Docker
bridge network isolation is sufficient containment for single-host deployments.

Gap 7 was deferred, not descoped. The deferral trigger — deployment on shared
multi-host infrastructure — became relevant as the system's operational profile
expanded to support multi-node Kubernetes deployments. Phase 46 implements the
full mTLS requirement as an opt-in overlay that is transparent to operators who
do not need it and activated by a single environment variable for those who do.

### Problem Statement

Without mTLS, inter-container traffic is plaintext TCP. An attacker who achieves
lateral movement from a compromised pod (or shared host in a multi-tenant cluster)
can:

1. Passively read all database query traffic, including synthesized row data and
   synthesis job parameters.
2. Intercept or replay authentication tokens on the PgBouncer authentication path.
3. Modify Redis task queue payloads, causing arbitrary Huey worker behaviour.
4. Perform man-in-the-middle attacks on any container-to-container connection.

These attack vectors are not fully mitigated by Docker bridge network isolation
in Kubernetes multi-node deployments, where pod-to-pod traffic can traverse
physical host boundaries via the cluster overlay network.

---

## Decision

Implement mutual TLS for all data-plane inter-container connections using an
ECDSA P-256 internal certificate authority, with leaf certificates issued per
service. The implementation is an opt-in Docker Compose overlay
(`docker-compose.mtls.yml`) activated by `MTLS_ENABLED=true`.

For Kubernetes deployments, network-level segmentation is enforced by the
`NetworkPolicy` manifests in `k8s/network-policies/`, which complement
application-level mTLS.

---

## Design Decisions

### 1. ECDSA P-256 Only — No RSA

**Decision:** All certificates (CA, leaf) use ECDSA P-256. RSA is not used.

**Rationale:**

- ECDSA P-256 provides 128-bit equivalent security with significantly shorter
  key and signature sizes than 2048-bit RSA (32-byte key vs 256-byte key).
- TLS handshake performance is measurably better with ECDSA, particularly
  for high-frequency short-lived connections (Redis pipeline requests).
- P-256 is the NSA Suite B recommended curve and is supported by all TLS 1.3
  implementations in use (psycopg2/libpq, asyncpg, redis-py, PostgreSQL).
- The Conclave Engine makes no compatibility concessions to legacy clients;
  all services are under operator control. RSA backwards compatibility is not
  required.

### 2. Hardcoded SERVICE_HOSTNAMES Allowlist

**Decision:** The certificate generation script (`scripts/generate-mtls-certs.sh`)
issues certificates only for a fixed set of service hostnames defined as a
constant allowlist. Arbitrary hostnames cannot be issued by the internal CA.

**Rationale:** This is an intentional security constraint, not a limitation.
Allowing operators to specify arbitrary SANs for the internal CA would create
a cert-minting vector: a compromised operator script or CI pipeline could issue
a certificate for any hostname, enabling MITM attacks against any TLS endpoint
the container can reach. The fixed allowlist (`app`, `pgbouncer`, `postgres`,
`redis`) limits the CA's blast radius to exactly the services it was designed
to protect.

Operators requiring additional services must add them to the allowlist and
regenerate the CA — a deliberate, auditable action.

### 3. Single-Hop Certificate Chain — No Intermediate CAs

**Decision:** The certificate chain is exactly two hops: Root CA → Leaf
certificate. No intermediate CAs are issued.

**Rationale:** Intermediate CAs are justified when:
- Certificate issuance is delegated to separate organizational units.
- Revocation of a compromised intermediate is preferable to revoking the root.
- The CA hierarchy spans multiple geographies or environments.

None of these conditions apply to the Conclave Engine's single-operator,
air-gapped deployment. A three-hop chain (Root → Intermediate → Leaf) would
add operational complexity (intermediate key management, CRL distribution)
without a corresponding security benefit. If the Root CA is compromised, the
entire deployment is compromised regardless of intermediates. Single-hop is
the correct choice for this threat model.

### 4. Monitoring Services Exempt from mTLS

**Decision:** Prometheus, AlertManager, Grafana, and MinIO are exempt from the
mutual TLS requirement.

**Rationale:** Monitoring services are read-only observability consumers:

- **Prometheus** scrapes `/metrics` — an unauthenticated endpoint by design
  (no user data, only aggregated counters and histograms).
- **AlertManager** receives alert notifications from Prometheus — no sensitive
  data payload.
- **Grafana** queries Prometheus as a data source — aggregated metrics only.
- **MinIO** stores backup artifacts — encryption at rest handles the storage
  security concern; mTLS on the MinIO client-server path would require
  distributing client certs to the MinIO client, complicating the backup
  operator workflow for no meaningful gain.

None of these services have a write path to the PostgreSQL database or the
Huey task queue (the two surfaces that carry PII or job control data). The
mTLS threat model targets data-plane paths; monitoring-plane paths are exempt.

### 5. Docker Compose Overlay Pattern

**Decision:** mTLS is implemented as an overlay (`docker-compose.mtls.yml`)
applied on top of the base `docker-compose.yml` using Docker Compose merge
semantics (`-f docker-compose.yml -f docker-compose.mtls.yml`).

**Rationale:**

- Preserves backward compatibility. Operators who do not need mTLS (development
  environments, CI pipelines without certificates) continue to use the unmodified
  base file.
- Keeps the base file readable. mTLS configuration (volume mounts for cert
  directories, environment variable overrides) adds significant YAML verbosity.
  Separating it into an overlay keeps each file focused.
- Matches the `MTLS_ENABLED` environment variable gate: the overlay is only
  applied when the operator explicitly passes both compose files.

### 6. TLS 1.3 Minimum Protocol Version

**Decision:** All mTLS connections enforce TLS 1.3 as the minimum protocol version.
TLS 1.2 is not permitted.

**Rationale:** TLS 1.3 eliminates vulnerable cipher suites present in TLS 1.2
(RC4, 3DES, export ciphers), mandatory forward secrecy is built into the
protocol rather than a configurable option, and the handshake is one round-trip
fewer (0-RTT capability). All dependencies (PostgreSQL 14+, Redis 6+, Python
`ssl` module) support TLS 1.3. There is no operational reason to permit TLS 1.2.

---

## Threat Model

### In-Scope Threats (Mitigated by this Implementation)

| Threat | Mitigation |
|--------|------------|
| Network sniffing on shared cluster infrastructure | All data-plane connections encrypted with TLS 1.3 |
| MITM on pod-to-pod traffic | Mutual certificate authentication — both sides verify the peer |
| Lateral movement from compromised monitoring pod | Monitoring services can reach only `/metrics`; cannot connect to postgres or redis |
| Replay of authentication tokens on PgBouncer path | TLS session keys are ephemeral (forward secrecy via ECDHE) |
| Unauthorized pod connecting to postgres | postgres-policy NetworkPolicy + client cert requirement on PgBouncer |
| DNS spoofing of service discovery | Certificate SANs are bound to service hostnames, not IP addresses |

### Out-of-Scope Threats (Not Mitigated)

| Threat | Reason Out of Scope |
|--------|---------------------|
| Compromised container with filesystem access reading cert files | Container filesystem access implies full compromise — cert files are one of many things an attacker could read. Key rotation mitigates persistence. |
| Kernel exploit / host OS compromise | Below the container abstraction layer. Host hardening (LUKS, capability dropping, seccomp) is documented in `docs/infrastructure_security.md`. |
| Compromised Kubernetes control plane | Control plane access bypasses all application-layer controls. Cluster hardening is out of scope for application-level ADRs. |
| Certificate Authority key compromise | Mitigated by keeping the CA key offline after issuance (operator responsibility). See `scripts/generate-mtls-certs.sh`. |
| Supply chain attack on base container image | Addressed separately by image signing (ADR-0042). |

### CNI Prerequisite for NetworkPolicy Enforcement

Kubernetes `NetworkPolicy` manifests are a **declaration of intent**, not an
enforcement mechanism in themselves. Enforcement requires a CNI plugin that
implements the NetworkPolicy API:

- **Calico** — supports NetworkPolicy and GlobalNetworkPolicy; recommended for
  on-premise bare-metal Kubernetes.
- **Cilium** — eBPF-based enforcement with identity-aware policies; recommended
  for high-throughput or multi-tenant clusters.
- **Weave Net** — supports NetworkPolicy; simpler operational model.

CNI plugins that **do not** enforce NetworkPolicy (kubenet, Flannel, AWS VPC CNI
without the Network Policy Controller) will silently ignore the manifests in
`k8s/network-policies/`. Operators MUST verify their CNI before assuming the
policies are active.

---

## Implementation

### Phase 46 Deliverables

| Task | Deliverable | Status |
|------|-------------|--------|
| T46.1 | `scripts/generate-mtls-certs.sh` — ECDSA P-256 internal CA and per-service leaf certs | Complete |
| T46.1 | `src/synth_engine/shared/tls.py` — `TLSConfig` dataclass and `build_ssl_context()` helper | Complete |
| T46.2 | `docker-compose.mtls.yml` — overlay wiring mTLS on all data-plane connections | Complete |
| T46.2 | `src/synth_engine/shared/db.py` — psycopg2 and asyncpg mTLS connection wiring | Complete |
| T46.2 | `src/synth_engine/bootstrapper/dependencies/redis.py` — Redis mTLS client wiring | Complete |
| T46.3 | `src/synth_engine/shared/tls.py` — `get_cert_expiry_days()` and cert expiry Prometheus metric | Complete |
| T46.4 | `k8s/network-policies/` — Kubernetes NetworkPolicy manifests | Complete |
| T46.4 | This ADR | Complete |

### Data-Plane Connection Matrix

| Connection | Protocol | mTLS Mode | Cert Presented By |
|------------|----------|-----------|-------------------|
| app → pgbouncer | TCP 6432 | `verify-full` | app client cert |
| pgbouncer → postgres | TCP 5432 | mutual | pgbouncer client cert |
| app → redis | TCP 6379 (TLS) | mutual | app client cert |
| huey worker → redis | TCP 6379 (TLS) | mutual | app client cert |

### Certificate Layout

```
secrets/mtls/
├── ca.crt          # Root CA certificate (shared by all services for verification)
├── ca.key          # Root CA private key (keep offline after issuance)
├── app.crt         # App service leaf certificate
├── app.key         # App service private key
├── pgbouncer.crt   # PgBouncer leaf certificate
├── pgbouncer.key   # PgBouncer private key
├── postgres.crt    # PostgreSQL leaf certificate
├── postgres.key    # PostgreSQL private key
├── redis.crt       # Redis leaf certificate
└── redis.key       # Redis private key
```

---

## Consequences

### Positive

- All data-plane inter-container connections are encrypted and mutually
  authenticated when `MTLS_ENABLED=true`.
- The opt-in overlay model preserves CI and development ergonomics — no cert
  infrastructure required for non-production runs.
- The NetworkPolicy manifests add a defence-in-depth layer for Kubernetes
  deployments, restricting which pods may initiate connections regardless of
  application-level controls.
- ADR-0029 Gap 7 is now fully closed. All nine gaps from the Phase 10 roast
  are resolved.

### Negative / Constraints

- Operators who enable mTLS must provision and rotate certificates. The cert
  generation script (`scripts/generate-mtls-certs.sh`) automates initial
  issuance; rotation requires a rolling restart.
- NetworkPolicy enforcement requires a compatible CNI plugin. Operators on
  kubenet or Flannel must migrate their CNI before the policies take effect.
- Certificate expiry monitoring is implemented (T46.3 Prometheus metric) but
  automated rotation is not. A future task should add cert-manager integration
  for zero-downtime rotation.

---

## Amendment: Prometheus Metrics Naming Convention (ADV-P46-05)

**Added:** 2026-03-22 (P47 advisory drain)

### Rationale

As Phase 46 introduced the first application-defined Prometheus metric
(`conclave_mtls_cert_expiry_days` in `src/synth_engine/shared/tls.py`), a
naming and labelling convention is required to prevent drift across future
phases. This amendment documents the binding convention for all Conclave Engine
Prometheus metrics.

### Metric Naming Rules

1. **Prefix**: All metrics MUST use the `conclave_` prefix.

   Correct: `conclave_synthesis_jobs_total`
   Incorrect: `synth_jobs_total`, `engine_synthesis_jobs_total`

2. **Snake case**: Metric names use lowercase `snake_case` after the prefix.

3. **Unit suffix**: Metrics that measure a specific unit MUST include the unit
   as a suffix, using Prometheus base units:
   - `_seconds` — durations
   - `_bytes` — sizes
   - `_total` — monotonically increasing counters (use `Counter` type)
   - `_days` — calendar-day gauges (e.g. cert expiry)
   - `_ratio` — dimensionless ratios between 0 and 1

4. **No double-underscore in name**: The `conclave_` prefix already acts as
   the namespace separator. Service sub-namespaces go before the measurement
   noun: `conclave_mtls_cert_expiry_days`, not `conclave__mtls__cert_expiry_days`.

### Label Conventions

| Label key | Usage | Example values |
|-----------|-------|----------------|
| `service` | Identifies the source service or module | `app`, `redis`, `pgbouncer`, `postgres` |
| `path` | HTTP path for request-scoped metrics | `/api/v1/synthesize`, `/health` |
| `status` | HTTP status class or outcome | `2xx`, `4xx`, `5xx`, `success`, `failure` |
| `job_id` | Synthesis job identifier (use sparingly — high cardinality) | UUID string |

**High-cardinality warning:** Labels whose value space is unbounded (e.g. raw
user IDs, full file paths, synthesis row counts) MUST NOT be used as label
values. High-cardinality labels cause Prometheus TSDB to generate a separate
time series per unique label combination, leading to memory exhaustion.

### Existing Metrics Inventory

| Metric | Type | Labels | Module | Introduced |
|--------|------|--------|--------|------------|
| `conclave_mtls_cert_expiry_days` | Gauge | `service` | `shared/tls.py` | T46.3 |

Future phases MUST add new metrics to this table when introducing them.

### Enforcement

This convention is enforced by code review. A future task should add a
`pytest` fixture that scans all registered Prometheus collectors and asserts:
- Every metric name starts with `conclave_`.
- No metric uses a forbidden high-cardinality label (e.g. raw UUIDs as label
  values without cardinality bounding).

---

## Status

**Implemented (Phase 46, T46.1–T46.4)**

ADR-0029 Gap 7 disposition updated from **Deferred** to **Implemented (Phase 46)**.

---

## References

- `docs/ARCHITECTURAL_REQUIREMENTS.md` §4.2 — source mTLS requirement
- ADR-0029 Gap 7 — original deferral and Phase 46 implementation summary
- `docs/backlog/deferred-items.md` TBD-03 — tracking item, now DELIVERED
- `scripts/generate-mtls-certs.sh` — T46.1 certificate generation
- `src/synth_engine/shared/tls.py` — T46.1 TLS configuration helpers
- `docker-compose.mtls.yml` — T46.2 mTLS overlay
- `src/synth_engine/shared/db.py` — T46.2 database mTLS wiring
- `src/synth_engine/bootstrapper/dependencies/redis.py` — T46.2 Redis mTLS wiring
- `k8s/network-policies/` — T46.4 Kubernetes NetworkPolicy manifests
- ADR-0042 — Artifact signing and key versioning (supply chain hardening complement)
- `docs/infrastructure_security.md` — host-level security controls (LUKS, IPC_LOCK, non-root)
