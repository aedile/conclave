# Phase 86 — Horizontal Scaling & Kubernetes Deployment

**Tier**: 8 (Enterprise Scale)
**Goal**: Validate and enable multi-node deployment via Kubernetes with horizontal pod
autoscaling for both the API and worker layers.

**Dependencies**: Phase 79 (multi-tenancy), Phase 75 (Redis-backed circuit breaker and
multi-worker safety — already done)

---

## Context & Constraints

- Current deployment: single-node Docker Compose with N uvicorn workers + 1 Huey worker.
- Phase 75 added Redis-backed circuit breaker, grace period clock, and Prometheus
  multiprocess mode — these were designed for multi-worker but not tested multi-node.
- Kubernetes deployment requires: liveness/readiness probes (exist), resource limits,
  shared artifact storage, distributed job queue coordination.
- MinIO is already in docker-compose for artifact storage — it naturally scales to
  multi-node since all pods write/read from the same bucket.
- The vault unseal state is currently in-process. In a multi-pod deployment, each pod
  must be unsealed independently OR the unseal state must be shared (Redis).

---

## Tasks

### T86.1 — Kubernetes Manifests (Helm Chart)

**Files to create**:
- `deploy/helm/conclave/` (new directory)
- `deploy/helm/conclave/Chart.yaml`
- `deploy/helm/conclave/values.yaml`
- `deploy/helm/conclave/templates/deployment.yaml`
- `deploy/helm/conclave/templates/service.yaml`
- `deploy/helm/conclave/templates/hpa.yaml`
- `deploy/helm/conclave/templates/configmap.yaml`
- `deploy/helm/conclave/templates/secrets.yaml`

**Acceptance Criteria**:
- [ ] Helm chart deploys: API (N replicas), Huey workers (M replicas), Redis, PostgreSQL
- [ ] Configurable replica counts via `values.yaml`
- [ ] HPA based on CPU and request rate (Prometheus metrics)
- [ ] Resource requests and limits for all pods
- [ ] Liveness probe: `/health/live` (existing)
- [ ] Readiness probe: `/health/ready` (existing — checks DB, Redis, MinIO)
- [ ] Secrets via Kubernetes secrets (not ConfigMap)
- [ ] PodDisruptionBudget for API pods (at least 1 always available)
- [ ] NetworkPolicy: API pods can reach Redis, PostgreSQL, MinIO; no cross-pod API traffic

### T86.2 — Distributed Job Queue

**Files to modify**:
- `shared/task_queue.py`
- `modules/synthesizer/jobs/tasks.py`

**Acceptance Criteria**:
- [ ] Huey workers across multiple pods coordinate via Redis (already the case — verify)
- [ ] Job locking: only one worker processes a given job (Redis-based lock with TTL)
- [ ] Worker crash recovery: stale locks expire, job re-queued after TTL
- [ ] Job progress visible from any API pod (stored in Redis/PostgreSQL, not in-process)
- [ ] Test: kill a worker pod mid-job → job transitions to FAILED or retries (not stuck)

### T86.3 — Shared Vault Unseal State

**Files to modify**:
- `shared/security/vault.py`
- `shared/settings.py`

**Acceptance Criteria**:
- [ ] Vault unseal state optionally stored in Redis (`VAULT_STATE_BACKEND=redis|memory`)
- [ ] When using Redis backend: unseal on one pod unseals all pods
- [ ] When using memory backend: each pod must be unsealed independently (current behavior)
- [ ] KEK is never stored in Redis — only the sealed/unsealed boolean and salt
- [ ] Each pod derives its own KEK from the shared passphrase (passphrase transmitted once via API, each pod runs PBKDF2 independently)
- [ ] ADR documenting the Redis vault state tradeoffs (convenience vs attack surface)

### T86.4 — Multi-Node Load Test

**Files to create**:
- `scripts/load_test_k8s.py` (new)

**Acceptance Criteria**:
- [ ] Deploy 4-pod setup (2 API, 2 worker) via Helm chart in local k3s/kind
- [ ] Run concurrent multi-tenant jobs: 3 orgs, 2 jobs each, simultaneously
- [ ] Verify: no tenant data leakage, all jobs complete, epsilon budgets correct per org
- [ ] Verify: HPA scales up under load, scales down after load subsides
- [ ] Verify: killing a worker pod mid-training → job fails gracefully, worker replaced
- [ ] Results documented in `docs/LOAD_TEST_RESULTS.md` (append, don't overwrite)

---

## Testing & Quality Gates

- All existing tests pass unchanged (backward compatibility with single-node)
- Helm chart linted: `helm lint deploy/helm/conclave/`
- Integration test: deploy to kind, run synthesis job, verify output
- Chaos test: kill pods during job execution, verify recovery
- Network policy test: API pod cannot directly reach another API pod
- Vault state test: unseal via Redis, verify all pods report unsealed
