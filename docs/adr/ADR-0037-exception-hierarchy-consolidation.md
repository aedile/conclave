# ADR-0037: Exception Hierarchy Consolidation — Vault and License Exceptions

> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

**Status:** Accepted
**Date:** 2026-03-18
**Deciders:** PM, Architecture Reviewer
**Task:** T34.1 — Unify Vault Exceptions Under SynthEngineError

---

## Context

Three vault exceptions and one license exception inherited the wrong base class,
causing them to bypass the domain exception middleware entirely.

**Affected exceptions before T34.1:**

| Exception | Old Base | Location | Problem |
|---|---|---|---|
| `VaultEmptyPassphraseError` | `ValueError` | `shared/security/vault.py` | Bypassed domain middleware; caught by any `except ValueError` handler |
| `VaultAlreadyUnsealedError` | `ValueError` | `shared/security/vault.py` | Same |
| `VaultConfigError` | `ValueError` | `shared/security/vault.py` | Same |
| `LicenseError` | `Exception` | `shared/security/licensing.py` | Bypassed domain middleware; fell through to catch-all 500 handler |
| `EpsilonMeasurementError` | *(new in T37.1)* | `shared/exceptions.py` | Added to shared hierarchy so `OPERATOR_ERROR_MAP` can reference it without crossing module boundaries. HTTP-safe. |

The domain exception middleware in `bootstrapper/errors/` package uses
`OPERATOR_ERROR_MAP` (defined in `bootstrapper/errors/mapping.py`), a
`dict[type[Exception], OperatorErrorEntry]`, to map domain exceptions to RFC 7807
operator-friendly responses.  This lookup is
based on `isinstance` checks against `SynthEngineError` subclasses.  Exceptions
that did not inherit `SynthEngineError` could not be matched and therefore
produced incorrect HTTP responses (500 Internal Server Error instead of 400/403).

The original `ValueError` bases in the vault exceptions were set as an
Architecture finding in P5-T5.3, to allow typed catching at the `/unseal`
endpoint instead of string-matching.  That goal is preserved: `lifecycle.py`
still catches these exceptions by their specific type, which works regardless
of base class.

---

## Decision

1. Move `VaultEmptyPassphraseError`, `VaultAlreadyUnsealedError`,
   `VaultConfigError`, and `LicenseError` into `shared/exceptions.py` as
   first-class members of the `SynthEngineError` hierarchy.

2. Remove the local definitions from `vault.py` and `licensing.py`; re-export
   the classes from those modules for backward compatibility.

3. Do NOT make `SynthEngineError` a subclass of `ValueError` to preserve
   backward compatibility.  That approach would pollute the domain hierarchy
   and allow vault exceptions to be silently caught by unrelated
   `except ValueError` handlers elsewhere in the codebase.  All affected catch
   sites must be audited and updated explicitly.

4. All new exceptions are added to `shared/exceptions.__all__`.

---

## Affected Catch Sites Audit

The following `except ValueError` occurrences were audited to determine whether
they previously caught vault exceptions:

| File | Line | Context | Catches vault exceptions? | Action required |
|---|---|---|---|---|
| `bootstrapper/lifecycle.py` | 142 | `except (ValueError, RuntimeError):` after audit event in `/unseal` | No — catches `ValueError` from `audit._parse_audit_key()` | None |
| `bootstrapper/routers/security.py` | 102 | `except ValueError as exc:` after audit event in `/security/crypto-shred` | No — same audit path | None |
| `bootstrapper/routers/security.py` | 183 | `except ValueError as exc:` after audit event in `/security/keys/rotate` | No — same audit path | None |
| `bootstrapper/cli.py` | 275, 282 | `except ValueError as exc:` catching `validate_connection_string()` | No — `validate_connection_string` raises `ValueError` unrelated to vault | None |
| `shared/telemetry.py` | 70 | `except ValueError:` in URL parsing | No | None |
| `bootstrapper/routers/jobs_streaming.py` | 116 | `except ValueError:` | No | None |
| `bootstrapper/dependencies/request_limits.py` | 231 | `except ValueError:` | No | None |
| `shared/security/audit.py` | 223 | `except ValueError as exc:` in hex parsing | No | None |
| `modules/synthesizer/jobs/job_finalization.py` | 95 | `except ValueError:` | No | None |

