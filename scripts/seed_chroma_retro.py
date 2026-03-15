"""
ChromaDB Retrospective & Advisory Memory Seeding Script.

Reads docs/RETRO_LOG.md, parses the Task Reviews section into individual
retrospective notes and the Open Advisory Items table into advisory rows,
then upserts them into the local ChromaDB instance under the
'Retrospectives' and 'Advisories' collections respectively.

Verifies seeding by executing a retrieval query against each collection.

Task: B — ChromaDB Learning System Wiring
CONSTITUTION Priority 0: No PII or secrets are handled by this script.
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, List, cast

try:
    import chromadb
    from chromadb import Collection
except ImportError:
    # logger is not yet configured at import time — sys.stderr is the only safe output.
    sys.stderr.write("chromadb module is not installed. Please install it to continue.\n")
    sys.exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH: str = os.path.expanduser("~/.chroma_data")

RETRO_LOG_PATH: str = "docs/RETRO_LOG.md"

DOMAIN_KEYWORDS: List[str] = [
    "bootstrapper",
    "testing",
    "imports",
    "docker",
    "security",
    "coverage",
    "migration",
    "schema",
    "ingestion",
    "profiler",
    "synthesizer",
    "masking",
    "privacy",
    "shared",
    "cli",
    "api",
    "database",
    "postgres",
    "redis",
    "minio",
    "poetry",
    "type",
    "mypy",
    "ruff",
    "bandit",
    "pii",
    "encryption",
    "audit",
    "advisory",
    "retro",
    "tdd",
    "adr",
    "wiring",
    "integration",
    "unit",
]

VERIFICATION_QUERIES: dict[str, str] = {
    "Retrospectives": "bootstrapper wiring pattern",
    "Advisories": "security finding before production",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def infer_domain_tags(content: str) -> List[str]:
    """Infer domain tags from retrospective content by keyword matching.

    Performs a case-insensitive scan of the content text against the
    DOMAIN_KEYWORDS list and returns every keyword that appears.

    Args:
        content: Full text of a retrospective note or advisory row.

    Returns:
        Sorted list of matched domain keyword strings (may be empty).
    """
    lower = content.lower()
    return sorted({kw for kw in DOMAIN_KEYWORDS if kw in lower})


def extract_task_id(heading: str) -> str:
    """Extract a machine-readable task ID from a RETRO_LOG section heading.

    Attempts to match patterns such as "P4-T4.2a", "P3.5-T3.5.4",
    "P1-T1.3-1.7", or falls back to a slug derived from the full heading.

    Args:
        heading: Raw heading text, e.g. "[2026-03-14] P4-T4.2a — OOM Pre-Flight".

    Returns:
        Task ID string, e.g. "P4-T4.2a". Falls back to a lowercased,
        hyphen-separated slug of the heading if no pattern matches.
    """
    # Match common task-ID patterns: P<n>[-.]T<n>[.<n>][<letter>]
    pattern = r"(P[\d.]+[-\u2013]T[\d.]+(?:[-\u2013][\d.]+)?[a-z]?)"
    match = re.search(pattern, heading, re.IGNORECASE)
    if match:
        return match.group(1)
    # Match sprint/phase labels like "Phase 3.5 End-of-Phase"
    phase_match = re.search(r"(Phase\s+[\d.]+[^—\]]*)", heading, re.IGNORECASE)
    if phase_match:
        slug = re.sub(r"\s+", "-", phase_match.group(1).strip().lower())
        return slug
    # Last resort: slug the whole heading
    clean = re.sub(r"[\[\]()\u2014\u2013]", " ", heading)
    slug = re.sub(r"\s+", "-", clean.strip().lower())
    return slug[:80]  # cap length for use as a document ID component


def parse_retrospective_notes(retro_log_text: str) -> List[dict[str, str]]:
    """Parse the Task Reviews section of RETRO_LOG.md into structured records.

    Splits the document at the '## Task Reviews' heading and then on each
    '### ' subsection. Each subsection becomes one retrospective note.

    Args:
        retro_log_text: Full text of RETRO_LOG.md.

    Returns:
        List of dicts with keys: 'task_id', 'heading', 'content',
        'domain_tags' (comma-separated string).
    """
    # Locate Task Reviews section
    task_reviews_match = re.search(r"^## Task Reviews\s*$", retro_log_text, re.MULTILINE)
    if not task_reviews_match:
        logger.warning("'## Task Reviews' section not found in RETRO_LOG.md.")
        return []

    reviews_section = retro_log_text[task_reviews_match.end() :]

    # Split on level-3 headings within the section
    # Each ### block is one retrospective note
    parts = re.split(r"\n(?=### )", reviews_section)

    notes: List[dict[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Extract the heading line
        first_line_match = re.match(r"^### (.+)$", part, re.MULTILINE)
        if not first_line_match:
            continue
        heading = first_line_match.group(1).strip()
        task_id = extract_task_id(heading)
        domain_tags = infer_domain_tags(part)
        notes.append(
            {
                "task_id": task_id,
                "heading": heading,
                "content": part,
                "domain_tags": ",".join(domain_tags),
            }
        )

    logger.info("Parsed %d retrospective notes from Task Reviews section.", len(notes))
    return notes


def parse_advisory_items(retro_log_text: str) -> List[dict[str, str]]:
    """Parse the Open Advisory Items table from RETRO_LOG.md into structured records.

    Reads the markdown table rows between the '## Open Advisory Items' heading
    and the next '---' separator or heading. Skips the header and separator rows.

    Args:
        retro_log_text: Full text of RETRO_LOG.md.

    Returns:
        List of dicts with keys: 'adv_id', 'source', 'target_task',
        'advisory', 'domain_tags' (comma-separated string).
        Returns empty list if the section or table is absent.
    """
    advisory_match = re.search(r"^## Open Advisory Items\s*$", retro_log_text, re.MULTILINE)
    if not advisory_match:
        logger.warning("'## Open Advisory Items' section not found in RETRO_LOG.md.")
        return []

    # Grab text up to the first '---' separator or next '##' heading
    section_text = retro_log_text[advisory_match.end() :]
    end_match = re.search(r"\n---\n|\n## ", section_text)
    if end_match:
        section_text = section_text[: end_match.start()]

    items: List[dict[str, str]] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        # Skip blank lines, table header, and separator rows
        is_non_data = (
            not stripped
            or stripped.startswith("| ID")
            or stripped.startswith("|---")
            or stripped.startswith("Advisory")
        )
        if is_non_data:
            continue
        if not stripped.startswith("|"):
            continue

        # Parse pipe-delimited columns: | ID | Source | Target Task | Advisory |
        cols = [c.strip() for c in stripped.split("|")]
        # Filter out empty strings from leading/trailing pipes
        cols = [c for c in cols if c]
        if len(cols) < 4:
            continue

        adv_id = cols[0]
        source = cols[1]
        target_task = cols[2]
        advisory_text = cols[3]

        if not adv_id.startswith("ADV-"):
            continue

        domain_tags = infer_domain_tags(advisory_text)
        items.append(
            {
                "adv_id": adv_id,
                "source": source,
                "target_task": target_task,
                "advisory": advisory_text,
                "domain_tags": ",".join(domain_tags),
            }
        )

    logger.info("Parsed %d advisory items from Open Advisory Items table.", len(items))
    return items


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def seed_retrospectives(collection: Collection, notes: List[dict[str, str]]) -> int:
    """Upsert parsed retrospective notes into the Retrospectives collection.

    Args:
        collection: Target ChromaDB collection object.
        notes: List of dicts as returned by parse_retrospective_notes().

    Returns:
        Number of documents upserted.
    """
    if not notes:
        logger.warning("No retrospective notes to seed.")
        return 0

    ids = [f"retro-{note['task_id']}" for note in notes]
    documents = [note["content"] for note in notes]
    metadatas = [
        {
            "task_id": note["task_id"],
            "heading": note["heading"],
            "domain_tags": note["domain_tags"],
        }
        for note in notes
    ]

    # cast: chromadb accepts dict[str, str] at runtime; stubs require a broader type
    collection.upsert(documents=documents, ids=ids, metadatas=cast(Any, metadatas))
    logger.info("Upserted %d retrospective notes into 'Retrospectives'.", len(notes))
    return len(notes)


def seed_advisories(collection: Collection, items: List[dict[str, str]]) -> int:
    """Upsert parsed advisory items into the Advisories collection.

    Args:
        collection: Target ChromaDB collection object.
        items: List of dicts as returned by parse_advisory_items().

    Returns:
        Number of documents upserted.
    """
    if not items:
        logger.warning("No advisory items to seed.")
        return 0

    ids = [f"adv-{item['adv_id']}" for item in items]
    documents = [item["advisory"] for item in items]
    metadatas = [
        {
            "adv_id": item["adv_id"],
            "source": item["source"],
            "target_task": item["target_task"],
            "domain_tags": item["domain_tags"],
        }
        for item in items
    ]

    # cast: chromadb accepts dict[str, str] at runtime; stubs require a broader type
    collection.upsert(documents=documents, ids=ids, metadatas=cast(Any, metadatas))
    logger.info("Upserted %d advisory items into 'Advisories'.", len(items))
    return len(items)


def verify_retrieval(collection: Collection, collection_name: str, query: str) -> None:
    """Execute a semantic query and log the top result for manual verification.

    Args:
        collection: Target ChromaDB collection object.
        collection_name: Human-readable name for log messages.
        query: Natural language query string.
    """
    results = collection.query(query_texts=[query], n_results=1)
    documents = results.get("documents", [[]])
    top_hit = documents[0][0] if documents and documents[0] else "<no results>"
    logger.info("Verification query for '%s': '%s'", collection_name, query)
    logger.info("Top result preview: %s...", top_hit[:200].replace("\n", " "))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Seed ChromaDB with retrospective notes and advisory items, then verify.

    Reads docs/RETRO_LOG.md from the repository root, parses it into
    structured records, upserts them into the 'Retrospectives' and
    'Advisories' collections, and verifies each collection with a
    retrieval query.

    Raises:
        SystemExit: If RETRO_LOG.md is not found, the ChromaDB client
            cannot connect, or either collection cannot be created.
    """
    # Resolve repo root relative to this script's location
    repo_root = Path(__file__).resolve().parent.parent
    retro_log_path = repo_root / RETRO_LOG_PATH

    if not retro_log_path.exists():
        logger.error("RETRO_LOG.md not found at %s", retro_log_path)
        sys.exit(1)

    logger.info("Reading RETRO_LOG.md from %s...", retro_log_path)
    retro_log_text = retro_log_path.read_text(encoding="utf-8")

    notes = parse_retrospective_notes(retro_log_text)
    advisory_items = parse_advisory_items(retro_log_text)

    logger.info("Connecting to ChromaDB at %s...", DB_PATH)
    try:
        client = chromadb.PersistentClient(path=DB_PATH)
    except Exception as e:
        # chromadb's public API does not expose a stable typed exception base class.
        logger.error("Failed to connect to ChromaDB at %s: %s", DB_PATH, e)
        sys.exit(1)

    # Seed Retrospectives collection
    retro_collection = client.get_or_create_collection(name="Retrospectives")
    seed_retrospectives(retro_collection, notes)
    verify_retrieval(
        retro_collection,
        "Retrospectives",
        VERIFICATION_QUERIES["Retrospectives"],
    )

    # Seed Advisories collection
    adv_collection = client.get_or_create_collection(name="Advisories")
    seed_advisories(adv_collection, advisory_items)
    verify_retrieval(
        adv_collection,
        "Advisories",
        VERIFICATION_QUERIES["Advisories"],
    )

    logger.info(
        "Retrospective seeding complete. %d notes in 'Retrospectives', %d items in 'Advisories'.",
        len(notes),
        len(advisory_items),
    )


if __name__ == "__main__":
    main()
