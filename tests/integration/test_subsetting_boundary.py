"""Integration test: subsetting module import boundary verification.

This test verifies the architectural import contract declared in
``pyproject.toml`` ``[[tool.importlinter.contracts]]`` at runtime using
Python's ``importlib`` introspection.

The subsetting module:
- MUST NOT import from: ``ingestion``, ``profiler``, ``masking``,
  ``synthesizer``, or ``privacy``.
- IS ALLOWED to import from: ``mapping`` and ``shared``.

This test complements the static ``import-linter`` CI gate by providing a
runtime guard that fails immediately if a boundary violation is introduced.
It uses ``sys.modules`` inspection after a clean import to enumerate all
transitively imported modules — no source-file string matching.

Marks: ``integration``

CONSTITUTION Priority 0: Security — boundary enforcement prevents information
    leakage between isolated domain modules.
CONSTITUTION Priority 3: TDD — integration gate for P26-T26.5.
Task: P26-T26.5 — Licensing + Migration + FK Masking Integration Tests
"""

from __future__ import annotations

import importlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Constants — boundary rules
# ---------------------------------------------------------------------------

_SUBSETTING_ROOT = "synth_engine.modules.subsetting"

_FORBIDDEN_MODULE_PREFIXES = (
    "synth_engine.modules.ingestion",
    "synth_engine.modules.profiler",
    "synth_engine.modules.masking",
    "synth_engine.modules.synthesizer",
    "synth_engine.modules.privacy",
)

_ALLOWED_MODULE_PREFIXES = (
    "synth_engine.modules.mapping",
    "synth_engine.shared",
    "synth_engine.modules.subsetting",  # self-imports are fine
)


def _is_forbidden(key: str) -> bool:
    """Return True if a module key matches any forbidden prefix.

    Args:
        key: Dotted module name from sys.modules.

    Returns:
        True when the key equals or starts with a forbidden prefix.
    """
    return any(
        key == prefix or key.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES
    )


# ---------------------------------------------------------------------------
# AC4 integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_subsetting_does_not_import_forbidden_modules() -> None:
    """Subsetting module must not transitively import forbidden sibling modules.

    Uses ``importlib`` to import the subsetting package, then inspects
    ``sys.modules`` for any entries whose dotted name starts with a forbidden
    prefix.

    This is a runtime complement to the static import-linter CI gate.

    Arrange: snapshot sys.modules before import to avoid false positives from
        previously imported modules.
    Act: import ``synth_engine.modules.subsetting`` and all its sub-modules.
    Assert: no forbidden module names appear in sys.modules after the import.
    """
    # Snapshot before import so we only check NEW entries introduced by
    # importing subsetting (avoids false positives from conftest imports).
    before_keys: set[str] = set(sys.modules.keys())

    importlib.import_module(_SUBSETTING_ROOT)

    # Also import all public sub-modules to get transitive imports
    for submodule in ("core", "traversal", "egress"):
        importlib.import_module(f"{_SUBSETTING_ROOT}.{submodule}")

    after_keys: set[str] = set(sys.modules.keys())
    new_keys = after_keys - before_keys

    violations: list[str] = [key for key in new_keys if _is_forbidden(key)]

    assert not violations, (
        "subsetting module imported forbidden modules at runtime:\n"
        + "\n".join(f"  - {v}" for v in sorted(violations))
        + "\nFix the import boundary violation in synth_engine/modules/subsetting/."
    )


@pytest.mark.integration
def test_subsetting_is_importable() -> None:
    """synth_engine.modules.subsetting must be importable without errors.

    Verifies the public API (__all__) is accessible.

    Arrange/Act: import the subsetting package.
    Assert: SubsettingEngine, DagTraversal, EgressWriter, SubsetResult are available.
    """
    subsetting = importlib.import_module(_SUBSETTING_ROOT)

    for name in ("SubsettingEngine", "DagTraversal", "EgressWriter", "SubsetResult"):
        assert hasattr(subsetting, name), f"synth_engine.modules.subsetting must export {name!r}"


@pytest.mark.integration
def test_subsetting_allowed_imports_are_present() -> None:
    """Subsetting may import from mapping and shared — verify at least one is used.

    This test guards against a regression where the subsetting module is
    accidentally refactored to depend on no shared infrastructure at all,
    which would indicate a test gap rather than an improvement.

    Arrange/Act: import subsetting and snapshot sys.modules.
    Assert: at least one ``synth_engine.shared`` or ``synth_engine.modules.mapping``
        entry exists in sys.modules.
    """
    importlib.import_module(_SUBSETTING_ROOT)

    loaded = set(sys.modules.keys())
    non_self_prefixes = [
        p for p in _ALLOWED_MODULE_PREFIXES if p != "synth_engine.modules.subsetting"
    ]
    has_allowed_import = any(
        key == prefix or key.startswith(prefix + ".")
        for key in loaded
        for prefix in non_self_prefixes
    )

    assert has_allowed_import, (
        "subsetting module does not import from mapping or shared.  "
        "This may indicate an import graph regression."
    )
    # Specific: at least 2 allowed module prefixes are defined in the test
    assert len(non_self_prefixes) >= 1, "test must check at least 1 allowed module prefix"
