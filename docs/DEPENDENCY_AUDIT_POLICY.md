# Conclave Engine — Dependency Audit Policy

This document defines how the Conclave Engine project discovers, reports, and
remediates known vulnerabilities in its dependency tree. It covers `pip-audit`
usage, exemption criteria, and the cadence for ongoing audits.

---

## 1. Tooling

### `pip-audit`

`pip-audit` is listed as a required dev dependency in `pyproject.toml`:

```toml
[tool.poetry.group.dev.dependencies]
pip-audit = "*"
```

It queries the [Python Packaging Advisory Database (PyPA)](https://github.com/pypa/advisory-database)
and the [OSV database](https://osv.dev) to identify published CVEs affecting the
currently resolved dependency versions.

In an air-gapped environment, `pip-audit` requires a pre-downloaded vulnerability
database. Use the `--local` flag or supply a cached database file:

```bash
# Connected environment (standard)
poetry run pip-audit

# Air-gapped environment (requires offline database)
poetry run pip-audit --local
```

### `cyclonedx-bom`

`cyclonedx-bom` generates a CycloneDX Software Bill of Materials (SBOM) listing
all resolved dependencies with version and license information. This SBOM can be
provided to auditors or compliance reviewers.

```bash
poetry run cyclonedx-py poetry > sbom.json
```

---

## 2. When to Run

| Trigger | Action |
|---------|--------|
| Adding a new dependency | Run `pip-audit` before committing the `pyproject.toml` change |
| Upgrading an existing dependency | Run `pip-audit` after updating the lock file |
| Weekly (automated, if CI budget allows) | Full `pip-audit` scan of the resolved lock file |
| Before any production release | Full scan; no unresolved HIGH or CRITICAL findings permitted |
| After a public CVE disclosure affecting Python or a dependency | Immediate scan |

---

## 3. Severity Tiers and Response Times

| Severity | Response Time | Action |
|----------|--------------|--------|
| **CRITICAL** | Within 24 hours | Block deployment; escalate; patch or remove dependency immediately |
| **HIGH** | Within 5 business days | Patch or find an alternative; document exemption if patching is not possible |
| **MEDIUM** | Within 30 days | Evaluate exploitability in Conclave's threat model; patch if exploitable |
| **LOW** | Next scheduled maintenance | Note in RETRO_LOG; patch in next convenience window |

---

## 4. Exemption Process

An exemption may be granted when:

1. The vulnerable code path is not reachable in Conclave's deployment model (e.g.,
   a network-facing component in a library that Conclave uses only for local file I/O).
2. The upstream patch is not yet available and no alternative package exists.
3. The vulnerability affects a dev-only dependency (e.g., `pytest`, `ruff`) that
   is never deployed in production containers.

**Exemption requirements:**

- File an advisory row in `docs/RETRO_LOG.md` tagged `ADVISORY` with:
  - CVE identifier
  - Affected package and version
  - Justification (why the vulnerability is not exploitable in Conclave's context)
  - Target resolution date
- Get sign-off from the PM and at least one engineer.
- Exemptions do not carry over across major releases. Re-evaluate at each release.

**Never exempt CRITICAL severity findings without a patch timeline committed.**

---

## 5. Adding a New Dependency — Security Checklist

Before adding any new package to `pyproject.toml`:

1. **Justify the dependency**: prefer stdlib; document the reason in `docs/DEPENDENCY_AUDIT.md`.
2. **Check for known vulnerabilities**: `poetry run pip-audit --package <package>==<version>`.
3. **Review the license**: confirm compatibility with the project's license.
4. **Check download counts and maintenance status**: unmaintained packages are a
   long-term supply chain risk (see `torchcsprng` in ADR-0017 v2 as an example).
5. **Pin the version range** in `pyproject.toml`. Do not use `*` for production deps.
6. **Add to `docs/DEPENDENCY_AUDIT.md`**: record purpose, runtime usage, and group.
7. **Security review**: if the package handles cryptographic material, network I/O,
   or file system access, file it for devops-reviewer scrutiny in the PR.

---

## 6. Running a Full Audit

```bash
# Standard audit (connected environment)
poetry run pip-audit

# Audit with JSON output (for CI integration)
poetry run pip-audit --format json --output pip-audit-report.json

# Audit excluding dev group (production surface only)
poetry run pip-audit --ignore-vuln GHSA-xxxx-xxxx-xxxx  # use for documented exemptions only

# Generate SBOM for compliance submission
poetry run cyclonedx-py poetry > sbom-$(date +%Y%m%d).json
```

---

## 7. Integration with Quality Gates

`pip-audit` is not currently wired into the pre-commit hooks (it requires
network access or a pre-cached database that is not available in the hook
isolation environment). It runs as a manual gate.

**Manual gate requirement:** `pip-audit` must pass (exit code 0) before any PR
is merged into `main`. If findings are present and an exemption has been granted,
document the exemption in `RETRO_LOG.md` and use `--ignore-vuln` with the specific
CVE identifier to allow the gate to pass.

**Future automation:** when GitHub Actions budget is restored (see `CLAUDE.md`
Quality Gates section), wire `pip-audit` into a weekly scheduled CI job that
runs against the pinned lock file and notifies the team via its output artefact.

---

## 8. References

- `docs/DEPENDENCY_AUDIT.md` — Full audit table of all direct dependencies
- `pyproject.toml` — Canonical dependency specification and version pins
- `docs/adr/ADR-0018-psutil-ram-introspection.md` — Example of a single-dependency ADR
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md` — Example of supply chain concern (ADV-015)
- [pip-audit documentation](https://pypi.org/project/pip-audit/) (consult before air-gap deployment)
- [Python Packaging Advisory Database](https://github.com/pypa/advisory-database)
