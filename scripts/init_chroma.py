"""
ChromaDB Namespace Initialization Script.

This script initializes the core semantic memory namespaces required for the
Autonomous Agile Orchestration phase (Phase 0.6) natively on the host machine.
It strictly adheres to mypy constraints and utilizes Google-style docstrings.
"""

import logging
import os
import sys
from typing import List

try:
    import chromadb
except ImportError:
    # logger is not yet configured at import time — sys.stderr is the only safe output.
    sys.stderr.write("chromadb module is not installed. Please install it to continue.\n")
    sys.exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def initialize_collections(db_path: str, collection_names: List[str]) -> None:
    """Initialize the required ChromaDB collections dynamically.

    Args:
        db_path: The absolute path to the local ChromaDB storage directory.
        collection_names: A list of string collection names to initialize.

    Raises:
        SystemExit: If the ChromaDB client cannot connect to db_path.
    """
    try:
        client = chromadb.PersistentClient(path=db_path)
    except Exception as e:
        # chromadb's public API does not expose a stable typed exception base class.
        logger.error("Failed to connect to ChromaDB at %s: %s", db_path, e)
        sys.exit(1)

    for name in collection_names:
        try:
            client.get_or_create_collection(name=name)
            logger.info("Collection '%s' successfully initialized or verified.", name)
        except Exception as e:
            # chromadb's public API does not expose a stable typed exception base class.
            logger.error("Failed to initialize collection '%s': %s", name, e)


def main() -> None:
    """Main execution block for the script."""
    home_dir: str | None = os.getenv("HOME")
    if not home_dir:
        logger.error("HOME environment variable is not set. Cannot determine ChromaDB path.")
        sys.exit(1)

    db_storage_path: str = os.path.join(home_dir, ".chroma_data")
    target_collections: List[str] = ["ADRs", "Retrospectives", "Constitution"]

    logger.info("Initializing semantic memory at %s...", db_storage_path)
    initialize_collections(db_path=db_storage_path, collection_names=target_collections)


if __name__ == "__main__":
    main()
