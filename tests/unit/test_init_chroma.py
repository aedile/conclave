"""
Tests for scripts/init_chroma.py.

CONSTITUTION Priority 3: TDD RED Phase
CONSTITUTION Priority 4: 90%+ Coverage
"""

import sys
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module():
    """Import init_chroma after ensuring scripts/ is on sys.path."""
    import importlib
    import os
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(scripts_dir))
    import init_chroma
    importlib.reload(init_chroma)
    return init_chroma


# ---------------------------------------------------------------------------
# initialize_collections
# ---------------------------------------------------------------------------

class TestInitializeCollections:
    """Tests for initialize_collections()."""

    def test_creates_all_collections(self) -> None:
        """initialize_collections calls get_or_create_collection for each name."""
        module = _import_module()
        mock_client = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.return_value = mock_client

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            module.initialize_collections("/tmp/test_db", ["ADRs", "Retrospectives"])

        assert mock_client.get_or_create_collection.call_count == 2
        mock_client.get_or_create_collection.assert_any_call(name="ADRs")
        mock_client.get_or_create_collection.assert_any_call(name="Retrospectives")

    def test_logs_error_and_continues_on_collection_failure(self) -> None:
        """A single collection failure does not abort remaining collections."""
        module = _import_module()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.side_effect = [
            Exception("disk full"),
            MagicMock(),  # second collection succeeds
        ]
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.return_value = mock_client

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            # Should not raise — error is logged and loop continues
            module.initialize_collections("/tmp/test_db", ["ADRs", "Retrospectives"])

        assert mock_client.get_or_create_collection.call_count == 2

    def test_exits_on_client_connection_failure(self) -> None:
        """initialize_collections calls sys.exit(1) when PersistentClient raises."""
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.side_effect = Exception("connection refused")

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            with pytest.raises(SystemExit) as exc_info:
                module.initialize_collections("/tmp/bad_db", ["ADRs"])
        assert exc_info.value.code == 1

    def test_empty_collection_list_is_no_op(self) -> None:
        """An empty collection list performs no operations."""
        mock_client = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.return_value = mock_client

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            module.initialize_collections("/tmp/test_db", [])

        mock_client.get_or_create_collection.assert_not_called()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    """Tests for main()."""

    def test_main_exits_when_home_unset(self) -> None:
        """main() calls sys.exit(1) when HOME environment variable is absent."""
        mock_chroma = MagicMock()
        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            with patch.dict("os.environ", {}, clear=True):
                # Remove HOME specifically
                import os
                env_without_home = {k: v for k, v in os.environ.items() if k != "HOME"}
                with patch.dict("os.environ", env_without_home, clear=True):
                    with pytest.raises(SystemExit) as exc_info:
                        module.main()
            assert exc_info.value.code == 1

    def test_main_calls_initialize_with_expected_collections(self) -> None:
        """main() invokes initialize_collections with the three required namespaces."""
        mock_chroma = MagicMock()
        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            module = _import_module()
            with patch.object(module, "initialize_collections") as mock_init:
                module.main()
            mock_init.assert_called_once()
            collections = mock_init.call_args.kwargs["collection_names"]
            assert set(collections) == {"ADRs", "Retrospectives", "Constitution"}
