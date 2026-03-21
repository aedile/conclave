"""Artifact persistence helpers for synthesis jobs.

Handles writing Parquet outputs and HMAC-SHA256 signing sidecars.  Called by
``job_orchestration._generate_and_finalize`` after CTGAN training completes.

Split from ``tasks.py`` in P26-T26.1 to improve module focus.

Signing modes
-------------
Two signing modes are supported:

1. **Versioned** (preferred): When ``ARTIFACT_SIGNING_KEYS`` (a JSON dict of
   key_id → hex key) and ``ARTIFACT_SIGNING_KEY_ACTIVE`` are set in
   :class:`~synth_engine.shared.settings.ConclaveSettings`, signatures use
   the format ``KEY_ID (4 bytes) || HMAC-SHA256 (32 bytes)`` via
   :func:`~synth_engine.shared.security.hmac_signing.sign_versioned`.

2. **Legacy** (backward-compatible): When only ``ARTIFACT_SIGNING_KEY`` is
   set, a bare 32-byte HMAC signature is written (original behaviour).

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P26-T26.1 — Split Oversized Files (Refactor Only)
Task: T42.1 — Artifact Signing Key Versioning
"""

from __future__ import annotations

import logging
from pathlib import Path

from synth_engine.shared.security.hmac_signing import (
    HMAC_DIGEST_SIZE,
    compute_hmac,
    sign_versioned,
)

_logger = logging.getLogger(__name__)

#: Environment variable name for the artifact HMAC signing key.
#: Value must be a hex-encoded byte string (e.g. 64 hex chars for 32 bytes).
_ARTIFACT_SIGNING_KEY_ENV: str = "ARTIFACT_SIGNING_KEY"

#: Sanitized error message written to job.error_msg on generation failure.
#: Internal exception details are logged server-side; this generic string is
#: safe to surface in API responses (finding F4 — DevOps review).
_GENERATION_FAILED_MSG: str = "Generation failed — see server logs for details"


def _write_parquet_with_signing(
    df: object,
    parquet_path: str,
) -> None:
    """Write a DataFrame to Parquet and optionally write an HMAC-SHA256 sidecar.

    Signing is attempted in the following priority order:

    1. **Versioned mode**: If both ``ARTIFACT_SIGNING_KEYS`` and
       ``ARTIFACT_SIGNING_KEY_ACTIVE`` are present in settings, the active
       key is used to produce a ``KEY_ID || HMAC-SHA256`` sidecar.

    2. **Legacy mode**: If only ``ARTIFACT_SIGNING_KEY`` is present, a bare
       32-byte HMAC sidecar is written (original behaviour, backward-compatible).

    3. **Unsigned**: If neither key is configured, the artifact is written
       unsigned with a WARNING log.

    If a signing key is present but malformed, the error is logged at ERROR
    level and signing is skipped (the Parquet file has already been written).

    Args:
        df: A :class:`pandas.DataFrame` to serialise as Parquet.  Typed as
            ``object`` to avoid a hard ``pandas`` import at module level; the
            caller (``_generate_and_finalize``) guarantees a real DataFrame.
        parquet_path: Destination filesystem path (must end with ``.parquet``).

    Raises:
        OSError: If the Parquet write or sidecar write fails.

    Note:
        If a signing key is present but contains invalid hex characters or has
        an odd length, the :exc:`ValueError` from ``bytes.fromhex()`` is caught
        internally and signing is skipped with an ERROR log.  The Parquet file
        is already written at that point and the job continues normally.

    """  # noqa: DOC502
    # F5 fix: log basename only — full paths may expose internal filesystem layout.
    parquet_name = Path(parquet_path).name

    # df.to_parquet() is the pandas DataFrame API.  We call it duck-typed to
    # keep pandas as an optional import at module level; mypy is satisfied by
    # the ``object`` annotation with ``type: ignore`` at the call site.
    df.to_parquet(parquet_path, index=False)  # type: ignore[attr-defined]  # duck-typed pandas DataFrame; guaranteed by caller
    _logger.debug("Parquet artifact written: %s", parquet_name)

    from synth_engine.shared.settings import get_settings

    settings = get_settings()

    # ------------------------------------------------------------------
    # Priority 1: Versioned multi-key mode
    # ------------------------------------------------------------------
    if settings.artifact_signing_keys and settings.artifact_signing_key_active:
        _write_versioned_signature(
            parquet_path=parquet_path,
            parquet_name=parquet_name,
            keys_dict=settings.artifact_signing_keys,
            active_key_id_hex=settings.artifact_signing_key_active,
        )
        return

    # ------------------------------------------------------------------
    # Priority 2: Legacy single-key mode
    # ------------------------------------------------------------------
    signing_key_hex = settings.artifact_signing_key
    if not signing_key_hex:
        _logger.warning(
            "ARTIFACT_SIGNING_KEY is not set; Parquet artifact written unsigned: %s",
            parquet_name,
        )
        return

    # F2 fix: guard bytes.fromhex() against malformed hex input (ValueError).
    # T38.4: elevated to ERROR — a malformed signing key means the operator
    # configured signing but artifacts are written unsigned; this is a
    # security-relevant misconfiguration that warrants ERROR, not WARNING.
    try:
        signing_key = bytes.fromhex(signing_key_hex)
    except ValueError:
        _logger.error(
            "ARTIFACT_SIGNING_KEY is not valid hex; skipping Parquet signing: %s",
            parquet_name,
        )
        return

    if len(signing_key) == 0:
        _logger.warning(
            "ARTIFACT_SIGNING_KEY decoded to empty bytes; skipping Parquet signing: %s",
            parquet_name,
        )
        return

    parquet_bytes = Path(parquet_path).read_bytes()
    digest = compute_hmac(signing_key, parquet_bytes)
    assert len(digest) == HMAC_DIGEST_SIZE  # nosec B101 — internal guard

    sig_path = parquet_path + ".sig"
    Path(sig_path).write_bytes(digest)
    # F5 fix: log basename only for the sidecar path.
    _logger.info(
        "Parquet artifact HMAC-SHA256 signed (legacy); sidecar written to %s",
        Path(sig_path).name,
    )


