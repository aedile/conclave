"""Unit tests for synthesizer task error handling, OOM rejection, and failure paths.

Covers: OOM guardrail rejection, RuntimeError during training, job-not-found guard,
Parquet HMAC signing edge cases, step-9 OSError handling, generation failure sanitization,
audit logger failure after budget deduction, and num_rows validation.

All tests are isolated (no real DB, no real Huey worker, no network I/O).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
from tests.unit.helpers_synthesizer import _make_synthesis_job

# ---------------------------------------------------------------------------
# OOM guardrail rejection path
# ---------------------------------------------------------------------------


class TestSynthesisTaskOOMRejection:
    """Unit tests for OOM guardrail rejection: guardrail fails → FAILED status."""

    def test_oom_guardrail_rejection_sets_failed_status(self) -> None:
        """When OOM guardrail rejects, task must set status=FAILED."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
        from synth_engine.modules.synthesizer.training.guardrails import OOMGuardrailError

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("6.8 GiB estimated, 4.0 GiB available"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"

    def test_oom_guardrail_rejection_sets_error_msg(self) -> None:
        """When OOM guardrail rejects, task must record the guardrail error message."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
        from synth_engine.modules.synthesizer.training.guardrails import OOMGuardrailError

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        oom_msg = "6.8 GiB estimated, 4.0 GiB available -- reduce dataset by 2.00x"
        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError(oom_msg),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        assert oom_msg in job.error_msg

    def test_oom_guardrail_rejection_never_calls_train(self) -> None:
        """When OOM guardrail rejects, engine.train() must never be called."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
        from synth_engine.modules.synthesizer.training.guardrails import OOMGuardrailError

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        mock_engine.train.assert_not_called()

    def test_oom_guardrail_rejection_commits_failed_status(self) -> None:
        """OOM rejection must commit the FAILED status to the database."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
        from synth_engine.modules.synthesizer.training.guardrails import OOMGuardrailError

        mock_session = MagicMock()
        job = _make_synthesis_job(id=2, status="QUEUED", total_epochs=100, checkpoint_every_n=5)
        mock_session.get.return_value = job

        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility",
            side_effect=OOMGuardrailError("too big"),
        ):
            _run_synthesis_job_impl(
                job_id=2,
                session=mock_session,
                engine=mock_engine,
            )

        # session.commit() must be called at least once to persist FAILED status
        assert mock_session.commit.call_count >= 1


# ---------------------------------------------------------------------------
# RuntimeError mid-training failure
# ---------------------------------------------------------------------------


class TestSynthesisTaskRuntimeFailure:
    """Unit tests for RuntimeError during training.

    Verifies: task sets FAILED status, error message is recorded, and the
    checkpoint for the last completed epoch exists in storage.
    """

    def test_runtime_error_sets_failed_status(self) -> None:
        """RuntimeError during training must set status=FAILED."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA out of memory at epoch 3")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"

    def test_runtime_error_sets_error_msg(self) -> None:
        """RuntimeError during training must record the error message."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("CUDA out of memory at epoch 3")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        assert "CUDA out of memory" in job.error_msg

    def test_checkpoint_saved_before_failure(self) -> None:
        """Checkpoint for the last completed batch must exist in storage after failure.

        Training is mocked to complete the first call (epoch batch 1) then fail
        on the second call (epoch batch 2).  Storage must have been called at
        least once to persist the epoch-3 checkpoint.

        The checkpoint_every_n=3 means a checkpoint is saved after epoch 3
        (the first checkpoint boundary).  When train() raises on the second
        call, the first checkpoint must already be in storage.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        # total_epochs=6, checkpoint_every_n=3 → checkpoints at epoch 3 and 6
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=6, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        # First train() call (epochs 1-3) succeeds; second (epochs 4-6) raises
        first_artifact = MagicMock()
        first_artifact.save.return_value = "/artifacts/job3_epoch3.pkl"
        mock_engine.train.side_effect = [first_artifact, RuntimeError("OOM at epoch 5")]

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

        # Artifact must have been saved at least once (epoch-3 checkpoint)
        assert first_artifact.save.call_count >= 1

    def test_failed_job_commits_to_db(self) -> None:
        """RuntimeError path must commit FAILED status to the database."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(id=3, status="QUEUED", total_epochs=5, checkpoint_every_n=3)
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_engine.train.side_effect = RuntimeError("failed")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=3,
                session=mock_session,
                engine=mock_engine,
            )

        assert mock_session.commit.call_count >= 1

    def test_total_epochs_zero_marks_job_failed(self) -> None:
        """_run_synthesis_job_impl must mark job FAILED when total_epochs=0.

        total_epochs=0 skips the training while-loop entirely, leaving
        last_ckpt_path as None.  The step-6 guard must catch this and set
        status=FAILED with an error_msg containing 'No artifact produced'.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=99,
            status="QUEUED",
            total_epochs=0,
            checkpoint_every_n=5,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job
        mock_engine = MagicMock()

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=99,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"
        assert job.error_msg is not None
        assert "No artifact produced" in job.error_msg, (
            f"Expected 'No artifact produced' in error_msg; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# Job not found
# ---------------------------------------------------------------------------


class TestSynthesisJobNotFound:
    """Verify task handles missing job ID gracefully."""

    def test_task_raises_if_job_not_found(self) -> None:
        """_run_synthesis_job_impl must raise ValueError when job ID is not in DB."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        mock_session.get.return_value = None  # Job not found

        mock_engine = MagicMock()

        with pytest.raises(ValueError, match="SynthesisJob.*not found"):
            _run_synthesis_job_impl(
                job_id=999,
                session=mock_session,
                engine=mock_engine,
            )


# ---------------------------------------------------------------------------
# T23.1 — HMAC signing of Parquet artifact (RED)
# ---------------------------------------------------------------------------


class TestParquetHMACSigning:
    """When ARTIFACT_SIGNING_KEY is set, the Parquet output must be HMAC-signed (AC3)."""

    def test_parquet_written_unsigned_when_no_signing_key(self) -> None:
        """When ARTIFACT_SIGNING_KEY is not set, Parquet is written without a signature."""
        import os

        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=70,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"y": range(4)})

        env_without_key = {k: v for k, v in os.environ.items() if k != "ARTIFACT_SIGNING_KEY"}

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch.dict("os.environ", env_without_key, clear=True),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=70,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # File must exist and be a readable Parquet — assertions inside the
            # with block so the tmpdir has not yet been cleaned up.
            assert job.output_path is not None
            df_loaded = pd.read_parquet(job.output_path)
            assert len(df_loaded) == 4

    def test_parquet_sidecar_sig_file_written_when_signing_key_set(self) -> None:
        """When ARTIFACT_SIGNING_KEY is set, a .sig sidecar must be written alongside the Parquet.

        The sidecar file path is output_path + '.sig'.
        The .sig file must contain a 32-byte HMAC-SHA256 digest.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl
        from synth_engine.shared.security.hmac_signing import HMAC_DIGEST_SIZE

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=71,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"y": range(4)})

        # A 32-byte key expressed as 64 hex chars.
        signing_key_hex = "a" * 64

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": signing_key_hex}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            _run_synthesis_job_impl(
                job_id=71,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # Assertions inside the with block so tmpdir persists during checks.
            assert job.output_path is not None, "output_path must be set"
            sig_path = job.output_path + ".sig"
            assert Path(sig_path).exists(), f"Sidecar .sig file must exist at {sig_path!r}"
            sig_bytes = Path(sig_path).read_bytes()
            assert len(sig_bytes) == HMAC_DIGEST_SIZE, (
                f"Signature must be {HMAC_DIGEST_SIZE} bytes; got {len(sig_bytes)}"
            )


