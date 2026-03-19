# Phase 42 — Security Hardening, Key Rotation & Deployment Safety

**Goal**: Close remaining P1/P2 security findings from the 2026-03-19 audit:
artifact signing key versioning, HTTPS enforcement, CORS documentation, DP
quality benchmarks, and DDoS protection at the application layer.

**Prerequisite**: Phase 41 merged. Zero open advisories.

**ADR**: None required — incremental security hardening, no architectural decisions.

**Source**: Production Readiness Audit, 2026-03-19 — P1/P2 items 5-10.

---

## T42.1 — Implement Artifact Signing Key Versioning

**Priority**: P1 — Security. Artifact signing key has no rotation mechanism.
Compromised key means all artifacts are forgeable. Old artifacts become
unverifiable after key rotation.

### Context & Constraints

1. Currently, `ARTIFACT_SIGNING_KEY` is a single hex-encoded 32-byte key read
   from `ConclaveSettings`. HMAC-SHA256 signatures are computed in
   `job_finalization.py` and verified in `jobs_streaming.py`.

2. Fix: Implement key versioning:
   - Signature format changes from `HMAC(data)` to `KEY_ID || HMAC(data)`
     where `KEY_ID` is a 4-byte version identifier.
   - `ConclaveSettings` supports multiple signing keys:
     `artifact_signing_keys: dict[str, str]` mapping key_id → hex key.
   - `artifact_signing_key_active: str` identifies the current signing key.
   - Verification accepts any key in the key map (for backward compatibility
     during rotation windows).

3. Migration: existing unsigned or single-key-signed artifacts must remain
   verifiable. Use a sentinel `KEY_ID = 0x00000000` for legacy signatures.

4. Key rotation events must be logged to the WORM audit trail.

### Acceptance Criteria

1. Signatures include a key ID prefix.
2. Multiple signing keys supported concurrently.
3. Active key used for new signatures; any key verifies old signatures.
4. Legacy (pre-versioning) artifacts remain verifiable.
5. Key rotation logged to audit trail.
6. New tests: sign with key A → verify with key A, rotate to key B →
   old artifact still verifiable, new artifact signed with key B.
7. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/modules/synthesizer/job_finalization.py`
- Modify: `src/synth_engine/modules/synthesizer/models.py`
- Modify: `src/synth_engine/bootstrapper/routers/jobs_streaming.py`
- Modify: `src/synth_engine/shared/settings.py`
- Create: `tests/unit/test_key_versioning.py`

---

## T42.2 — Add HTTPS Enforcement & Deployment Safety Checks

**Priority**: P1 — Security. No application-level HTTPS enforcement. If TLS is
misconfigured, synthetic data streams unencrypted.

### Context & Constraints

1. The `jobs_streaming.py` download endpoint streams Parquet files in 64 KiB
   chunks. If deployment uses `http://` instead of `https://`, data is
   intercepted in flight.

2. Fix: In production mode (`is_production() == True`), add middleware that:
   - Checks `X-Forwarded-Proto` header (behind reverse proxy) or scheme
   - Rejects `http://` requests with 421 Misdirected Request
   - Allows `http://` in development mode

3. Also add a startup health check that warns if `CONCLAVE_SSL_REQUIRED=true`
   but no TLS certificate is configured.

4. Document HTTPS requirement in `OPERATOR_MANUAL.md` and
   `PRODUCTION_DEPLOYMENT.md`.

### Acceptance Criteria

1. Production mode rejects HTTP requests with 421.
2. Development mode allows HTTP.
3. Startup warning if SSL required but not configured.
4. Documentation updated.
5. New tests: HTTP in production → 421, HTTPS in production → allowed,
   HTTP in development → allowed.
6. Full gate suite passes.

### Files to Create/Modify

- Create: `src/synth_engine/bootstrapper/dependencies/https_enforcement.py`
- Modify: `src/synth_engine/bootstrapper/middleware.py`
- Modify: `docs/OPERATOR_MANUAL.md`
- Modify: `docs/PRODUCTION_DEPLOYMENT.md`
- Create: `tests/unit/test_https_enforcement.py`

---

## T42.3 — Run and Document DP Quality Benchmarks

**Priority**: P1 — Documentation. README states "Benchmarks Pending" since Phase 30.
`DP_QUALITY_REPORT.md:62,68-72` shows "placeholder" and "pending benchmark run".

### Context & Constraints

1. The command exists: `poetry run python3 scripts/benchmark_dp_quality.py`
2. The benchmark should measure:
   - Statistical fidelity (column distributions before/after synthesis)
   - Privacy guarantee (actual epsilon spent vs configured epsilon)
   - Utility metrics (ML model accuracy on synthetic vs real data)
3. Results must be written to `docs/DP_QUALITY_REPORT.md` with actual numbers.
4. README:157-170 must be updated to reflect actual benchmark results or
   removed if benchmarks show unsatisfactory quality.

### Acceptance Criteria

1. Benchmark script executed successfully.
2. `DP_QUALITY_REPORT.md` contains actual benchmark results (not placeholders).
3. README updated to reference actual results.
4. If benchmark reveals quality issues, they are documented honestly (not hidden).
5. Markdownlint passes.

### Files to Create/Modify

- Modify: `docs/DP_QUALITY_REPORT.md`
- Modify: `README.md`

---

## T42.4 — Document CORS Policy & Add DDoS Mitigation Notes

**Priority**: P2 — Documentation + Defense-in-depth. CORS is intentionally
absent (air-gapped deployment assumption) but undocumented. DDoS mitigation
relies on deployment infrastructure but has no application-layer guidance.

### Context & Constraints

1. **CORS**: The CSP middleware sets Content-Security-Policy but no CORS headers.
   This is correct for air-gapped/same-origin deployments. If a frontend on a
   different domain is deployed, CORS must be configured explicitly. Document this.

2. **DDoS**: `request_limits.py` handles body size and JSON depth. Rate limiting
   (T39.3) handles per-user/per-IP throttling. Additional protections needed:
   - Document recommended upstream protections (nginx rate limiting, cloud WAF)
   - Document connection timeout configuration
   - Document slow-read attack mitigation (uvicorn `--timeout-keep-alive`)

3. Add a `docs/SECURITY_HARDENING.md` guide covering:
   - CORS configuration (when and how to enable)
   - DDoS mitigation stack (application + infrastructure layers)
   - TLS configuration best practices
   - Vault passphrase management recommendations
   - Key rotation procedures

### Acceptance Criteria

1. `docs/SECURITY_HARDENING.md` covers CORS, DDoS, TLS, vault, key rotation.
2. OPERATOR_MANUAL references the hardening guide.
3. Markdownlint passes.

### Files to Create/Modify

- Create: `docs/SECURITY_HARDENING.md`
- Modify: `docs/OPERATOR_MANUAL.md`

---

## Task Execution Order

```
T42.1 (Key versioning) ────────────> parallel
T42.2 (HTTPS enforcement) ─────────> parallel
T42.3 (DP benchmarks) ─────────────> parallel (documentation only)
T42.4 (CORS/DDoS docs) ────────────> parallel (documentation only)
```

All four tasks are independent.

---

## Phase 42 Exit Criteria

1. Artifact signing supports key versioning with rotation.
2. HTTPS enforced in production mode.
3. DP quality benchmarks executed and documented with real numbers.
4. CORS policy and DDoS mitigation documented.
5. All quality gates pass.
6. Zero open advisories in RETRO_LOG.
7. Review agents pass for all tasks.
