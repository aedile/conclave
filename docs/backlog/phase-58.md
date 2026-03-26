# Phase 58 — Refactoring & Quality Hardening

**Goal**: Address the structural refactoring priorities and test/documentation
quality findings from the 2026-03-26 staff-level audit. These items reduce
cognitive load, improve type safety, and strengthen test efficacy.

**Prerequisite**: Phase 57 merged.

**Source**: Staff-level security audit (2026-03-26), justified scoring across
7 categories — maintainability (7/10), test efficacy (8/10), documentation
value (7/10), hidden technical debt (7/10).

---

## Refactoring Items — Full List

### Tier 1: Type Safety & Code Quality

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-01 | Replace `Any` types with TYPE_CHECKING Protocols | `dp_engine.py:138-150` | Type safety at DP boundary |
| RQ-02 | Eliminate double JWT decode | `auth.py:338-357` | Performance + reduced complexity |
| RQ-03 | Replace `inspect.getsource()` tests | `test_ssrf_fail_closed.py:148-185` | Refactor-resilient tests |
| RQ-04 | Replace structural pass-with-pass tests | `test_bootstrapper_wiring.py:32-76` | Meaningful behavioral coverage |

### Tier 2: File Decomposition

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-05 | Split audit.py (721 LOC) | `shared/security/audit.py` | Reduce per-file cognitive load |
| RQ-06 | Split models.py (694 LOC) | `synthesizer/storage/models.py` | Separate artifact from unpickler |
| RQ-07 | Group ConclaveSettings into nested sub-models | `shared/settings.py` | Discoverability, 40+ fields → grouped |

### Tier 3: Documentation Cleanup

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-08 | Deduplicate settings.py class docstring | `settings.py:89-165` | Remove 76 lines of duplication |
| RQ-09 | Compress verbose module docstrings | `auth.py:1-58`, `health.py:1-78`, `dp_engine.py:55-108` | Reduce scroll-past noise |
| RQ-10 | Move response schemas from docstrings to OpenAPI | `health.py:37-46` | Single source of truth |

### Tier 4: Test Infrastructure

| ID | Refactor | Location | Yield |
|----|----------|----------|-------|
| RQ-11 | Add Hypothesis property-based tests | HMAC signing, SSRF validation | Edge case coverage |
| RQ-12 | Track `# type: ignore` reduction | 35 across 22 files | Systematic type safety improvement |

---

## T58.1 — Replace Any Types in DPTrainingWrapper

**Priority**: P3 — Type safety.

### Context & Constraints

1. `dp_engine.py:138,144,148-150`: Five `Any` types for `_privacy_engine`,
   `wrapped_module`, `_wrapped_optimizer`, `_wrapped_dataloader`, and related
   PyTorch/Opacus objects.
2. These are optional dependencies — not always installed.
3. Fix: Use `TYPE_CHECKING` guard with `from __future__ import annotations`:
   ```python
   if TYPE_CHECKING:
       from opacus import PrivacyEngine
       from torch.nn import Module
       from torch.optim import Optimizer
       from torch.utils.data import DataLoader
   ```
4. Type as `PrivacyEngine | None`, `Module | None`, etc.

### Acceptance Criteria

1. Zero `Any` types for PyTorch/Opacus objects in `dp_engine.py`.
2. `mypy src/` passes.
3. No runtime dependency on opacus/torch for type checking.
4. Full gate suite passes.

---

## T58.2 — Eliminate Double JWT Decode

**Priority**: P3 — Performance + clarity.

### Context & Constraints

1. `auth.py:338-357`: `require_scope()` re-decodes the JWT that
   `get_current_operator` already decoded and verified.
2. Fix: Store decoded claims on `request.state.jwt_claims` in
   `get_current_operator`. Read from `request.state` in `require_scope`.
3. This eliminates redundant HMAC verification per scope-protected request.

### Acceptance Criteria

1. JWT decoded exactly once per request, not twice.
2. `request.state.jwt_claims` populated by `get_current_operator`.
3. `require_scope` reads claims from `request.state`, not by re-decoding.
4. All auth tests pass unchanged (behavioral equivalence).
5. Full gate suite passes.

---

## T58.3 — Replace Fragile Source-Inspection Tests

**Priority**: P4 — Test quality.

### Context & Constraints

1. `test_ssrf_fail_closed.py:148-185`: Uses `inspect.getsource()` to verify
   specific strings in production source code. Breaks on any formatting change.
2. `test_bootstrapper_wiring.py:32-76`: Six tests assert `callable()` and
   `__name__` — pass with `def fn(): pass`.
3. `test_bootstrapper_wiring.py:104-114`: Idempotency tests verify no exception
   but not correctness of registration state.
4. Fix: Replace source-inspection with behavioral mocks. Replace structural
   tests with behavioral assertions.

### Acceptance Criteria

1. Zero `inspect.getsource()` assertions in test suite.
2. SSRF strict/lenient verified by mocking `validate_callback_url` and checking
   call arguments (strict=True at registration, strict=False at delivery).
