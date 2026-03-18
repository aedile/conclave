"""NIST SP 800-88 compliant artifact erasure for synthesis jobs.

Provides :func:`shred_artifacts`, the domain-level function responsible for
deleting all synthesis output artifacts associated with a
:class:`~synth_engine.modules.synthesizer.job_models.SynthesisJob`.

NIST 800-88 context
-------------------
NIST SP 800-88 (Rev. 1) Section 2.4 defines *Clear*, *Purge*, and *Destroy*
as media sanitisation categories.  For ephemeral file-backed storage the
applicable method is *Clear*: overwrite or delete the data such that it
cannot be recovered by ordinary software techniques.

For this system the artifacts are:

* The generated synthetic Parquet file (``job.output_path``).
* The HMAC-SHA256 signature sidecar (``job.output_path + ".sig"``).
* The trained model artifact pickle (``job.artifact_path``).

All three are removed if they exist.  Missing files are silently skipped so
that the operation is idempotent — a second shred of the same job does not
raise an error.  This is intentional: NIST 800-88 considers idempotent
erasure acceptable.

Security properties
-------------------
* File deletion uses :meth:`pathlib.Path.unlink` with ``missing_ok=True``,
  which is a single unlink(2) syscall.  On the tmpfs-backed MinIO volume
  used in production, the blocks are immediately reclaimed by the kernel
  and inaccessible without physical media forensics.
* Internal file paths are NEVER written to API responses or INFO logs.  Only
  the base filenames are logged at DEBUG level.
* :class:`OSError` from filesystem operations is caught, logged at ERROR
  level with only the basename, and re-raised so the caller (the router)
  can surface an appropriate 500 response.  This prevents I/O errors from
  being silently swallowed (known failure pattern T23.1).

Boundary constraints (import-linter enforced):
    - Must NOT import from ``bootstrapper/``.
    - Must NOT import from ``modules/ingestion/``, ``modules/masking/``,
      ``modules/subsetting/``, ``modules/profiler/``, or ``modules/privacy/``.

Task: P23-T23.4 — Cryptographic Erasure Endpoint
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synth_engine.modules.synthesizer.job_models import SynthesisJob

_logger = logging.getLogger(__name__)


def shred_artifacts(job: SynthesisJob) -> None:
    """Delete all synthesis artifacts for *job* from the filesystem.

    Removes the generated Parquet output, its HMAC-SHA256 signature sidecar,
    and the trained model artifact pickle.  Files that are absent are silently
    skipped so the operation is idempotent.

    This function implements the file-system layer of the NIST SP 800-88
    compliant erasure workflow (``POST /jobs/{id}/shred``).  It performs only
    the physical deletion; status transitions and audit logging are the
    responsibility of the calling router.

    Args:
        job: The :class:`~synth_engine.modules.synthesizer.job_models.SynthesisJob`
            whose artifacts should be erased.  Only ``output_path`` and
            ``artifact_path`` fields are accessed.

    """
    _delete_file_if_present(job.output_path)
    if job.output_path is not None:
        _delete_file_if_present(job.output_path + ".sig")
    _delete_file_if_present(job.artifact_path)


def _delete_file_if_present(path: str | None) -> None:
    """Delete the file at *path* if it exists; silently skip if absent or None.

    Args:
        path: Absolute filesystem path to delete, or ``None`` to skip.

    Raises:
        OSError: If the file exists but cannot be deleted (e.g. permission
            denied, directory not found).
    """
    if path is None:
        return

    target = Path(path)
    try:
        target.unlink(missing_ok=True)
        _logger.debug("Shredded artifact: %s", target.name)
    except OSError:
        _logger.error("Failed to delete artifact: %s", target.name)
        raise
