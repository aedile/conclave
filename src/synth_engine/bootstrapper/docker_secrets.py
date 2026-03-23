"""Docker secrets reader and MinIO endpoint constants.

Centralises the Docker-secrets cluster that was previously defined inline
in ``bootstrapper/main.py``.  Extracting these into their own module:

1. Removes private symbols from ``main.py``'s public namespace.
2. Allows ``health.py`` to import endpoint constants without depending on
   the entire application factory module.

``main.py`` re-exports all names from this module so existing code that
references ``synth_engine.bootstrapper.main._read_secret``,
``synth_engine.bootstrapper.main._MINIO_ENDPOINT``, or
``synth_engine.bootstrapper.main._EPHEMERAL_BUCKET`` continues to resolve
correctly (test patches inclusive).

CONSTITUTION Priority 0: Security
Task: P48 review F3 — Extract Docker secrets from main.py
"""

from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default MinIO endpoint for the ephemeral storage bucket.
MINIO_ENDPOINT: str = "http://minio-ephemeral:9000"

#: Ephemeral bucket name — backed by tmpfs in Docker Compose.
EPHEMERAL_BUCKET: str = "synth-ephemeral"

#: Docker secrets directory — credentials mounted here at runtime.
_SECRETS_DIR: Path = Path("/run/secrets")


# ---------------------------------------------------------------------------
# _read_secret
# ---------------------------------------------------------------------------


def _read_secret(name: str) -> str:
    """Read a Docker secret from ``_SECRETS_DIR``.

    Args:
        name: Secret filename (e.g. ``"minio_ephemeral_access_key"``).

    Returns:
        Secret value stripped of leading/trailing whitespace.

    Raises:
        RuntimeError: If the secret file does not exist or cannot be read.
    """
    secret_path = _SECRETS_DIR / name
    try:
        return secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Docker secret '{name}' not found at {secret_path}. "
            "Ensure the secret is mounted at /run/secrets/ by Docker Compose."
        ) from exc