3. Wiring structural tests replaced with behavioral tests that verify IoC
   registration state after `wire_all()`.
4. Zero test function deletion (replace, don't delete).
5. Full gate suite passes.

---

## T58.4 — Split audit.py and models.py

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `shared/security/audit.py` (721 LOC): Covers v1/v2/v3 signatures, chain
   management, anchor resume, singleton, and key loading.
2. `synthesizer/storage/models.py` (694 LOC): Covers RestrictedUnpickler,
   SynthesizerModel Protocol, ModelArtifact, signing key validation, format
   detection, and Prometheus counters.
3. Fix:
   - Split `audit.py` → `audit_logger.py` (chain + events), `audit_signatures.py`
     (v1/v2/v3 signing/verification), `audit_singleton.py` (get/reset).
   - Split `models.py` → `artifact.py` (ModelArtifact), `restricted_unpickler.py`
     (RestrictedUnpickler + allowlists + SynthesizerModel Protocol).
4. Re-export from `__init__.py` for backward compatibility.

### Acceptance Criteria

1. No file exceeds 400 LOC after split.
2. All existing imports continue to work (re-exports).
3. All tests pass without modification.
4. Full gate suite passes.

---

## T58.5 — Group ConclaveSettings into Sub-Models

**Priority**: P5 — Maintainability.

### Context & Constraints

1. `settings.py` has 40+ fields in a single flat class.
2. Fix: Group into nested Pydantic sub-models:
   - `TLSSettings` (tls_cert_path, tls_key_path, mtls_*)
   - `RateLimitSettings` (general_limit, burst_limit, etc.)
   - `RetentionSettings` (job_retention_days, artifact_retention_days, etc.)
   - `WebhookSettings` (webhook_delivery_timeout_seconds, etc.)
   - `AnchorSettings` (anchor_backend, anchor_file_path, anchor_every_*)
3. Fields accessed via `settings.tls.cert_path` instead of `settings.tls_cert_path`.

### Acceptance Criteria

1. Settings grouped into 5+ sub-models.
2. All call sites updated.
3. `.env.example` still works (Pydantic nested model env var prefix).
4. Full gate suite passes.

---

## T58.6 — Documentation Deduplication

**Priority**: P6 — Documentation.

### Context & Constraints

1. `settings.py:89-165`: Class docstring duplicates all Field descriptions.
2. `auth.py:1-58`: 58-line module docstring.
3. `health.py:1-78`: 78-line module docstring with embedded JSON schema.
4. `dp_engine.py:55-108`: Constructor params duplicated.
5. Fix: Remove field-by-field restatement from class docstrings. Move response
   schemas to OpenAPI metadata. Compress module docstrings to security-relevant
   rationale only.

### Acceptance Criteria

1. No class docstring restates Field descriptions.
2. Response schemas in OpenAPI, not Python comments.
3. Module docstrings ≤30 lines (security rationale only).
4. Constructor param docs appear once, not twice.
5. Full gate suite passes.

---

## T58.7 — Property-Based Testing (Hypothesis)

**Priority**: P4 — Test quality.

### Context & Constraints

1. HMAC signing: arbitrary field content (including pipe characters, null bytes,
   Unicode, multi-GB strings) should never produce collisions in v3 format.
2. SSRF validation: arbitrary IP addresses in blocked ranges should always be
   rejected; arbitrary safe IPs should always pass.
3. Fix: Add `hypothesis` to dev dependencies. Write property-based tests for
   HMAC v3 signing and SSRF validation.

### Acceptance Criteria

1. `hypothesis` added to dev dependencies.
2. Property test: v3 HMAC signing with arbitrary st.text() fields never
   produces collisions when fields differ.
3. Property test: SSRF validation rejects all RFC 1918, loopback, link-local
   addresses regardless of encoding (IPv4, IPv6, mapped).
4. Full gate suite passes.

---

## Task Execution Order

```
T58.1 (Any types) ──────────────────┐
T58.2 (JWT double-decode) ──────────┼──> quick wins, parallel
T58.3 (fragile tests) ─────────────┘
T58.4 (file splits) ───────────────> depends on T58.1 (dp_engine types)
T58.5 (settings sub-models) ───────> independent
T58.6 (doc dedup) ─────────────────> independent, any time
T58.7 (Hypothesis tests) ──────────> independent, any time
```

---

## Phase 58 Exit Criteria

1. Zero `Any` types in `dp_engine.py`.
2. JWT decoded exactly once per request.
3. Zero `inspect.getsource()` in tests.
4. `audit.py` and `models.py` each split into ≤400 LOC files.
5. Settings grouped into nested sub-models.
6. Docstring duplication eliminated.
7. Property-based tests for HMAC and SSRF.
8. Exception handler boilerplate replaced with data-driven loop.
9. `extra="ignore"` replaced with `extra="forbid"` or warning.
10. Failed v1/v2 HMAC verification attempts logged.
11. `ClassVar` annotations on VaultState class attributes.
12. Broad `except Exception` in wiring.py narrowed.
13. All quality gates pass.
14. Review agents pass for all tasks.
