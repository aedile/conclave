"""
ChromaDB Namespace Initialization Script.

This script initializes the core semantic memory namespaces required for the
Autonomous Agile Orchestration phase (Phase 0.6) natively on the host machine.
It strictly adheres to mypy constraints and utilizes Google-style docstrings.
"""

import os
import sys
from typing import List

try:
    import chromadb
except ImportError:
    print("chromadb module is not installed. Please install it to continue.")
    sys.exit(1)


def initialize_collections(db_path: str, collection_names: List[str]) -> None:
    """
    Initializes the required ChromaDB collections dynamically.

    Args:
        db_path (str): The absolute path to the local ChromaDB storage directory.
        collection_names (List[str]): A list of string collection names to initialize.

    Returns:
        None
    """
    try:
        # Initialize the persistent client at the specified path
        client = chromadb.PersistentClient(path=db_path)
        
        for name in collection_names:
            try:
                # get_or_create_collection prevents duplication errors
                client.get_or_create_collection(name=name)
                print(f"Collection '{name}' successfully initialized or verified.")
            except Exception as e:
                print(f"Failed to initialize collection '{name}': {e}")
                
    except Exception as e:
        print(f"Failed to connect to ChromaDB at {db_path}: {e}")
        sys.exit(1)


def main() -> None:
    """
    Main execution block for the script.
    """
    home_dir: str | None = os.getenv("HOME")
    if not home_dir:
        print("ERROR: HOME environment variable is not set. Cannot determine ChromaDB path.")
        sys.exit(1)

    db_storage_path: str = os.path.join(home_dir, ".chroma_data")
    target_collections: List[str] = ["ADRs", "Retrospectives", "Constitution"]

    print(f"Initializing semantic memory at {db_storage_path}...")
    initialize_collections(db_path=db_storage_path, collection_names=target_collections)


if __name__ == "__main__":
    main()
