# ADR-0016: CLI Framework — click

**Status:** Accepted
**Date**: 2026-03-14
**Task**: P3.5-T3.5.4 — Bootstrapper Wiring & Minimal CLI Entrypoint

---

## Context

Task P3.5-T3.5.4 introduced a `conclave-subset` CLI entrypoint
(`src/synth_engine/bootstrapper/cli.py`) that allows operators to run the
subsetting pipeline without writing Python.  A CLI framework was required to
handle argument parsing, `--help` generation, option type coercion, and
testability.

Two candidates were evaluated:

| Criterion | `argparse` (stdlib) | `click >= 8.0.0, < 9.0.0` |
|-----------|---------------------|---------------------------|
| Dependency cost | Zero (stdlib) | One production dependency |
| `--help` generation | Manual or auto from ArgumentParser | Automatic, composable |
| Composable sub-commands | Possible but verbose | First-class (`@click.group`) |
| Test harness | No built-in CliRunner; requires subprocess or monkeypatching | `click.testing.CliRunner` built-in |
| Type coercion | Manual | Declarative (`type=int`, `type=click.Path`, etc.) |
| Phase 5 extensibility | New commands require more boilerplate | Sub-commands compose via `@cli.add_command` |
| CVE history | N/A (stdlib) | No known CVEs in 8.x as of 2026-03-14 |

---

## Decision

Use `click >= 8.0.0, < 9.0.0` as the CLI framework for the
`conclave-subset` entrypoint.

---

## Rationale

1. **CliRunner test support**: `click.testing.CliRunner` provides an
   in-process, isolation-friendly test harness that captures stdout/stderr
   and exit codes without spawning subprocesses.  This is essential for the
   unit tests mandated by T3.5 AC2 and enforced by the project's 95%
   coverage gate.  Replicating equivalent test infrastructure for `argparse`
   would require either subprocess invocation (fragile, slow, coverage-blind)
   or a custom in-process harness (non-trivial to implement correctly).

2. **Composable sub-commands**: Phase 5 will introduce additional CLI
   sub-commands (e.g., `conclave-profile`, `conclave-generate`).  `click`'s
   `@click.group` / `@cli.add_command` API makes this extension trivial.
   `argparse` sub-parsers are functional but require significantly more
   boilerplate for the same result.

3. **Security posture**: `click 8.x` is a pure Python package with no
   native extensions, no C extensions, and no network calls.  It has no
   known CVEs as of this writing.  It is actively maintained with a
   well-defined deprecation policy (major versions).

4. **Pin rationale**: The `< 9.0.0` upper bound prevents automatic
   adoption of a future breaking major version change.  The `>= 8.0.0`
   lower bound ensures the modern decorator-based API (introduced in 8.0)
   is available.  The pin is consistent with all other production
   dependencies in `pyproject.toml`.

---

## Air-Gap Bundling

`click` is a pure Python package with no native extensions or platform-
specific wheels.  It is bundled cleanly in the air-gap wheel archive via
`make build-airgap-bundle` (see `scripts/build_airgap.sh`).  No additional
system libraries or network access are required at runtime.

---

## Trade-offs

- **Production dependency cost**: `argparse` would have zero dependency
  cost.  The `click` dependency is justified by the mandatory CliRunner
  test harness and Phase 5 composability requirements.  This is a deliberate
  trade-off: testability and extensibility are higher priority than
  minimising the production dependency count for a developer-facing CLI.

- **Learning curve**: `click`'s decorator model differs from `argparse`.
  This is acceptable — the project already uses FastAPI (also decorator-
  based), and `click`'s API is well-documented.

---

## Alternatives Considered

| Alternative | Reason Rejected |
|---|---|
| `argparse` (stdlib) | No built-in CliRunner; test harness would require subprocess or custom in-process shim; sub-command composability is more verbose |
| `typer` | Wraps `click`; adds an additional abstraction layer with no material benefit over using `click` directly; thinner dependency footprint without `typer` |
| `docopt` | Unmaintained; last release 2014; not acceptable for a security-focused project |
