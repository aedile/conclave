"""Mutation score threshold checker for cosmic-ray sessions (T53.1).

Reads a cosmic-ray SQLite session file, computes the mutation score, and
exits non-zero if the score is below the ADR-0047 threshold (60%).

Spec-challenger guards:
  - Zero-mutant sessions fail loudly (not silently claimed as 100% or 0%).
  - Incomplete runs (pending mutants remaining) fail loudly.
  - Score must be >= THRESHOLD_PERCENT to pass.

Usage:
    python scripts/check_mutation_score.py <session.sqlite>

Exit codes:
    0 — Score meets threshold.
    1 — Score below threshold, zero mutants, or incomplete run.
"""

import sys
from pathlib import Path

THRESHOLD_PERCENT: float = 60.0  # ADR-0047: initial threshold, targeting 70% by Phase 55


def check_score(session_path: str) -> int:
    """Check mutation score from a cosmic-ray session file.

    Args:
        session_path: Path to the cosmic-ray SQLite session (.sqlite) file.

    Returns:
        Exit code: 0 if threshold met, 1 otherwise.
    """
    # Import inside function so the script fails descriptively if cosmic-ray is
    # not installed rather than at import time (used as a standalone script).
    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import TestOutcome

    session_file = Path(session_path)
    if not session_file.exists():
        print(f"ERROR: Session file not found: {session_file}", file=sys.stderr)
        return 1

    try:
        with use_db(str(session_file), WorkDB.Mode.open) as db:
            total_work_items = db.num_work_items
            total_results = db.num_results

            killed = 0
            survived = 0
            timeout_count = 0
            for _job_id, result in db.results:
                if result.test_outcome == TestOutcome.KILLED:
                    killed += 1
                elif result.test_outcome == TestOutcome.SURVIVED:
                    survived += 1
                else:
                    # Timeout, incompetent, no_coverage — not counted in score denominator
                    timeout_count += 1

    except Exception as exc:
        print(f"ERROR: Failed to read session database: {exc}", file=sys.stderr)
        return 1

    pending_unexecuted = total_work_items - total_results
    total_evaluated = killed + survived

    print(f"Cosmic-ray session: {session_file}")
    print(f"  Total work items:  {total_work_items}")
    print(f"  Completed results: {total_results}")
    print(f"  Killed:            {killed}")
    print(f"  Survived:          {survived}")
    print(f"  Timeout/other:     {timeout_count}")
    print(f"  Pending:           {pending_unexecuted}")

    # Guard: zero-mutant sessions fail loudly.
    # A session with 0 work items cannot produce a meaningful score.
    if total_work_items == 0:
        print(
            "\nFAIL: zero mutants found in this session. "
            "The cosmic-ray configuration may be misconfigured — "
            "no Python files were targeted for mutation. "
            "Ensure module-path points to a non-empty package.",
            file=sys.stderr,
        )
        return 1

    # Guard: incomplete run detection — pending (unexecuted) mutants remain.
    # A partial session must fail the gate rather than reporting a misleading score.
    if pending_unexecuted > 0:
        print(
            f"\nFAIL: incomplete run detected — {pending_unexecuted} mutant(s) "
            "are still pending (unexecuted). "
            "The 'cosmic-ray exec' step was likely interrupted before completion. "
            "Re-run 'cosmic-ray exec' to complete the session before checking the score.",
            file=sys.stderr,
        )
        return 1

    # Guard: if all results are incompetent/no-coverage with none evaluated, fail.
    if total_evaluated == 0 and total_results > 0:
        print(
            f"\nFAIL: {total_results} mutant(s) executed but none killed or survived "
            "(all incompetent or timed out). "
            "This likely indicates a misconfigured test command or module path — "
            "the tests may not be exercising the mutated code.",
            file=sys.stderr,
        )
        return 1

    # Compute score
    score = (killed / total_evaluated) * 100.0
    print(f"\nMutation score: {score:.1f}% ({killed}/{total_evaluated})")
    print(f"Threshold: {THRESHOLD_PERCENT:.0f}% (ADR-0047)")

    if score < THRESHOLD_PERCENT:
        print(
            f"\nFAIL: mutation score {score:.1f}% is below the {THRESHOLD_PERCENT:.0f}% threshold. "
            f"Improve test coverage for the {survived} surviving mutant(s) "
            "in shared/security/ and modules/privacy/.",
            file=sys.stderr,
        )
        return 1

    print(f"\nPASS: mutation score {score:.1f}% meets the {THRESHOLD_PERCENT:.0f}% threshold.")
    return 0


def main() -> None:
    """Entry point for the mutation score checker.

    Raises:
        SystemExit: Always exits with 0 (pass) or 1 (fail).
    """
    if len(sys.argv) != 2:
        print(
            f"Usage: {sys.argv[0]} <session.sqlite>",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(check_score(sys.argv[1]))


if __name__ == "__main__":
    main()
