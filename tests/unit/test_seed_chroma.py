"""
Tests for scripts/seed_chroma.py.

CONSTITUTION Priority 3: TDD RED Phase
CONSTITUTION Priority 4: 90%+ Coverage
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module():
    """Import seed_chroma after ensuring scripts/ is on sys.path."""
    import importlib
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(scripts_dir))
    import seed_chroma
    importlib.reload(seed_chroma)
    return seed_chroma


# ---------------------------------------------------------------------------
# chunk_document
# ---------------------------------------------------------------------------

class TestChunkDocument:
    """Tests for chunk_document()."""

    def test_single_chunk_for_short_text(self) -> None:
        """Text shorter than chunk_size produces exactly one chunk."""
        module = _import_module()
        result = module.chunk_document("hello world", chunk_size=100, overlap=10)
        assert result == ["hello world"]

    def test_multiple_chunks_for_long_text(self) -> None:
        """Text longer than chunk_size is split into multiple chunks."""
        module = _import_module()
        text = "a" * 1200
        result = module.chunk_document(text, chunk_size=600, overlap=100)
        assert len(result) > 1

    def test_chunks_overlap(self) -> None:
        """Consecutive chunks share overlap characters."""
        module = _import_module()
        text = "abcdefghij" * 100  # 1000 chars
        chunks = module.chunk_document(text, chunk_size=200, overlap=50)
        # The end of chunk[0] should appear at the start of chunk[1]
        assert chunks[0][-50:] in chunks[1]

    def test_empty_text_returns_empty_list(self) -> None:
        """Empty input produces an empty chunk list."""
        module = _import_module()
        result = module.chunk_document("", chunk_size=600, overlap=100)
        assert result == []

    def test_whitespace_only_text_returns_empty_list(self) -> None:
        """Whitespace-only text produces no chunks (stripped to empty)."""
        module = _import_module()
        result = module.chunk_document("   \n\t  ", chunk_size=600, overlap=100)
        assert result == []

    def test_raises_on_overlap_equal_to_chunk_size(self) -> None:
        """chunk_document raises ValueError when overlap equals chunk_size (infinite loop guard)."""
        module = _import_module()
        with pytest.raises(ValueError, match="overlap"):
            module.chunk_document("some text" * 100, chunk_size=100, overlap=100)

    def test_raises_on_overlap_greater_than_chunk_size(self) -> None:
        """chunk_document raises ValueError when overlap exceeds chunk_size."""
        module = _import_module()
        with pytest.raises(ValueError, match="overlap"):
            module.chunk_document("some text" * 100, chunk_size=100, overlap=200)

    def test_no_empty_chunks_in_output(self) -> None:
        """All returned chunks are non-empty strings."""
        module = _import_module()
        text = "word " * 500
        chunks = module.chunk_document(text, chunk_size=50, overlap=10)
        assert all(len(c) > 0 for c in chunks)


# ---------------------------------------------------------------------------
# seed_collection
# ---------------------------------------------------------------------------

class TestSeedCollection:
    """Tests for seed_collection()."""

    def test_upserts_chunks_into_collection(self, tmp_path: Path) -> None:
        """seed_collection reads a file and calls collection.upsert with the expected chunk count.

        'hello world ' * 100 = 1200 chars. With chunk_size=600, overlap=100:
          chunk 0: chars 0–599, chunk 1: chars 500–1099, chunk 2: chars 1000–1199 → 3 chunks.
        """
        module = _import_module()
        source = tmp_path / "doc.md"
        source.write_text("hello world " * 100, encoding="utf-8")

        mock_collection = MagicMock()
        count = module.seed_collection(mock_collection, source, "TestCollection")

        mock_collection.upsert.assert_called_once()
        assert count == 3

    def test_exits_when_source_file_missing(self, tmp_path: Path) -> None:
        """seed_collection calls sys.exit(1) when source path does not exist."""
        module = _import_module()
        missing = tmp_path / "nonexistent.md"
        mock_collection = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            module.seed_collection(mock_collection, missing, "TestCollection")
        assert exc_info.value.code == 1

    def test_chunk_ids_are_unique(self, tmp_path: Path) -> None:
        """Each chunk upserted to the collection has a unique ID."""
        module = _import_module()
        source = tmp_path / "doc.md"
        source.write_text("x " * 1000, encoding="utf-8")

        captured_ids: list[str] = []

        def capture_upsert(**kwargs: object) -> None:
            captured_ids.extend(kwargs.get("ids", []))  # type: ignore[arg-type]

        mock_collection = MagicMock()
        mock_collection.upsert.side_effect = capture_upsert
        module.seed_collection(mock_collection, source, "TestCollection")

        assert len(captured_ids) == len(set(captured_ids)), "Duplicate chunk IDs detected"

    def test_metadata_includes_source_filename(self, tmp_path: Path) -> None:
        """Each chunk's metadata includes the source filename."""
        module = _import_module()
        source = tmp_path / "CONSTITUTION.md"
        source.write_text("binding rules " * 100, encoding="utf-8")

        captured_metadatas: list[dict] = []

        def capture_upsert(**kwargs: object) -> None:
            captured_metadatas.extend(kwargs.get("metadatas", []))  # type: ignore[arg-type]

        mock_collection = MagicMock()
        mock_collection.upsert.side_effect = capture_upsert
        module.seed_collection(mock_collection, source, "Constitution")

        assert all(m["source"] == "CONSTITUTION.md" for m in captured_metadatas)


# ---------------------------------------------------------------------------
# verify_retrieval
# ---------------------------------------------------------------------------

class TestVerifyRetrieval:
    """Tests for verify_retrieval()."""

    def test_calls_collection_query(self) -> None:
        """verify_retrieval issues a query against the collection."""
        module = _import_module()
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [["relevant result text"]]}

        module.verify_retrieval(mock_collection, "Constitution", "logging policy")

        mock_collection.query.assert_called_once_with(
            query_texts=["logging policy"], n_results=1
        )

    def test_handles_empty_query_results_gracefully(self) -> None:
        """verify_retrieval does not raise when query returns no documents."""
        module = _import_module()
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [[]]}

        # Should complete without raising
        module.verify_retrieval(mock_collection, "Constitution", "nonexistent topic")

    def test_handles_missing_documents_key(self) -> None:
        """verify_retrieval does not raise when query result has no 'documents' key."""
        module = _import_module()
        mock_collection = MagicMock()
        mock_collection.query.return_value = {}

        module.verify_retrieval(mock_collection, "Constitution", "query")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for seed_chroma.main()."""

    def test_main_happy_path_iterates_seeding_manifest(self) -> None:
        """main() calls seed_collection and verify_retrieval for each entry in SEEDING_MANIFEST."""
        mock_client = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.return_value = mock_client

        with patch.dict("sys.modules", {"chromadb": mock_chroma, "chromadb.Collection": MagicMock()}):
            module = _import_module()
            with patch.object(module, "seed_collection") as mock_seed, \
                 patch.object(module, "verify_retrieval"):
                module.main()

            expected_calls = len(module.SEEDING_MANIFEST)
            assert mock_seed.call_count == expected_calls

    def test_main_exits_on_chromadb_connection_failure(self) -> None:
        """main() calls sys.exit(1) when PersistentClient raises."""
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.side_effect = Exception("connection refused")

        with patch.dict("sys.modules", {"chromadb": mock_chroma, "chromadb.Collection": MagicMock()}):
            module = _import_module()
            with pytest.raises(SystemExit) as exc_info:
                module.main()
        assert exc_info.value.code == 1
