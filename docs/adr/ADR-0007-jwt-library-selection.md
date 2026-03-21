# ADR-0007: JWT Library Selection — PyJWT

**Status:** Accepted (supersedes python-jose decision of 2026-03-13)
**Date:** 2026-03-13

## Context

The zero-trust token-binding implementation (T2.3) requires a Python library for issuing and
verifying JWTs. The project originally adopted `python-jose[cryptography]`. A CVE was subsequently
published against `ecdsa`, an unmaintained transitive dependency of `python-jose`:

- **CVE-2024-23342** — `ecdsa` library (all versions including 0.19.1): Minerva timing
  side-channel on ECDSA nonce generation. Rated MEDIUM. `ecdsa` is unmaintained; no patch is
  expected. This CVE blocks `pip-audit` on every CI run as long as `python-jose` is present.

## Decision

Replace `python-jose[cryptography]` with `PyJWT[cryptography]` (version `>=2.10.0,<3.0.0`).

## Rationale

- `PyJWT` has no dependency on `ecdsa`. Its elliptic-curve support is provided entirely by the
  `cryptography` package (already present via `passlib[bcrypt]`), which is actively maintained.
- `PyJWT` ships its own PEP 561 type stubs. No separate `types-*` package is needed; the
  `types-python-jose` dev dependency has been removed.
- The public API used by this project (`encode`, `decode`, named exception classes) is
  identical in both libraries for HS256. The migration is a drop-in replacement.
- `ExpiredSignatureError` exists in both `jose` and `jwt.exceptions`; exception handling
  semantics are unchanged — `ExpiredSignatureError` is still caught before the general
  `PyJWTError` to give callers accurate error context.
- PyJWT 2.x `encode()` returns `str` directly (no cast required), which simplifies the
  implementation slightly.
- pip-audit baseline: clean (no known CVEs) as of 2026-03-13.

## Air-gap bundling implications

`PyJWT` and its `[cryptography]` extra are bundled as pre-built wheels in
`scripts/build_airgap.sh` via `docker save`. The `ecdsa` wheel is no longer included.
No additional wheels or network access are needed at runtime.

## Consequences

- Pin: `PyJWT = ">=2.10.0,<3.0.0"` with `extras = ["cryptography"]` in `pyproject.toml`.
- `types-python-jose` removed from dev dependencies; `PyJWT` stubs are included in the
  main package.
- Run `poetry run pip-audit` on every dependency update to monitor for new CVEs.
- If RS256/ES256 is required in production, no library change is needed — only a `JWTConfig`
  change to supply the PEM key and update the `algorithm` field.

---

## Amendment — T32.1 (Phase 32)

Implementation removed in T32.1 as unwired scaffolding — the module was defined but never
wired into the application (zero call sites in `bootstrapper/`). The design decision remains
sound and will be re-implemented when the trigger condition is met.

See `docs/backlog/deferred-items.md` TBD-06 for acceptance criteria and trigger condition.

---

## Amendment — T39.1/T39.2 (Phase 39)

Basic JWT authentication re-implemented in `bootstrapper/dependencies/auth.py` (T39.1) using
PyJWT with HS256 signing (this ADR's library selection decision). The re-implementation wires
`get_current_operator()` dependency injection into all authenticated routes. Token binding
(ADR-0008) remains deferred per TBD-07.
