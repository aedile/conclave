# Phase 53 — Mutation Testing & Advisory Drain

**Goal**: Establish working mutation testing on Python 3.14, close the
Constitution Priority 4 programmatic gate gap, and drain all actionable open
advisories.

**Prerequisite**: Phase 50 merged (security fixes). Phase 52 is independent
(demo/benchmark suite) and does not block this phase.

**ADR**: T53.1 requires a new ADR (cosmic-ray adoption or dual-interpreter
strategy — technology substitution per Rule 6). T53.2 may amend ADR-0048
(audit trail anchoring) if signature format changes.

**Source**: Staff-level architecture review, 2026-03-23 — mutation testing
gap identified as highest-priority remediation. ADR-0052 re-evaluation
trigger (c): "An alternative mutation tool is evaluated and found compatible
with Python 3.14."

---

## T53.1 — Mutation Testing: Evaluate cosmic-ray, Adopt or Fallback

**Priority**: P0 — Constitution Priority 4 / Priority 0.5. The mutation
testing gate (ADR-0047) is non-functional. ADR-0052 accepted the gap
temporarily; this task closes it.

### Context & Constraints

1. `mutmut 3.x` crashes with SIGSEGV on CPython 3.14 due to its in-process
   trampoline mechanism. All 200 mutants exit with signal -11. Mutation scores
   are meaningless (ADR-0052).
2. `cosmic-ray 8.4.4` (latest, Feb 2026) uses subprocess isolation per mutant
   — no trampoline. It mutates at the AST level and runs each mutant in a
   fresh subprocess. This architecture avoids the SIGSEGV root cause entirely.
3. cosmic-ray does not declare Python 3.14 in its PyPI classifiers (up to
   3.13), but its subprocess-based approach has no known 3.14 incompatibility.
   The spike must verify this empirically.
4. If cosmic-ray works on 3.14: adopt it, wire into CI, supersede ADR-0052.
   Rule 6 requires an ADR for the tool substitution.
5. If cosmic-ray does NOT work on 3.14: fall back to running mutmut under a
   Python 3.13 interpreter for mutation runs only, while the rest of the
   project remains on 3.14. This requires a `tox` or `nox` configuration to
   manage the dual-interpreter setup. An ADR is still required.
6. Either path must produce a real, verifiable mutation score for
   `shared/security/` and `modules/privacy/`. The ADR-0047 threshold (60%,
   targeting 70% by Phase 55) must be enforceable.
7. The CI gate must block merges when mutation score drops below threshold.
8. The existing 19 manual hardening tests (`test_mutation_hardening_t49_5.py`)
   remain as defense-in-depth regardless of which tool is adopted.

### Acceptance Criteria

1. **Spike**: cosmic-ray installed in dev dependencies. Run against
   `shared/security/audit.py` (medium complexity, known mutant surface).
   Record: mutant count, kill count, surviving mutants, wall time, exit codes.
   Document whether all mutants execute cleanly (no SIGSEGV).
2. **If cosmic-ray works** (no SIGSEGV, mutation score computable):
   - Replace `mutmut` with `cosmic-ray` in `pyproject.toml` dev dependencies.
   - Configure cosmic-ray to target `shared/security/` and `modules/privacy/`.
   - Mutation score meets or exceeds 60% threshold (ADR-0047).
   - Wire `cosmic-ray` into `.github/workflows/ci.yml` as a blocking gate.
   - ADR documenting substitution: mutmut → cosmic-ray, rationale (Python 3.14
     compatibility), configuration, and threshold enforcement.
   - Update ADR-0052 status to `Superseded by ADR-XXXX`.
3. **If cosmic-ray fails** (SIGSEGV or other 3.14 incompatibility):
   - Configure dual-interpreter: `tox` or `nox` environment pinned to Python
     3.13 for mutation testing only.
   - `mutmut` runs under 3.13 interpreter against source code that targets
     3.14. Document any 3.13/3.14 syntax incompatibilities and mitigations.
   - Wire the dual-interpreter mutation gate into CI.
   - ADR documenting the dual-interpreter strategy, rationale, and risks.
   - Update ADR-0052 status to `Superseded by ADR-XXXX`.
4. Constitution enforcement table updated: Priority 4 mutation score row
   changes from `[ADVISORY — no programmatic gate]` to the actual CI gate
   command.
5. Full gate suite passes.

### Files to Create/Modify

- Modify: `pyproject.toml` (dependency swap or addition)
- Modify: `.github/workflows/ci.yml` (add mutation testing gate)
- Modify: `CONSTITUTION.md` (update enforcement table)
- Create: `docs/adr/ADR-XXXX-mutation-tool-adoption.md`
- Modify: `docs/adr/ADR-0052-mutmut-python-314-gap.md` (status → Superseded)
- Possibly create: `tox.ini` or `noxfile.py` (fallback path only)

---

## T53.2 — Audit HMAC: Include Details Field in Signature

**Priority**: P0 — Security. Closes ADV-P49-02.

### Context & Constraints

1. `shared/security/audit.py` computes HMAC-SHA256 signatures over a canonical
   representation of audit events. The `details` dict is excluded from the
   signed payload.
2. An attacker with write access to the WORM log store could modify `details`
   without invalidating the HMAC signature. The chain hash covers `details`
   transitively but is re-computable by an attacker who controls the store.
3. Fix: include a canonical serialization of `details` in the HMAC input.
   Use `json.dumps(details, sort_keys=True, separators=(",", ":"))` for
   deterministic serialization.
4. **Migration**: existing audit events have signatures computed without
   `details`. The verification routine must support both formats:
   - Events with a signature version prefix (e.g., `v2:`) verify with
     details included.
   - Events without the prefix verify with the legacy (no-details) format.
   - New events always use the v2 format.
