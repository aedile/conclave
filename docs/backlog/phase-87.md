# Phase 87 — Python 3.12 / 3.13 CI Matrix Testing

**Tier**: 8 (Enterprise Scale)
**Goal**: Expand CI test matrix to cover Python 3.12 and 3.13, ensuring compatibility
across all supported Python versions before Tier 8 exit.

**Dependencies**: None (can run in parallel with other Tier 8 phases)

---

## Context & Constraints

- Currently: CI runs exclusively on Python 3.14 (single-version matrix).
- Python 3.14 is still pre-release / not production-ready. Many enterprise deployments
  run 3.12 or 3.13. Testing only on 3.14 risks shipping code that fails on the versions
  customers actually use.
- Python 3.12 is the current LTS-equivalent (mainstream support). Python 3.13 added
  free-threaded mode and other runtime changes that may affect behavior.
- **Python 3.14 is explicitly excluded** from the supported version matrix — it is not
  production-ready. CI may continue running 3.14 as an informational job (allow-fail)
  but it must not be the only version tested.
- `pyproject.toml` `python` version constraint must be updated to reflect the supported
  range (e.g., `>=3.12,<3.14`).

---

## Tasks

### T87.1 — CI Matrix Expansion

**Files to modify**:
- `.github/workflows/ci.yml`

**Acceptance Criteria**:
- [ ] CI matrix includes Python 3.12 and 3.13 as required jobs
- [ ] Python 3.14 job is either removed or marked `continue-on-error: true`
- [ ] All 6 CI job definitions (security, lint, test, integration-test, trivy, sbom)
      run on the matrix versions where applicable
- [ ] Poetry install and dependency resolution succeeds on all matrix versions
- [ ] Matrix strategy uses `fail-fast: false` so all versions are tested even if one fails

### T87.2 — Version Compatibility Fixes

**Files to modify**: (as needed — discovered during CI runs)

**Acceptance Criteria**:
- [ ] All unit tests pass on Python 3.12
- [ ] All unit tests pass on Python 3.13
- [ ] All integration tests pass on Python 3.12
- [ ] All integration tests pass on Python 3.13
- [ ] No version-specific `sys.version` hacks — use feature detection or `typing_extensions`
- [ ] Any version-specific behavior documented in code comments

### T87.3 — pyproject.toml Version Bounds

**Files to modify**:
- `pyproject.toml`

**Acceptance Criteria**:
- [ ] `python` version constraint set to `>=3.12,<3.14` (or appropriate range)
- [ ] All dependencies compatible with Python 3.12+ (check `poetry lock` succeeds)
- [ ] `classifiers` updated to list supported Python versions

---

## Testing & Quality Gates

- All existing unit and integration tests must pass on Python 3.12 and 3.13
- Coverage threshold (95%) must be met on all matrix versions
- Static analysis (ruff, mypy, bandit, vulture) must pass on all versions
- No new dependencies added for version compatibility
