# ADR-0027 — Bootstrapper Submodule Re-Export Pattern

**Date:** 2026-03-16
**Status:** Accepted
**Deciders:** Architecture Reviewer + PM
**Task:** P9-T9.3
**Resolves:** Architecture BLOCKER finding from P9-T9.3 review

---

## Context

Task P9-T9.3 decomposed `main.py` into focused submodules:

- `factories.py` — Synthesis and DP factory functions (`build_synthesis_engine`, `build_dp_wrapper`)
- `lifecycle.py` — Lifespan hooks and ops route registration (`_lifespan`, `_register_routes`, `UnsealRequest`)
- `middleware.py` — Middleware stack setup (`setup_middleware`)
- `router_registry.py` — Domain router and exception handler wiring (`_include_routers`, `_register_exception_handlers`)

The existing test suite patches names at the `synth_engine.bootstrapper.main` namespace (e.g.,
`@patch("synth_engine.bootstrapper.main.build_synthesis_engine")`). When a function moves to a
submodule, the patch target would need to change — unless the canonical name is preserved in
`main.py` via re-export.

The acceptance criterion AC3 for T9.3 states: *no existing test file may be modified*. This
constraint makes re-exporting a requirement, not an option.

---

## Decision

Names that are patched in tests at `synth_engine.bootstrapper.main.*` MUST be re-exported
from `main.py` using explicit import with a `# noqa: F401` suppression comment:

```python
from .submodule import name  # noqa: F401 — re-exported for test patches
```

The canonical implementation lives in the submodule. `main.py` holds only the re-export
binding. This preserves the patch target path without duplicating implementation.

### Scope of re-exports at time of writing

| Name | Canonical module | Reason for re-export |
|------|-----------------|----------------------|
| `build_synthesis_engine` | `bootstrapper.factories` | Patched in synthesis integration tests |
| `build_dp_wrapper` | `bootstrapper.factories` | Patched in DP integration tests |
| `UnsealRequest` | `bootstrapper.lifecycle` | Patched in vault seal/unseal tests |

### Docker-secrets cluster exception

The Docker-secrets cluster (`_read_secret`, `_SECRETS_DIR`, `_MINIO_ENDPOINT`,
`_EPHEMERAL_BUCKET`, `MinioStorageBackend`, `build_ephemeral_storage_client`) remains
implemented directly in `main.py` rather than being re-exported from a submodule.

Rationale: `_read_secret` closes over `_SECRETS_DIR` at the module level of its definition.
If `_read_secret` were moved to a submodule, patching `synth_engine.bootstrapper.main._SECRETS_DIR`
would not affect the closure in the submodule — the patch would have no effect. Re-exporting
`_read_secret` from `main.py` would leave the closure bound to the submodule's `_SECRETS_DIR`,
not the one being patched. Keeping the entire cluster co-located in `main.py` is the only
approach that preserves patch semantics without modifying tests.

---

## Consequences

**Positive:**
- Existing test patches require zero modification (AC3 satisfied).
- Each submodule has a single, coherent responsibility and can be read, tested, and maintained
  independently of `main.py`.
- The re-export pattern is self-documenting: the `# noqa: F401 — re-exported for test patches`
  comment explains why an otherwise-unused import is intentional.

**Negative / Constraints:**
- Future developers adding patchable names to bootstrapper submodules MUST re-export from
  `main.py` if existing tests patch `synth_engine.bootstrapper.main.X`. Forgetting this step
  will silently break patch targets with no import error at test collection time.
- The Docker-secrets cluster cannot be extracted from `main.py` without modifying tests or
  restructuring closure scope. This is an accepted constraint documented here.

**Process rule (enforced by architecture reviewer):**
When a PR moves a name out of `main.py` into a submodule, the architecture reviewer MUST
verify that every `@patch("synth_engine.bootstrapper.main.<name>")` occurrence in the test
suite is still valid. If the name is no longer present in `main.py` (either directly or via
re-export), the PR is a BLOCKER.

---

## References

- P9-T9.3 acceptance criterion AC3: no test file modifications
- ADR-0001: Modular monolith topology — canonical module boundary rules
- `synth_engine/bootstrapper/main.py` — re-export bindings at import block