5. This is a signature format change. Document in ADR-0048 amendment or a new
   ADR if the change is substantial enough.

### Acceptance Criteria

1. New audit events include `details` in HMAC computation.
2. Signature format includes a version discriminator (`v1:` legacy,
   `v2:` with details).
3. `verify_event()` correctly verifies both v1 (legacy) and v2 signatures.
4. New test: create event, tamper with `details` field only → v2 signature
   verification fails.
5. New test: legacy v1 event (no details in HMAC) → still verifies correctly.
6. New test: v2 event with matching details → verifies correctly.
7. New test: v2 event with `details=None` vs `details={}` produce different
   signatures (edge case).
8. Close ADV-P49-02 in RETRO_LOG.
9. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/security/audit.py`
- Modify: `tests/unit/test_audit.py`
- Modify or amend: `docs/adr/ADR-0048-audit-trail-anchoring.md`
- Modify: `docs/RETRO_LOG.md` (close ADV-P49-02)

---

## T53.3 — Programmatic Auth Coverage Gate

**Priority**: P0 — Constitution Priority 0.5. Closes the `[ADVISORY — no
programmatic gate]` annotation on the auth coverage row in the Constitution
enforcement table.

### Context & Constraints

1. CONSTITUTION.md line 107 documents: `[ADVISORY — no programmatic gate:
   test_all_routes_require_auth() does not exist]`.
2. The system has `AUTH_EXEMPT_PATHS` in `_exempt_paths.py`. Every route NOT
   in this list must require authentication.
3. A programmatic gate: enumerate all registered FastAPI routes, subtract
   the exempt paths, assert each remaining route returns 401 without a valid
   Bearer token.
4. This test must be an integration test (real FastAPI app, real ASGI
   transport) to catch middleware ordering bugs.
5. The test must be self-maintaining: if a new route is added without auth,
   the test fails. No manual route list to update.

### Acceptance Criteria

1. `tests/integration/test_all_routes_require_auth.py` created.
2. Test enumerates all routes from the FastAPI app's `app.routes`.
3. Test subtracts `AUTH_EXEMPT_PATHS` and health/readiness probe paths.
4. For each remaining route, sends a request with no Bearer token → asserts
   401 Unauthorized.
5. For each remaining route, sends a request with an invalid Bearer token →
   asserts 401 Unauthorized.
6. Test is self-maintaining: adding a new route without auth causes this test
   to fail (no manual route list).
7. Constitution enforcement table updated: auth coverage row changes from
   `[ADVISORY — no programmatic gate]` to
   `test_all_routes_require_auth() in tests/integration/`.
8. Full gate suite passes.

### Files to Create/Modify

- Create: `tests/integration/test_all_routes_require_auth.py`
- Modify: `CONSTITUTION.md` (update enforcement table)

---

## T53.4 — Redis TLS Promotion Deduplication

**Priority**: P2 — Maintainability. Closes ADV-P47-02.

### Context & Constraints

1. `_promote_redis_url_to_tls` logic is duplicated between
   `shared/tls/config.py` and the bootstrapper init. Both implementations
   rewrite `redis://` to `rediss://` when mTLS is enabled.
2. The duplication was intentional during T46.2 (mTLS implementation) to
   minimize cross-module coupling during a security-critical change.
3. Now that mTLS is stable and tested, consolidate into a single utility in
   `shared/tls/config.py` and have the bootstrapper import it.
4. This is a pure refactor — no behavior change. Existing tests must continue
   to pass without modification.

### Acceptance Criteria

1. Single `promote_redis_url_to_tls()` function in `shared/tls/config.py`.
2. Bootstrapper imports and calls the shared function instead of duplicating
   the logic.
3. No behavior change — existing TLS promotion tests pass without
   modification.
4. New test: verify the shared function handles edge cases (already `rediss://`,
   `None` URL, URL with port, URL with auth credentials).
5. Close ADV-P47-02 in RETRO_LOG.
6. Full gate suite passes.

### Files to Create/Modify

- Modify: `src/synth_engine/shared/tls/config.py`
- Modify: `src/synth_engine/bootstrapper/main.py` (or wherever the duplicate
  lives)
- Modify: `tests/unit/test_mtls_wiring.py` or `test_tls_config.py`
- Modify: `docs/RETRO_LOG.md` (close ADV-P47-02)

---

## Task Execution Order

```
T53.1 (mutation testing spike + adoption) ──────> foundation (longest task)
                                                    |
T53.2 (audit HMAC details) ────────────┐            |
T53.3 (auth coverage gate) ────────────┼──> parallel with T53.1
T53.4 (Redis TLS dedup) ──────────────┘
```

T53.1 is the longest task (spike + evaluation + CI wiring) and should start
immediately. T53.2, T53.3, and T53.4 are independent of each other and of
T53.1 — all four tasks can run in parallel.

---

## Phase 53 Exit Criteria

1. Mutation testing produces a real, verifiable score on Python 3.14.
2. Mutation score meets ADR-0047 threshold (≥60%) on `shared/security/` and
   `modules/privacy/`.
3. Mutation testing gate wired into CI and blocks merges below threshold.
4. Constitution enforcement table has zero `[ADVISORY — no programmatic gate]`
   rows for Priority 0 items.
5. Audit HMAC covers `details` field with backward-compatible versioned
   signatures.
6. All registered routes (except explicit exemptions) return 401 without auth
   — verified by self-maintaining integration test.
7. Redis TLS promotion logic consolidated into single shared utility.
8. ADV-P49-02 and ADV-P47-02 closed in RETRO_LOG.
9. ADR-0052 superseded by new mutation tool ADR.
10. All quality gates pass.
11. Review agents pass for all tasks.
