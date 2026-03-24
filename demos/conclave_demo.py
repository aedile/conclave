"""Conclave Engine — Interactive Demo Wrapper (T52.1).

Orchestrates a complete synthesis pipeline via direct Python imports.
Designed for interactive use in demos, notebooks, and offline walkthroughs.

Security requirements:
  - Uses an isolated SQLite budget ledger (never the production PostgreSQL ledger).
  - MUST pass a signing_key to ModelArtifact.load(); loading without a key is
    forbidden to prevent silent downgrade attacks on pickle artifacts.
  - Never calls any external API; all imports are from synth_engine or stdlib.

Usage::

    from demos.conclave_demo import run_demo
    run_demo(signing_key=b"your-32-byte-signing-key-here!!!")

Task: P52-T52.1 — Benchmark Infrastructure
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# sys.path adjustment — allows direct execution and notebook imports
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict  # type: ignore[assignment]


class RunDemoResult(TypedDict):
    """Return value contract for :func:`run_demo`.

    Attributes:
        synthetic_df: The generated synthetic DataFrame.
        actual_epsilon: Privacy budget consumed (epsilon value).
        artifact_path: Path to the saved model artifact, or a placeholder
            string when a temporary directory was used and has been cleaned up.
        row_count: Number of rows in the synthetic DataFrame.
    """

    synthetic_df: pd.DataFrame
    actual_epsilon: float
    artifact_path: str
    row_count: int


def run_demo(
    signing_key: bytes,
    *,
    n_rows: int = 100,
    epochs: int = 5,
    noise_multiplier: float = 1.0,
    output_dir: str | None = None,
) -> RunDemoResult:
    """Run a minimal synthesis demo using isolated SQLite-backed budget tracking.

    Generates synthetic data from a fictional dataset, saves and reloads the
    model artifact (with signing key), and returns a summary of the run.

    Args:
        signing_key: Raw signing key bytes (must be >= 32 bytes).  Passed
            directly to ``ModelArtifact.load()`` — loading without a key is
            forbidden to prevent silent downgrade attacks.
        n_rows: Number of synthetic rows to generate (default: 100).
        epochs: Training epochs (default: 5 — low for demo speed).
        noise_multiplier: Opacus noise multiplier (default: 1.0).
        output_dir: Directory to write the demo artifact.  Uses a temp
            directory if not provided.

    Returns:
        RunDemoResult with keys: synthetic_df (DataFrame), actual_epsilon (float),
        artifact_path (str), row_count (int).

    Raises:
        ValueError: If signing_key is empty or shorter than 32 bytes.
        ImportError: If the synthesizer group is not installed.
        RuntimeError: If the model artifact cannot be reloaded after saving.
    """
    if not signing_key:
        raise ValueError("signing_key must not be empty.")
    if len(signing_key) < 32:
        raise ValueError(f"signing_key must be at least 32 bytes; got {len(signing_key)} bytes.")

    import warnings

    try:
        import pandas as pd
        from faker import Faker
    except ImportError as exc:
        raise ImportError(
            f"Missing required dependency: {exc}. Run: poetry install --with dev,synthesizer"
        ) from exc

    try:
        from synth_engine.modules.privacy.dp_engine import DPTrainingWrapper
        from synth_engine.modules.synthesizer.dp_accounting import _DP_EPSILON_DELTA
        from synth_engine.modules.synthesizer.dp_training import DPCompatibleCTGAN
        from synth_engine.modules.synthesizer.models import ModelArtifact
    except ImportError as exc:
        raise ImportError(
            f"synth_engine import failed: {exc}. "
            "Ensure you are running from the repo root with synthesizer group installed."
        ) from exc

    # Generate a small fictional dataset (no PII, no real data)
    fake = Faker()
    fake.seed_instance(42)
    data = [
        {
            "age": fake.random_int(min=18, max=70),
            "salary": fake.random_int(min=30_000, max=150_000),
            "department": fake.random_element(["Engineering", "Sales", "HR", "Finance"]),
        }
        for _ in range(n_rows)
    ]
    source_df = pd.DataFrame(data)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        try:
            from sdv.metadata import SingleTableMetadata
        except ImportError as exc:
            raise ImportError(
                f"SDV not installed: {exc}. Run: poetry install --with synthesizer"
            ) from exc

        meta = SingleTableMetadata()
        meta.detect_from_dataframe(source_df)

        wrapper = DPTrainingWrapper(
            max_grad_norm=1.0,
            noise_multiplier=noise_multiplier,
        )
        model = DPCompatibleCTGAN(
            metadata=meta,
            epochs=epochs,
            dp_wrapper=wrapper,
        )
        model.fit(source_df)
        synth_df = model.sample(n_rows)
        # Use production delta for epsilon accounting to match benchmark harness
        actual_epsilon = wrapper.epsilon_spent(delta=_DP_EPSILON_DELTA)

    # Resolve output directory
    tmp_cleanup: tempfile.TemporaryDirectory[str] | None = None
    if output_dir is None:
        tmp_cleanup = tempfile.TemporaryDirectory()
        output_dir = tmp_cleanup.name

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Save model artifact with signing key
    artifact = ModelArtifact(
        table_name="demo_table",
        model=model,
        column_names=list(source_df.columns),
        column_dtypes={col: str(source_df[col].dtype) for col in source_df.columns},
        column_nullables={col: bool(source_df[col].isnull().any()) for col in source_df.columns},
    )
    artifact_path = artifact.save(str(out_path / "demo_model.pkl"), signing_key=signing_key)

    # Reload artifact — MUST pass signing_key; omitting it is forbidden
    _loaded = ModelArtifact.load(artifact_path, signing_key=signing_key)
    if _loaded is None:
        raise RuntimeError(
            "ModelArtifact.load() returned None — artifact may be corrupt or the "
            "signing key does not match the key used to save the artifact."
        )

    result: RunDemoResult = {
        "synthetic_df": synth_df,
        "actual_epsilon": actual_epsilon,
        "artifact_path": artifact_path,
        "row_count": len(synth_df),
    }

    # Clean up temp directory if we created one
    if tmp_cleanup is not None:
        tmp_cleanup.cleanup()
        result["artifact_path"] = "(temp directory cleaned up)"

    return result
