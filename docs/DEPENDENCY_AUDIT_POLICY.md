# Conclave Engine — Dependency Audit Policy

Policy for discovering, reporting, and remediating known vulnerabilities in the dependency tree.

---

## 1. Tooling

### `pip-audit`

Required dev dependency. Queries the [PyPA Advisory Database](https://github.com/pypa/advisory-database) and [OSV](https://osv.dev) for CVEs affecting resolved versions.

```bash
poetry run pip-audit           # connected environment
poetry run pip-audit --local   # air-gapped (requires pre-downloaded database)
```

### `cyclonedx-bom`

Generates a CycloneDX SBOM listing all resolved dependencies with version and license information.

```bash
poetry run cyclonedx-py poetry > sbom.json
```

---

## 2. When to Run

| Trigger | Action |
|---------|--------|
| Adding a new dependency | Run before committing `pyproject.toml` |
| Upgrading a dependency | Run after updating the lock file |
| Weekly (if CI budget allows) | Full scan of the resolved lock file |
| Before any production release | Full scan; no unresolved HIGH or CRITICAL findings |
| After a public CVE disclosure | Immediate scan |

---

## 3. Severity Tiers

| Severity | Response Time | Action |
|----------|--------------|--------|
| **CRITICAL** | 24 hours | Block deployment; patch or remove immediately |
| **HIGH** | 5 business days | Patch or find alternative; document exemption if not possible |
| **MEDIUM** | 30 days | Evaluate exploitability; patch if exploitable |
| **LOW** | Next maintenance | Note in RETRO_LOG; patch at next convenience window |

---

## 4. Exemption Process

An exemption may be granted when:

1. The vulnerable code path is unreachable in Conclave's deployment model.
2. No upstream patch or alternative exists yet.
3. The vulnerability affects a dev-only dependency never deployed in production.

**Requirements:**

- File an advisory row in `docs/RETRO_LOG.md` tagged `ADVISORY` with: CVE ID, affected package/version, justification, target resolution date.
- PM + at least one engineer sign-off.
- Exemptions do not carry over across major releases.

**Never exempt CRITICAL findings without a committed patch timeline.**

---

## 5. Adding a New Dependency — Checklist

1. **Justify**: prefer stdlib; document reason in `docs/DEPENDENCY_AUDIT.md`.
2. **Check CVEs**: `poetry run pip-audit --package <package>==<version>`.
3. **Check license**: confirm compatibility with the project license.
4. **Check maintenance**: unmaintained packages are a supply chain risk (see `torchcsprng`, ADR-0017 v2).
5. **Pin the version range**: no `*` for production deps.
6. **Add to `docs/DEPENDENCY_AUDIT.md`**: record purpose, runtime usage, group.
7. **Security review**: if the package handles crypto, network I/O, or filesystem access, flag for devops-reviewer in the PR.

---

## 6. Audit Commands

```bash
# Standard audit
poetry run pip-audit

# JSON output for CI
poetry run pip-audit --format json --output pip-audit-report.json

# With documented exemption
poetry run pip-audit --ignore-vuln GHSA-xxxx-xxxx-xxxx

# Generate SBOM
poetry run cyclonedx-py poetry > sbom-$(date +%Y%m%d).json
```

---

## 7. Integration with Quality Gates

`pip-audit` is not in pre-commit hooks (requires network or a pre-cached database not available in the hook environment). It runs as a manual gate.

**Manual gate**: `pip-audit` must pass (exit 0) before any PR merges to `main`. If an exemption applies, document it in `RETRO_LOG.md` and use `--ignore-vuln <CVE-ID>`.

**Future**: wire into a weekly scheduled CI job against the pinned lock file when GitHub Actions budget is restored.

---

## 8. References

- `docs/DEPENDENCY_AUDIT.md` — Full audit table
- `pyproject.toml` — Canonical version pins
- `docs/adr/ADR-0018-psutil-ram-introspection.md` — Example single-dependency ADR
- `docs/adr/ADR-0031-pgbouncer-image-substitution.md` — Supply chain concern example (ADV-015)
- [pip-audit documentation](https://pypi.org/project/pip-audit/)
- [Python Packaging Advisory Database](https://github.com/pypa/advisory-database)