def _write_versioned_signature(
    *,
    parquet_path: str,
    parquet_name: str,
    keys_dict: dict[str, str],
    active_key_id_hex: str,
) -> None:
    """Write a versioned ``KEY_ID || HMAC-SHA256`` sidecar file.

    Looks up the active key from ``keys_dict``, computes a versioned
    signature using :func:`sign_versioned`, and writes the result to
    ``parquet_path + '.sig'``.

    On malformed key ID or hex key, logs at ERROR and returns without
    writing the sidecar (the Parquet file is already written).

    Args:
        parquet_path: Absolute path to the Parquet artifact.
        parquet_name: Basename of the Parquet file (for log messages).
        keys_dict: Mapping of hex key ID string → hex key string.
        active_key_id_hex: Hex string of the key ID to use for signing.
    """
    hex_key = keys_dict.get(active_key_id_hex)
    if hex_key is None:
        _logger.error(
            "ARTIFACT_SIGNING_KEY_ACTIVE '%s' not found in ARTIFACT_SIGNING_KEYS; "
            "skipping Parquet signing: %s",
            active_key_id_hex,
            parquet_name,
        )
        return

    try:
        key_id_bytes = bytes.fromhex(active_key_id_hex)
        signing_key = bytes.fromhex(hex_key)
    except ValueError:
        _logger.error(
            "Malformed key ID or key hex in signing config; skipping Parquet signing: %s",
            parquet_name,
        )
        return

    if len(signing_key) == 0:
        _logger.error(
            "Active signing key decoded to empty bytes; skipping Parquet signing: %s",
            parquet_name,
        )
        return

    parquet_bytes = Path(parquet_path).read_bytes()
    sig = sign_versioned(key=signing_key, key_id=key_id_bytes, data=parquet_bytes)

    sig_path = parquet_path + ".sig"
    Path(sig_path).write_bytes(sig)
    _logger.info(
        "Parquet artifact HMAC-SHA256 signed (versioned, key_id=%s); sidecar written to %s",
        active_key_id_hex,
        Path(sig_path).name,
    )
