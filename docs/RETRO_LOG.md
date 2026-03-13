# Conclave Engine — Retrospective Log

Living ledger of review retrospective notes and open advisory items.
Updated after each task's review phase completes.

---

## Open Advisory Items

Advisory findings without a resolved target task are tracked here.
Drain (delete) rows when their target task is completed.

| ID | Source | Target Task | Advisory |
|----|--------|-------------|----------|
| ADV-001 | QA R4 | Task 1.1 — CI/CD Pipeline | Coverage gate (`--cov-fail-under=90`) is not enforced in the bootstrap CI pipeline because `pytest-cov` requires Poetry. Must be added to CI in Task 1.1 when `pyproject.toml` is initialized. |
| ADV-002 | QA R3 | Task 1.1 — CI/CD Pipeline | `VERIFICATION_QUERIES[collection_name]` in `seed_chroma.py` is an unguarded dict key lookup. If `SEEDING_MANIFEST` and `VERIFICATION_QUERIES` diverge, a `KeyError` surfaces at runtime with no test coverage. Recommend a shared data structure or startup assertion in Task 1.1 refactor. |
| ADV-003 | DevOps R2/R3 | Task 1.1 — CI/CD Pipeline | `chromadb` is installed ad-hoc via `pip` in the bootstrap phase. Once `pyproject.toml` is created in Task 1.1, `chromadb` must be declared with a pinned version range and `pip-audit` added to the CI pipeline. |
| ADV-004 | DevOps R3 | Task 1.1 — CI/CD Pipeline | `bandit` cannot scan `scripts/` because `pyproject.toml` does not exist. Once Poetry is initialized, extend `bandit` scan scope to include `scripts/*.py` alongside `src/`. |

---

## Task Reviews

---

### [2026-03-13] P0.6 — Autonomous Agile Environment Provisioning

**QA** (Round 3 — PASS):
This second-round review confirms genuine improvement over round 1: the `print`-to-logger migration, the infinite-loop guard in `chunk_document`, and the `validate_env_file` security gate in `pre_tool_use.sh` are all solid, well-tested work. The pattern worth flagging for future PRs is the asymmetry between `init_chroma.main()` and `seed_chroma.main()`: one has tests and defensive error handling around PersistentClient, the other has neither. This kind of structural sibling-file divergence tends to persist through a codebase when scripts are written incrementally — the team should consider a shared testing fixture or base pattern for ChromaDB-touching scripts so the defensive idioms don't have to be re-invented (and re-reviewed) each time. The weak `assert count > 0` on line 107 of test_seed_chroma.py is a small but real signal that test assertions were not reviewed with the same rigor as the implementation code; establishing a convention of asserting exact return values (not just positivity) in future PRs will improve regression sensitivity.

**UI/UX** (Round 4 — SKIP):
This is the fourth consecutive round with no UI surface to review, which is consistent with the project still being in its infrastructure and tooling phases. The pattern is worth noting positively: the team is building out CI, pre-commit gates, and quality scaffolding before any user-facing code exists. From an inclusive design standpoint, this is the right order of operations — accessibility is far easier to enforce when the pipeline is in place to catch regressions before they land. The open risk remains that when templates and interactive elements do arrive, there will be pressure to move quickly; the CI pipeline added in this round should be extended at that point to include an automated accessibility linter (such as axe-core or pa11y) so that WCAG 2.1 AA compliance is a machine-checked gate, not just a manual review step.

**DevOps** (Round 3 — PASS):
The Round 3 fixes were clean and precise — both ImportError guards were correctly converted to `sys.stderr.write()` with no residual `print()` calls, and the credential variable substitution pattern in `worktree_create.sh` is exactly right. The security posture of this diff is strong for a pre-framework bootstrap phase: no secrets, no PII, no bypass flags, and the `.env.local` validation in `pre_tool_use.sh` shows deliberate defense-in-depth thinking (rejecting command substitution in sourced files is non-trivial and is the right call). The one open operational risk is the absent CI pipeline — with tests now in the repository, there is no automated guard ensuring they stay green across branches. That gap should be closed at the start of Phase 1 alongside `pyproject.toml`, not deferred further; the longer CI is absent, the more likely a regression will slip into `main` undetected.
