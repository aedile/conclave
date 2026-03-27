"""Pydantic schemas for vault operations.

Contains request and response models for the /unseal endpoint and other
vault-related API operations.

Task: T60.5 — Move UnsealRequest from lifecycle.py to schemas/vault.py
    ``UnsealRequest`` previously lived in ``lifecycle.py`` alongside the
    lifespan hook.  All other Pydantic schemas live in ``schemas/``; this
    move enforces that convention.

    Backward compatibility: ``lifecycle.py`` re-exports ``UnsealRequest``
    from this module, and ``main.py`` re-exports it from ``lifecycle.py``,
    so existing imports continue to resolve:
    - ``from synth_engine.bootstrapper.schemas.vault import UnsealRequest``
    - ``from synth_engine.bootstrapper.lifecycle import UnsealRequest``
    - ``from synth_engine.bootstrapper.main import UnsealRequest``
"""

from __future__ import annotations

from pydantic import BaseModel


class UnsealRequest(BaseModel):
    """Request body for the /unseal endpoint.

    Attributes:
        passphrase: Operator-provided passphrase used to derive the KEK.
    """

    passphrase: str
