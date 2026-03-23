#!/usr/bin/env python3
"""Verify the audit chain against published anchors.

Reads a local anchor JSONL file (written by LocalFileAnchorBackend) and
compares the most recently published anchor against a provided chain-head
hash and entry count.

Usage::

    python scripts/verify-audit-chain.py \\
        --anchor-file logs/audit_anchors.jsonl \\
        --chain-head-hash <64-char hex> \\
        --entry-count <int>

Exit codes:
    0 — Verification passed (or no anchors found — first boot).
    1 — Verification FAILED: chain head does not match the latest anchor.
    2 — Usage / argument error.

Example (verification pass)::

    $ python scripts/verify-audit-chain.py \\
        --anchor-file logs/audit_anchors.jsonl \\
        --chain-head-hash abcdef...1234 \\
        --entry-count 5000
    [PASS] Chain verified against anchor at entry_count=5000

Example (verification fail)::

    $ python scripts/verify-audit-chain.py \\
        --anchor-file logs/audit_anchors.jsonl \\
        --chain-head-hash 000000...0000 \\
        --entry-count 5000
    [FAIL] Chain mismatch detected!
      Latest anchor:        entry_count=5000  hash=abcdef...1234
      Provided chain head:  entry_count=5000  hash=000000...0000

CONSTITUTION Priority 0: Security
Task: T48.4 — Immutable Audit Trail Anchoring
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_anchors(anchor_file: Path) -> list[dict[str, object]]:
    """Load all anchor records from a JSONL file.

    Args:
        anchor_file: Path to the anchor JSONL file.

    Returns:
        List of parsed anchor dicts, ordered by file line (oldest first).

    Raises:
        SystemExit(2): If the file does not exist or a line is not valid JSON.
    """
    if not anchor_file.exists():
        print(f"[INFO] Anchor file '{anchor_file}' does not exist — no anchors to verify against.")
        return []

    anchors: list[dict[str, object]] = []
    for lineno, line in enumerate(anchor_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record: dict[str, object] = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[ERROR] Anchor file line {lineno} is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(2)
        anchors.append(record)
    return anchors


def _verify(
    anchors: list[dict[str, object]],
    current_chain_head: str,
    current_entry_count: int,
) -> int:
    """Verify *current_chain_head* against the latest anchor.

    Args:
        anchors: All parsed anchor records.
        current_chain_head: Chain-head hash to verify.
        current_entry_count: Entry count at the chain head.

    Returns:
        0 if verification passes, 1 if it fails.
    """
    if not anchors:
        print("[PASS] No anchors found — first-boot state, nothing to verify.")
        return 0

    # Use the anchor with the highest entry_count as the authoritative reference.
    latest: dict[str, object] = max(
        anchors,
        key=lambda a: int(a.get("entry_count", 0)),  # type: ignore[arg-type]
    )

    latest_hash = str(latest.get("chain_head_hash", ""))
    latest_count = int(latest.get("entry_count", -1))  # type: ignore[arg-type]
    latest_ts = str(latest.get("timestamp", "unknown"))

    if current_chain_head == latest_hash and current_entry_count == latest_count:
        print(
            f"[PASS] Chain verified against anchor at entry_count={latest_count} "
            f"(timestamp={latest_ts})"
        )
        return 0
    else:
        print("[FAIL] Chain mismatch detected!", file=sys.stderr)
        print(
            f"  Latest anchor:       entry_count={latest_count}  "
            f"hash={latest_hash[:16]}...  timestamp={latest_ts}",
            file=sys.stderr,
        )
        print(
            f"  Provided chain head: entry_count={current_entry_count}  "
            f"hash={current_chain_head[:16]}...",
            file=sys.stderr,
        )
        if current_chain_head != latest_hash:
            print(
                "  REASON: chain_head_hash does not match the latest anchor.",
                file=sys.stderr,
            )
        if current_entry_count != latest_count:
            print(
                f"  REASON: entry_count {current_entry_count} != "
                f"anchor entry_count {latest_count}.",
                file=sys.stderr,
            )
        return 1


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="Verify the audit chain against published anchors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--anchor-file",
        required=True,
        metavar="PATH",
        help="Path to the anchor JSONL file written by LocalFileAnchorBackend.",
    )
    parser.add_argument(
        "--chain-head-hash",
        required=True,
        metavar="HEX64",
        help="Current chain-head hash (64 lowercase hex characters).",
    )
    parser.add_argument(
        "--entry-count",
        required=True,
        type=int,
        metavar="N",
        help="Current number of events in the audit chain.",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: parse arguments, load anchors, and verify.

    Exits with code 0 (pass), 1 (fail), or 2 (usage error).
    """
    args = _parse_args()

    chain_head_hash: str = args.chain_head_hash.lower()
    if not _HEX64_RE.match(chain_head_hash):
        print(
            f"[ERROR] --chain-head-hash must be exactly 64 lowercase hex characters; "
            f"got '{args.chain_head_hash}'",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.entry_count < 0:
        print(
            f"[ERROR] --entry-count must be >= 0; got {args.entry_count}",
            file=sys.stderr,
        )
        sys.exit(2)

    anchor_file = Path(args.anchor_file)
    anchors = _load_anchors(anchor_file)
    exit_code = _verify(anchors, chain_head_hash, args.entry_count)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
