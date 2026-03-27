"""Tests for T60.3 — build_ephemeral_storage_client moved to factories.py.

Verifies that:
- build_ephemeral_storage_client is importable from the canonical new location (factories.py)
- The re-export in main.py still works (patch target preservation)
- Both symbols resolve to the same function object

CONSTITUTION Priority 3: TDD
Task: T60.3 — Move build_ephemeral_storage_client to factories.py
"""

from __future__ import annotations


class TestEphemeralStorageClientCanonicalLocation:
    """build_ephemeral_storage_client must live in factories.py (canonical source)."""

    def test_function_importable_from_factories(self) -> None:
        """build_ephemeral_storage_client must be importable from factories.py."""
        from synth_engine.bootstrapper.factories import build_ephemeral_storage_client

        assert build_ephemeral_storage_client.__name__ == "build_ephemeral_storage_client"

    def test_function_is_callable(self) -> None:
        """build_ephemeral_storage_client must be a callable."""
        from synth_engine.bootstrapper.factories import build_ephemeral_storage_client

        assert callable(build_ephemeral_storage_client)


class TestEphemeralStorageClientReExport:
    """Re-export in main.py must be preserved for test patch targets."""

    def test_function_re_exported_from_main(self) -> None:
        """main.py must still export build_ephemeral_storage_client."""
        from synth_engine.bootstrapper.main import build_ephemeral_storage_client

        assert build_ephemeral_storage_client.__name__ == "build_ephemeral_storage_client"

    def test_factories_and_main_resolve_to_same_function(self) -> None:
        """factories.py and main.py must both point to the same function object."""
        from synth_engine.bootstrapper.factories import (
            build_ephemeral_storage_client as fn_from_factories,
        )
        from synth_engine.bootstrapper.main import (
            build_ephemeral_storage_client as fn_from_main,
        )

        assert fn_from_factories is fn_from_main

    def test_function_not_defined_in_main_module(self) -> None:
        """main.py must import (not define) build_ephemeral_storage_client.

        Verifies the function's module attribute points to factories, not main,
        confirming the canonical source is factories.py.
        """
        from synth_engine.bootstrapper.factories import build_ephemeral_storage_client

        # The function must be defined in factories, not main
        assert build_ephemeral_storage_client.__module__ == ("synth_engine.bootstrapper.factories")
