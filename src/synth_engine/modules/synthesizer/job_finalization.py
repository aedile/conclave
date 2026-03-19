"""Artifact persistence helpers for synthesis jobs.

Handles writing Parquet outputs and HMAC-SHA256 signing sidecars.  Called by
``job_orchestration._generate_and_finalize`` after CTGAN training completes.

Split from ``tasks.py`` in P26-T26.1 to improve module focus.

Boundary constraints (import-linter enforced):
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.
    - Must NOT import from ``bootstrapper/``.

Task: P26-T26.1 — Split Oversized Files (Refactor Only)
"""

from __future__ import annotations

import logging
from pathlib import Path

from synth_engine.shared.security.hmac_signing import HMAC_DIGEST_SIZE, compute_hmac

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

    Reads ``ARTIFACT_SIGNING_KEY`` from the environment.  If set, the value
    is decoded as a hex string, the raw Parquet bytes are read back, the
    HMAC-SHA256 digest is computed via
    :func:`synth_engine.shared.security.hmac_signing.compute_hmac`, and the
    32-byte digest is written to ``parquet_path + '.sig'``.

    If the key is absent or empty, the Parquet file is written unsigned and a
    WARNING is logged (unsigned artifacts are acceptable in development per the
    T23.1 spec).

    If the key is present but malformed (non-hex characters or odd length),
    the ``ValueError`` from ``bytes.fromhex`` is caught, an ERROR is logged
    (T38.4: elevated from WARNING — malformed key is a security-relevant
    misconfiguration), and signing is skipped — the Parquet file has already
    been written and the job continues normally.

    Args:
        df: A :class:`pandas.DataFrame` to serialise as Parquet.  Typed as
            ``object`` to avoid a hard ``pandas`` import at module level; the
            caller (``_generate_and_finalize``) guarantees a real DataFrame.
        parquet_path: Destination filesystem path (must end with ``.parquet``).

    Raises:
        OSError: If the Parquet write or sidecar write fails.

    Note:
        If ARTIFACT_SIGNING_KEY is present but contains invalid hex
        characters or has an odd length, the :exc: raised by
        bytes.fromhex() is caught internally and signing is skipped with
        an ERROR log.  The Parquet file is already written at that point
        and the job continues normally.  This is the function's primary
        defensive behavior.

    """  # noqa: DOC502
    # F5 fix: log basename only — full paths may expose internal filesystem layout.
    parquet_name = Path(parquet_path).name

    # df.to_parquet() is the pandas DataFrame API.  We call it duck-typed to
    # keep pandas as an optional import at module level; mypy is satisfied by
    # the ``object`` annotation with ``type: ignore`` at the call site.
    df.to_parquet(parquet_path, index=False)  # type: ignore[attr-defined]  # duck-typed pandas DataFrame; guaranteed by caller
    _logger.debug("Parquet artifact written: %s", parquet_name)

    from synth_engine.shared.settings import get_settings

    signing_key_hex = get_settings().artifact_signing_key
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
        "Parquet artifact HMAC-SHA256 signed; sidecar written to %s",
        Path(sig_path).name,
    )