# ---------------------------------------------------------------------------
# T23.1 review findings — new edge case tests (RED phase, P23-T23.1)
# ---------------------------------------------------------------------------


class TestWriteParquetWithSigningEdgeCases:
    """Edge-case tests for _write_parquet_with_signing (review findings F2, F5, F8)."""

    def test_malformed_hex_signing_key_skips_signing_gracefully(self) -> None:
        """ARTIFACT_SIGNING_KEY with non-hex chars must skip signing without raising.

        Finding F2: bytes.fromhex() raises ValueError on malformed input.
        After the fix, ValueError is caught and signing is skipped gracefully
        (no crash, Parquet file still written).
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=80,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"col": range(4)})

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": "not-valid-hex"}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            # Must not raise — malformed key should be handled gracefully.
            _run_synthesis_job_impl(
                job_id=80,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            # Job still completes and Parquet file is written.
            assert job.status == "COMPLETE", (
                f"Malformed signing key must not prevent COMPLETE; got {job.status}"
            )
            assert job.output_path is not None
            assert Path(job.output_path).exists(), (
                "Parquet must still be written when signing key is malformed"
            )
            # No .sig sidecar should exist — signing was skipped.
            sig_path = job.output_path + ".sig"
            assert not Path(sig_path).exists(), (
                "No .sig sidecar should be written when signing key is malformed"
            )

    def test_whitespace_only_signing_key_skips_signing(self) -> None:
        """ARTIFACT_SIGNING_KEY containing only whitespace skips signing gracefully.

        bytes.fromhex('   ') raises ValueError (odd-length string after stripping
        is still non-hex).  After fix F2, this must skip signing without crashing.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=81,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=4,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"col": range(4)})

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch.dict("os.environ", {"ARTIFACT_SIGNING_KEY": "   "}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            # Must not raise.
            _run_synthesis_job_impl(
                job_id=81,
                session=mock_session,
                engine=mock_engine,
                checkpoint_dir=tmpdir,
            )

            assert job.status == "COMPLETE", (
                f"Whitespace signing key must not prevent COMPLETE; got {job.status}"
            )
            assert job.output_path is not None
            assert Path(job.output_path).exists()


# ---------------------------------------------------------------------------
# Audit log failure after budget deduction (T38.1, Constitution Priority 0)
# ---------------------------------------------------------------------------


class TestAuditLoggerFailureAfterBudgetDeduction:
    """Audit log failure after budget deduction MUST fail the job (T38.1, Constitution Priority 0).

    T38.1 fixes finding F9: the old behavior silently completed the job when the WORM audit
    write failed.  Under Constitution Priority 0 (Security), every privacy budget spend MUST
    have an immutable WORM audit entry.  If the audit infrastructure is broken, the job output
    must NOT be delivered — the operator must reconcile the spend manually.
    """

    def test_audit_logger_exception_does_not_block_complete(self) -> None:
        """When audit log_event() raises after budget deduction, job must be FAILED.

        T38.1: Budget has been spent but no audit record was written.  The job
        must be marked FAILED so operators know manual reconciliation is required.
        The error message must include the reconciliation notice.
        """
        import synth_engine.modules.synthesizer.jobs.job_orchestration as orch_mod
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        job = _make_synthesis_job(
            id=85,
            status="QUEUED",
            total_epochs=5,
            checkpoint_every_n=5,
            enable_dp=True,
            actual_epsilon=None,
            num_rows=3,
        )
        mock_session = MagicMock()
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact

        dp_wrapper = MagicMock()
        dp_wrapper.epsilon_spent.return_value = 1.0
        mock_budget_fn = MagicMock()  # budget spend succeeds

        # Audit logger raises an exception after budget is deducted.
        mock_audit_logger = MagicMock()
        mock_audit_logger.log_event.side_effect = RuntimeError("Audit DB unavailable")

        original_fn = orch_mod._spend_budget_fn
        try:
            orch_mod.set_spend_budget_fn(mock_budget_fn)
            with (
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
                ),
                patch(
                    "synth_engine.modules.synthesizer.jobs.job_orchestration.get_audit_logger",
                    return_value=mock_audit_logger,
                ),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                _run_synthesis_job_impl(
                    job_id=85,
                    session=mock_session,
                    engine=mock_engine,
                    dp_wrapper=dp_wrapper,
                    checkpoint_dir=tmpdir,
                )
        finally:
            orch_mod._spend_budget_fn = original_fn  # type: ignore[assignment]

        # T38.1: job must be FAILED when audit write fails — not COMPLETE (old bug).
        assert job.status == "FAILED", (
            f"Audit logger failure must set job to FAILED (T38.1); got {job.status}"
        )
        assert job.error_msg is not None
        assert "audit trail write failed" in job.error_msg.lower(), (
            f"Error message must mention audit trail failure; got {job.error_msg!r}"
        )
        assert "manual reconciliation" in job.error_msg.lower(), (
            f"Error message must mention manual reconciliation; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# Step 9 OSError transitions FAILED (finding F1)
# ---------------------------------------------------------------------------


class TestStep9OSErrorTransitionsFailed:
    """Step 9 OSError during Parquet write must transition job to FAILED (finding F1).

    Before fix F1, an OSError from _write_parquet_with_signing() would propagate
    unhandled, leaving the job permanently in GENERATING status.
    After fix F1, the step-9 block catches OSError and sets FAILED.
    """

    def test_oserror_in_write_parquet_sets_job_failed(self) -> None:
        """OSError during _write_parquet_with_signing must transition job to FAILED."""
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=90,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("Disk full"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=90,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", (
            f"OSError in step 9 must set job.status=FAILED; got {job.status!r}"
        )
        assert job.error_msg is not None, "error_msg must be set on OSError failure"

    def test_oserror_in_write_parquet_commits_failed_status(self) -> None:
        """OSError in step 9 must commit FAILED status to the database."""
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=91,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("No space left on device"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=91,
                session=mock_session,
                engine=mock_engine,
            )

        assert mock_session.commit.call_count >= 1, (
            "session.commit() must be called after OSError to persist FAILED status"
        )

    def test_oserror_error_msg_is_sanitized(self) -> None:
        """OSError in step 9 must set a sanitized error_msg (no internal detail).

        Finding F4: error_msg must not contain raw exception internals.
        """
        import pandas as pd

        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=92,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=5,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.return_value = pd.DataFrame({"x": range(5)})

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.job_orchestration._write_parquet_with_signing",
                side_effect=OSError("internal filesystem error xyz"),
            ),
        ):
            _run_synthesis_job_impl(
                job_id=92,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.error_msg is not None
        # After fix F4: error_msg must be a sanitized static string.
        assert "see server logs" in job.error_msg, (
            f"error_msg must be sanitized; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# Generation RuntimeError sanitization (finding F4)
# ---------------------------------------------------------------------------


class TestGenerationRuntimeErrorSanitized:
    """Generation RuntimeError error_msg must be sanitized (finding F4)."""

    def test_generation_runtime_error_sets_failed(self) -> None:
        """RuntimeError during generation must set job to FAILED."""
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=65,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.side_effect = RuntimeError("generation failed")

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=65,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED", f"Expected FAILED; got {job.status}"
        assert job.error_msg is not None
        # F4 fix: error_msg is now sanitized — raw exception text must not appear.
        assert "see server logs" in job.error_msg, (
            f"Expected sanitized error_msg; got {job.error_msg!r}"
        )

    def test_generation_runtime_error_msg_is_sanitized(self) -> None:
        """RuntimeError during generation must NOT expose raw exception text in error_msg.

        Finding F4 (DevOps): job.error_msg is written verbatim from the exception.
        After fix, error_msg must be a static sanitized string.
        """
        from synth_engine.modules.synthesizer.jobs.job_orchestration import _run_synthesis_job_impl

        mock_session = MagicMock()
        job = _make_synthesis_job(
            id=95,
            status="QUEUED",
            total_epochs=3,
            checkpoint_every_n=5,
            num_rows=10,
        )
        mock_session.get.return_value = job

        mock_engine = MagicMock()
        mock_artifact = MagicMock()
        mock_engine.train.return_value = mock_artifact
        mock_engine.generate.side_effect = RuntimeError(
            "internal/path/to/model.py line 42: segfault"
        )

        with patch(
            "synth_engine.modules.synthesizer.jobs.job_orchestration.check_memory_feasibility"
        ):
            _run_synthesis_job_impl(
                job_id=95,
                session=mock_session,
                engine=mock_engine,
            )

        assert job.status == "FAILED"
        assert job.error_msg is not None
        # Sanitized message — must NOT include internal path details.
        assert "internal/path" not in job.error_msg, (
            f"error_msg must not expose internal exception details; got {job.error_msg!r}"
        )
        assert "see server logs" in job.error_msg, (
            f"error_msg must point to server logs; got {job.error_msg!r}"
        )


# ---------------------------------------------------------------------------
# num_rows validation (finding F3)
# ---------------------------------------------------------------------------


class TestSynthesisJobNumRowsValidation:
    """SynthesisJob must reject num_rows < 1 at construction time (finding F3)."""

    def test_synthesis_job_num_rows_zero_raises(self) -> None:
        """SynthesisJob must reject num_rows=0 with ValueError.

        Finding F3: docstring says 'Must be >= 1' but __init__ does not enforce it.
        """
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with pytest.raises(ValueError, match="num_rows must be >= 1"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                num_rows=0,
            )

    def test_synthesis_job_num_rows_negative_raises(self) -> None:
        """SynthesisJob must reject num_rows=-1 with ValueError."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        with pytest.raises(ValueError, match="num_rows must be >= 1"):
            SynthesisJob(
                total_epochs=10,
                table_name="persons",
                parquet_path="/data/persons.parquet",
                num_rows=-1,
            )

    def test_synthesis_job_num_rows_one_is_valid(self) -> None:
        """SynthesisJob must accept num_rows=1 (minimum valid value)."""
        from synth_engine.modules.synthesizer.jobs.job_models import SynthesisJob

        job = SynthesisJob(
            total_epochs=10,
            table_name="persons",
            parquet_path="/data/persons.parquet",
            num_rows=1,
        )
        assert job.num_rows == 1
