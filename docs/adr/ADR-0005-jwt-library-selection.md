# ADR-0005: JWT Library Selection — python-jose

**Status:** Accepted
**Date:** 2026-03-13

## Context

The zero-trust token-binding implementation (T2.3) requires a Python library for issuing and
verifying JWTs. Two primary candidates were evaluated: `python-jose[cryptography]` and `PyJWT`.

## Decision

Use `python-jose[cryptography]`.

Rationale:

- Supports both symmetric (HS256) and asymmetric (RS256/ES256) algorithms via the same API,
  allowing a future move to asymmetric keys without a library change.
- The `[cryptography]` extra uses the `cryptography` package (already in the dependency graph
  via passlib) rather than requiring a separate native extension.
- Named exception classes (`ExpiredSignatureError`, `JWTError`) enable precise exception
  handling; `ExpiredSignatureError` is caught before the general `JWTError` to give callers
  accurate error context.
- pip-audit baseline: no known CVEs at time of adoption (2026-03-13).

## Air-gap bundling implications

`python-jose` and its `[cryptography]` extra are bundled as pre-built wheels in
`scripts/build_airgap.sh` via `docker save`. No additional wheels or network access are needed
at runtime.

## Consequences

- Pin: `python-jose = ">=3.3.0,<4.0.0"` with `extras = ["cryptography"]` in `pyproject.toml`.
- Run `poetry run pip-audit` on every dependency update to monitor for new CVEs.
- If RS256/ES256 is required in production, no library change is needed — only a `JWTConfig`
  change to supply the PEM key and update the `algorithm` field.
