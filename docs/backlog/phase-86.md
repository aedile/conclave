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
- **Vault unseal state in Redis — CRITICAL SECURITY DESIGN**:
  The Redis flag is ADVISORY ONLY. It cannot substitute for passphrase receipt.
  Each pod independently receives the passphrase via `POST /unseal` and derives KEK
  locally via PBKDF2. The Redis flag (`conclave:vault:sealed`) signals to newly-started
  pods that unsealing has occurred, prompting them to request the passphrase via the
  same API. If a pod has the Redis "unsealed" flag but no local KEK, it remains
  functionally sealed and logs a WARNING. This prevents a Redis-write attack from
  granting decryption capability.
- **Helm chart quality gates**: Helm charts are YAML, not Python. The project's Python
  quality gates (ruff, mypy, bandit) don't apply. Helm-specific gates: `helm lint --strict`,
  `helm template | kubectl apply --dry-run=client`. Pre-commit hook not applicable;
  Helm validation runs in CI only.
- **Load test classification**: T86.4 is a manual acceptance test documented in
  `docs/LOAD_TEST_RESULTS.md`. It is NOT automated in CI (k3s/kind setup is too
  heavy for GitHub Actions). The Helm chart is validated via `helm lint` in CI;
  the multi-node test is a human-run gate at phase boundary.

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
- [ ] All pod templates include `securityContext`: `runAsNonRoot: true`,
      `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true` (where applicable),
      `seccompProfile.type: RuntimeDefault`
- [ ] Helm validation: `helm lint --strict` + `helm template | kubectl apply --dry-run=client`

### T86.2 — Distributed Job Queue

**Files to modify**:
- `shared/task_queue.py`
- `modules/synthesizer/jobs/tasks.py`

**Acceptance Criteria**:
- [ ] Huey workers across multiple pods coordinate via Redis (verify existing behavior)
- [ ] Job locking: Redis `SET NX EX` lock per job_id — only one worker processes a given
      job. Verify whether Huey's built-in deduplication is sufficient or whether an
      additional lock layer is needed (document finding).
- [ ] Worker crash recovery: stale locks expire via TTL, job re-queued after TTL
- [ ] Job progress visible from any API pod (stored in Redis/PostgreSQL, not in-process)
- [ ] Test: kill a worker pod mid-job → job transitions to FAILED or retries (not stuck)
- [ ] Concurrent double-acquisition test: two workers simultaneously attempt to acquire
      the same job lock — only one succeeds. Integration test required.

### T86.3 — Shared Vault Unseal State

**Files to modify**:
- `shared/security/vault.py`
- `shared/settings.py`

**Acceptance Criteria**:
- [ ] Vault unseal state optionally stored in Redis (`VAULT_STATE_BACKEND=redis|memory`)
- [ ] Redis key: `conclave:vault:sealed` (boolean), `conclave:vault:salt` (base64)
- [ ] KEK is NEVER stored in Redis — only the sealed/unsealed boolean and salt
- [ ] **Redis flag is advisory only**: each pod independently receives the passphrase
      via `POST /unseal` and runs PBKDF2 locally. The Redis flag cannot substitute for
      passphrase receipt.
- [ ] **Guard**: if a pod reads `unsealed=True` from Redis but has no local KEK, it
      remains functionally sealed and logs a WARNING. Test required: verify that setting
      Redis flag without API unseal does not grant decryption capability.
- [ ] **Test: inspect Redis payload after unseal** — verify Redis contains only the
      boolean/salt, no key material (bytes, hex, or base64 of KEK). BLOCKER test.
- [ ] Unseal operation acquires Redis distributed lock (`SET NX EX`) to prevent
      concurrent cross-pod unseal races during PBKDF2 derivation window
- [ ] Redis unavailable before unseal: pods fail-closed (remain sealed). Test required.
- [ ] Redis unavailable after unseal: pods continue operating using in-process cached
      unseal state. Test required.
- [ ] When using memory backend: each pod must be unsealed independently (current behavior)
- [ ] ADR documenting Redis vault state tradeoffs, the advisory-only flag design, and
      the Redis-write attack mitigation
- [ ] `.env.example` updated with `VAULT_STATE_BACKEND`
- [ ] Update `docs/ASSUMPTIONS.md` A-013 (no longer single-process assumption)
- [ ] Add runbook: `docs/runbooks/vault-redis-state-recovery.md` — steps for recovering
      from sealed-all-pods state due to Redis unavailability

### T86.4 — Multi-Node Load Test (Manual Acceptance Test)

**Files to create**:
- `scripts/load_test_k8s.py` (new)

**Acceptance Criteria**:
- [ ] Deploy 4-pod setup (2 API, 2 worker) via Helm chart in local k3s/kind
- [ ] Run concurrent multi-tenant jobs: 3 orgs, 2 jobs each, simultaneously
- [ ] Verify: no tenant data leakage, all jobs complete, epsilon budgets correct per org
- [ ] Verify: HPA scales up under load, scales down after load subsides
- [ ] Verify: killing a worker pod mid-training → job fails gracefully, worker replaced
- [ ] Results documented in `docs/LOAD_TEST_RESULTS.md` (append, don't overwrite)
- [ ] This is a MANUAL acceptance test, not automated in CI. The Helm chart is validated
      via `helm lint --strict` in CI; the multi-node test is documented as a human-run
      gate at phase boundary.

---

## Testing & Quality Gates

- All existing tests pass unchanged (backward compatibility with single-node)
- Helm chart linted: `helm lint --strict` + `helm template | kubectl apply --dry-run=client`
- Integration test: Redis vault state — unseal via API on one process, verify Redis
  reflects state, verify second process sees unseal signal
- Integration test: Redis vault state — set Redis flag without API unseal, verify pod
  remains functionally sealed (BLOCKER — prevents Redis-write attack)
- Integration test: Redis payload inspection — after unseal, verify no KEK material in Redis
- Integration test: Redis unavailable before unseal → pods remain sealed
- Integration test: Redis unavailable after unseal → pods continue operating
- Chaos test: kill pods during job execution, verify recovery (manual, documented)
- Network policy test: API pod cannot directly reach another API pod (manual, documented)
- Concurrent lock acquisition test: two workers for same job → only one acquires
