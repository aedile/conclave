"""
ChromaDB Governance Memory Seeding Script.

Reads CONSTITUTION.md and docs/ARCHITECTURAL_REQUIREMENTS.md, chunks them
into semantically coherent paragraphs, and injects them into the local
ChromaDB instance under the 'Constitution' and 'ADRs' collections.

Verifies seeding by executing a retrieval query against each collection.

Task: 0.6.2 — Memory Seeding (Governance)
CONSTITUTION Priority 0: No PII or secrets are handled by this script.
"""

import logging
import os
import sys
from pathlib import Path
from typing import List

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

DB_PATH: str = os.path.join(os.path.expanduser("~"), ".chroma_data")

# Map (source file relative to repo root) -> collection name
SEEDING_MANIFEST: dict[str, str] = {
    "CONSTITUTION.md": "Constitution",
    "docs/ARCHITECTURAL_REQUIREMENTS.md": "ADRs",
}

VERIFICATION_QUERIES: dict[str, str] = {
    "Constitution": "What is the logging policy?",
    "ADRs": "What are the key architectural constraints?",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def chunk_document(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
    """Split a document into overlapping fixed-size character chunks.

    A simple sliding-window chunker. Adequate for governance docs where
    retrieval granularity matters more than perfect semantic boundaries.

    Args:
        text: Raw document text.
        chunk_size: Maximum characters per chunk.
        overlap: Characters of overlap between consecutive chunks.
            Must be strictly less than chunk_size to prevent infinite looping.

    Returns:
        List of non-empty string chunks.

    Raises:
        ValueError: If overlap is greater than or equal to chunk_size.
    """
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) must be strictly less than chunk_size ({chunk_size}) "
            "to prevent an infinite loop."
        )

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def seed_collection(
    collection: Collection,
    source_path: Path,
    collection_name: str,
) -> int:
    """Read a markdown file, chunk it, and upsert into a ChromaDB collection.

    Args:
        collection: Target ChromaDB collection object.
        source_path: Absolute path to the source markdown file.
        collection_name: Human-readable name for log messages.

    Returns:
        Number of chunks upserted.

    Raises:
        SystemExit: If the source file does not exist at source_path.
    """
    if not source_path.exists():
        logger.error("Source file not found: %s", source_path)
        sys.exit(1)

    text = source_path.read_text(encoding="utf-8")
    chunks = chunk_document(text)

    ids = [f"{collection_name}-chunk-{i}" for i in range(len(chunks))]
    metadatas = [{"source": str(source_path.name), "chunk_index": i} for i in range(len(chunks))]

    collection.upsert(documents=chunks, ids=ids, metadatas=metadatas)
    logger.info("Upserted %d chunks into '%s'.", len(chunks), collection_name)
    return len(chunks)


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
    """Seed ChromaDB with governance documents and verify retrieval.

    Raises:
        SystemExit: If the ChromaDB client cannot connect.
    """
    repo_root = Path(__file__).resolve().parent.parent

    logger.info("Connecting to ChromaDB at %s...", DB_PATH)
    try:
        client = chromadb.PersistentClient(path=DB_PATH)
    except Exception as e:
        # chromadb's public API does not expose a stable typed exception base class.
        logger.error("Failed to connect to ChromaDB at %s: %s", DB_PATH, e)
        sys.exit(1)

    for relative_path, collection_name in SEEDING_MANIFEST.items():
        source_path = repo_root / relative_path
        logger.info("Seeding '%s' from %s...", collection_name, relative_path)

        collection = client.get_or_create_collection(name=collection_name)
        seed_collection(collection, source_path, collection_name)

        query = VERIFICATION_QUERIES[collection_name]
        verify_retrieval(collection, collection_name, query)

    logger.info("Memory seeding complete. Governance context is now queryable by all agent streams.")


if __name__ == "__main__":
    main()
