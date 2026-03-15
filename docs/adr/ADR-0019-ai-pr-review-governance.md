# ADR-0019 — AI PR Review Governance

**Date:** 2026-03-15
**Status:** Accepted
**Deciders:** PM + Architect
**Task:** Advisory Drain Sprint (ADV-038)

---

## Context

The project uses four parallel specialized review subagents (qa-reviewer,
ui-ux-reviewer, devops-reviewer, architecture-reviewer) to evaluate every PR
before merge. The PM then spawns a `pr-reviewer` subagent that consolidates
findings and posts a structured summary comment on the GitHub PR.

The `pr-reviewer` subagent also has the ability to post a `gh pr review
--approve` approval via the GitHub CLI. This raises a governance question:
**should AI agents be permitted to post GitHub PR approvals?**

Without an explicit governance decision, this pattern is opaque — readers
of the git history and GitHub UI cannot tell whether a human or an AI agent
approved a PR. This ADR documents the boundaries of that authority.

---

## Decision

**AI-posted GitHub PR approvals are permitted under the following constraints:**

1. **Scope: Approve, never merge.**
   The pr-reviewer agent may post `gh pr review --approve`. It may NOT
   post `gh pr review --merge`, invoke `gh pr merge`, or otherwise trigger
   the merge action. Merges are performed by GitHub's auto-merge mechanism
   after all required status checks pass, or by a human operator.

2. **Prerequisites for approval.**
   The pr-reviewer agent MUST verify all of the following before posting an
   approval:
   - All four review commits (`review(qa):`, `review(devops):`,
     `review(ui-ux):`, `review(arch):` where structural) are present on the
     branch.
   - All required CI status checks are green (verified via `gh pr checks`).
   - No review commit has a `BLOCKER` finding that is unresolved.

3. **Escalation path when gates cannot be evaluated.**
   If any gate cannot be evaluated (e.g., CI checks still pending, a review
   commit is missing, or GitHub API returns an error), the pr-reviewer agent
   MUST post a structured comment noting which gate could not be evaluated and
   requesting human review. The agent MUST NOT post an approval in this case.

4. **Audit trail.**
   The pr-reviewer's structured summary comment is the audit trail for each
   AI-approved PR. The comment MUST include: (a) the list of review commits
   verified, (b) the CI check status at the time of approval, and (c) an
   explicit statement that the approval was posted by an AI agent acting under
   this ADR.

5. **Override.**
   Any human operator may revoke an AI-posted approval and post a rejection at
   any time. Branch protection rules requiring human review may be applied at
   the repository level to override this policy for sensitive branches.

---

## Rationale

**Why permit AI approvals at all?**

The four parallel review agents (qa, devops, ui-ux, arch) already perform the
substantive review work: they read the CONSTITUTION.md, check boundary
constraints, verify test coverage, and validate security posture. The
pr-reviewer's approval is a consolidation step — it verifies that all review
gates were exercised and CI passed, then unblocks auto-merge.

Requiring a human approval on every PR when specialized agents have already
reviewed it adds latency without adding review quality, particularly during
autonomous phase execution (CLAUDE.md Rule 12).

**Why prohibit AI-triggered merges?**

Merging is a destructive action that modifies the target branch and potentially
triggers downstream workflows (deploy, release, notification). The merge action
is separated from the approval action in GitHub's model for good reason.
Keeping merge as a human or auto-merge-only action preserves a clear boundary
between "all gates passed" (AI-verifiable) and "ship it" (human or
auto-merge-gated-on-CI).

**Alternatives considered:**

- *Always require human approval:* Correct but adds latency and friction for
  routine phase tasks where the review subagents have already done the work.
- *AI agent self-approves own PR:* Rejected — the approver and author must be
  different entities. The pr-reviewer is a distinct agent from the
  software-developer, satisfying this constraint.
- *No formal governance, just do it:* Rejected — ADV-038 explicitly called out
  the absence of documentation as a finding. This ADR closes that finding.

---

## Consequences

- The pr-reviewer agent has a documented, bounded authority to approve PRs.
- Operators reading the GitHub PR timeline can identify AI-posted approvals by
  the structured summary comment format and the ADR reference it contains.
- Future changes to this policy (e.g., restricting to specific branches,
  requiring additional human gates for security-sensitive changes) require an
  ADR amendment.
- This decision does NOT apply to external contributors or forks — only to
  first-party agent workflows within this repository.