**Conclusion:** No catch sites were previously catching vault exceptions via
`except ValueError`.  The bootstrapper lifecycle and error map already catch
vault exceptions by their specific type (`VaultEmptyPassphraseError`,
`VaultAlreadyUnsealedError`, `VaultConfigError`), which works correctly
regardless of base class.  No catch sites required modification.

The `LicenseError` was previously caught by:
- `bootstrapper/routers/licensing.py` — catches `LicenseError` by specific type
- No `except Exception` sites were catching `LicenseError` directly

---

## Consequences

**Positive:**
- All vault and license exceptions are now members of the `SynthEngineError`
  hierarchy, ensuring they are handled by the domain exception middleware.
- `EpsilonMeasurementError` (T37.1) — added to `shared/exceptions.py` so that
  `bootstrapper/errors/mapping.py` can map it to an RFC 7807 response without
  importing from `modules/synthesizer/`.  HTTP-safe: maps to HTTP 500 with
  `type_uri="/problems/epsilon-measurement-failure"`.
- `OPERATOR_ERROR_MAP` can be extended to include `LicenseError` if needed
  without workarounds.
- `isinstance(exc, SynthEngineError)` now correctly identifies vault and
  license failures as intentional domain errors.
- The hierarchy is documented comprehensively in `shared/exceptions.py`.
- `EpsilonMeasurementError` is classified HTTP-safe and maps to HTTP 500
  (type: `/problems/epsilon-measurement-failure`) in `OPERATOR_ERROR_MAP`.
- Backward compatibility preserved: `vault.py` and `licensing.py` re-export
  all exception classes; all existing catch-by-type sites continue to work.

**Negative / Constraints:**
- `VaultEmptyPassphraseError`, `VaultAlreadyUnsealedError`, `VaultConfigError`
  no longer satisfy `isinstance(exc, ValueError)`.  Any code that relied on
  this (none found in audit above) would need to be updated.
- `LicenseError` no longer satisfies `isinstance(exc, Exception)` directly
  (it does transitively through `SynthEngineError`).  Any code doing an
  `except Exception` catch-all that relied on `LicenseError` NOT being a
  `SynthEngineError` would need to be updated (none found).

---

## Alternatives Considered

**A: Make `SynthEngineError` inherit `ValueError`.**
Rejected — this would allow ALL domain exceptions to be caught by any
`except ValueError` handler, creating subtle confusion and potentially
swallowing domain errors in unrelated error paths.

**B: Add `ValueError` as a second base to the vault exceptions (multiple inheritance).**
Rejected — multiple inheritance for exception hierarchies is fragile.
Python's MRO becomes non-trivial, and the goal (domain middleware handling)
is better achieved by the clean single-hierarchy approach.

**C: Keep exceptions in place; patch `OPERATOR_ERROR_MAP` to check by name.**
Rejected — this perpetuates the ADR-0033 duck-typing anti-pattern that
T26.2 explicitly removed.

---

## References

- ADR-0033: Cross-module exception detection by class name (the pattern this
  decision continues to supersede)
- `shared/exceptions.py` — updated exception taxonomy
- `bootstrapper/errors/` package — `OPERATOR_ERROR_MAP` in `bootstrapper/errors/mapping.py`, domain middleware in `bootstrapper/errors/middleware.py`
- `bootstrapper/lifecycle.py` — `/unseal` route exception handling
- `shared/security/vault.py` — vault exception definitions (now re-exports)
- `shared/security/licensing.py` — `LicenseError` definition (now re-exports)
- Architecture finding P5-T5.3 — original motivation for typed vault exceptions
- ADV-054 (P8-T8.3) — `LicenseError` must not carry HTTP status codes
