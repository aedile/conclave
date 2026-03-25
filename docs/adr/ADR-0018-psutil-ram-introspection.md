> **Amendment (Phase 56):** File paths updated to reflect synthesizer sub-package decomposition.

# ADR-0018 — psutil as RAM Introspection Library

**Date:** 2026-03-14
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** P4-T4.3a

---

## Context

The OOM pre-flight guardrail (`modules/synthesizer/training/guardrails.py`, T4.3a) must
determine available system RAM at runtime to evaluate whether a proposed training
job fits within safe memory bounds (85% of available memory).

Three candidate approaches were evaluated:

1. **`resource` stdlib module** — provides `resource.getrlimit()` for process
   resource limits, but does not expose current system-level available RAM.
   Not suitable: measures process limits, not available physical memory.

2. **`/proc/meminfo` direct read** — Linux-only; not portable to macOS or
   Windows development environments; fragile to parse; not acceptable for
   a codebase that runs in Docker on varied host OSes.

3. **`psutil`** — cross-platform process and system utilities library. Exposes
   `psutil.virtual_memory().available`, which returns the OS-reported available
   RAM in bytes. Already present in the integration test group as a
   `pytest-postgresql` transitive dependency; promoting it to the main group
   adds no new supply-chain vector.

---

## Decision

**Use `psutil.virtual_memory().available` for RAM introspection.**

`psutil` is the industry-standard library for this use case. It is already
present in the lock file (as an integration-group transitive dep), is
cross-platform (Linux, macOS, Windows), and has a stable API. The specific
call `psutil.virtual_memory().available` returns the memory that can be
given to processes without the system going into swap — the correct quantity
for an OOM guardrail.

**Version range:** `>=5.9.0,<8.0.0` — pins to the major-version series that
introduced the `available` field on `virtual_memory()` and excludes the next
hypothetical major version pending compatibility review.

---

## VRAM path

When `torch` is present and `torch.cuda.is_available()` returns `True`, the
guardrail uses GPU VRAM (`torch.cuda.get_device_properties().total_memory -
torch.cuda.memory_reserved()`) instead of RAM. `torch` is an optional import
(guarded by `importlib.util.find_spec`); `psutil` is used as the RAM fallback
when `torch` is absent or CUDA is unavailable. This layered approach avoids
making `torch` a hard production dependency of `guardrails.py`.

---

## Air-Gap Implications

`psutil` ships as a compiled wheel (C extension). The air-gap bundle
(`make build-airgap-bundle`) must include the `psutil` wheel for the target
platform (Linux/ARM64 or Linux/x86_64). The wheel is already captured in
`poetry.lock`; no additional bundling step is required beyond the existing
`cyclonedx-bom` SBOM generation.

---

## Consequences

- `psutil` moves from the integration group to the `[tool.poetry.dependencies]`
  main group. This increases the production image size by ~1 MB.
- `types-psutil` is added to the dev group for mypy coverage.
- No other modules depend on `psutil` directly; it is an internal detail of
  `guardrails.py` only.

---

## References

- `src/synth_engine/modules/synthesizer/training/guardrails.py` — the sole consumer of
  `psutil` in production code.
- ADR-0017 (`docs/adr/ADR-0017-synthesizer-dp-library-selection.md`) —
  documents the CTGAN + Opacus decision that T4.3a supports.
