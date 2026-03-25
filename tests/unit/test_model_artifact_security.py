"""Unit tests for RestrictedUnpickler — attack tests for pickle deserialization security.

Tests verify that the restricted unpickler:
1. REJECTS crafted pickle payloads containing dangerous classes (os.system,
   subprocess.Popen, arbitrary __reduce__).
2. ACCEPTS legitimate ModelArtifact pickles.
3. Preserves HMAC verification before unpickling (existing behavior).

CONSTITUTION Priority 0: Security — pickle deserialization is a critical attack surface
CONSTITUTION Priority 3: TDD — attack tests committed before feature tests
Task: T55.2 — Replace Pickle with Safe Serialization (Restricted Unpickler)
"""

from __future__ import annotations

import os
import pickle

import pytest

from synth_engine.modules.synthesizer.storage.models import ModelArtifact
from synth_engine.shared.security.hmac_signing import SecurityError

# ---------------------------------------------------------------------------
# Helpers: craft malicious pickle payloads without executing them
# ---------------------------------------------------------------------------


def _craft_os_system_pickle(cmd: str = "echo pwned") -> bytes:
    """Craft a pickle payload that calls os.system(cmd) on unpickling.

    Uses the pickle REDUCE opcode to invoke os.system.  This simulates an
    attacker-crafted artifact payload targeting a pre-RCE pickle.loads call.

    Args:
        cmd: Shell command to embed in the payload.

    Returns:
        Raw pickle bytes.
    """
    # Build the payload using the low-level opcode approach
    # (construct raw bytes directly to avoid executing the payload)
    # GLOBAL 'os' 'system' + MARK + STRING cmd + TUPLE + REDUCE + STOP
    payload = (
        b"\x80\x02"  # PROTO 2
        b"c" + b"os\nsystem\n"  # GLOBAL os.system
        b"q\x00"  # BINPUT 0
        b"(" + cmd.encode() + b"\n"  # MARK + STRING
        b"\x85"  # TUPLE1
        b"R"  # REDUCE
        b"."  # STOP
    )
    return payload


def _craft_subprocess_pickle(cmd: str = "id") -> bytes:
    """Craft a pickle payload that invokes subprocess.Popen on unpickling.

    Args:
        cmd: Command to embed.

    Returns:
        Raw pickle bytes.
    """
    payload = (
        b"\x80\x02"  # PROTO 2
        b"c" + b"subprocess\nPopen\n"  # GLOBAL subprocess.Popen
        b"q\x00"  # BINPUT 0
        b"(" + cmd.encode() + b"\n"  # MARK + STRING
        b"\x85"  # TUPLE1
        b"R"  # REDUCE
        b"."  # STOP
    )
    return payload


def _craft_arbitrary_class_pickle(module: str, classname: str) -> bytes:
    """Craft a pickle payload that references an arbitrary non-allowlisted class.

    Args:
        module: Python module path (e.g. "evil_module").
        classname: Class name within that module.

    Returns:
        Raw pickle bytes.
    """
    payload = (
        b"\x80\x02"  # PROTO 2
        + b"c"
        + module.encode()
        + b"\n"
        + classname.encode()
        + b"\n"  # GLOBAL module.classname
        + b"q\x00"  # BINPUT 0
        + b"."  # STOP
    )
    return payload


def _make_signed_payload(signing_key: bytes, pickle_payload: bytes) -> bytes:
    """Prepend an HMAC-SHA256 signature to a pickle payload.

    Args:
        signing_key: 32-byte signing key.
        pickle_payload: Raw pickle bytes.

    Returns:
        Signed artifact bytes (HMAC || pickle).
    """
    from synth_engine.shared.security.hmac_signing import compute_hmac

    signature = compute_hmac(signing_key, pickle_payload)
    return signature + pickle_payload


# ---------------------------------------------------------------------------
# Attack tests — MUST fail (RED) before RestrictedUnpickler exists
# ---------------------------------------------------------------------------


def test_restricted_unpickler_rejects_os_system() -> None:
    """Crafted pickle containing os.system MUST raise SecurityError.

    An attacker who compromises the artifact store can inject a pickle payload
    that executes arbitrary OS commands.  The restricted unpickler MUST reject
    any reference to os.system before any bytecode is executed.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    payload = _craft_os_system_pickle("echo pwned")
    with pytest.raises(SecurityError, match="not permitted"):
        RestrictedUnpickler.loads(payload)


def test_restricted_unpickler_rejects_subprocess_popen() -> None:
    """Crafted pickle containing subprocess.Popen MUST raise SecurityError.

    subprocess.Popen can spawn arbitrary processes.  No legitimate ModelArtifact
    payload references subprocess.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    payload = _craft_subprocess_pickle("id")
    with pytest.raises(SecurityError, match="not permitted"):
        RestrictedUnpickler.loads(payload)


def test_restricted_unpickler_rejects_arbitrary_module() -> None:
    """Crafted pickle with non-allowlisted module MUST raise SecurityError.

    Any module not in the explicit allowlist must be rejected, regardless of
    whether it exists or could be imported.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    payload = _craft_arbitrary_class_pickle("evil_library", "ExploitClass")
    with pytest.raises(SecurityError, match="not permitted"):
        RestrictedUnpickler.loads(payload)


def test_restricted_unpickler_rejects_pathlib() -> None:
    """pathlib.Path is not in the allowlist and MUST be rejected.

    Standard library classes that are not needed for ModelArtifact
    deserialization must be blocked by default.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    payload = _craft_arbitrary_class_pickle("pathlib", "Path")
    with pytest.raises(SecurityError, match="not permitted"):
        RestrictedUnpickler.loads(payload)


def test_restricted_unpickler_rejects_importlib() -> None:
    """importlib is not in the allowlist and MUST be rejected.

    importlib can be used to dynamically load and execute arbitrary modules.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    payload = _craft_arbitrary_class_pickle("importlib", "import_module")
    with pytest.raises(SecurityError, match="not permitted"):
        RestrictedUnpickler.loads(payload)


# ---------------------------------------------------------------------------
# Feature tests — valid ModelArtifact round-trip through RestrictedUnpickler
# ---------------------------------------------------------------------------


def test_restricted_unpickler_accepts_model_artifact() -> None:
    """A valid ModelArtifact pickle MUST load successfully via RestrictedUnpickler.

    The primary use case: save a ModelArtifact and reload it.  The restricted
    unpickler must not break legitimate round-trip serialization.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    artifact = ModelArtifact(
        table_name="test_table",
        model=None,
        column_names=["id", "name"],
        column_dtypes={"id": "int64", "name": "object"},
        column_nullables={"id": False, "name": True},
    )
    payload = pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL)
    loaded = RestrictedUnpickler.loads(payload)

    assert isinstance(loaded, ModelArtifact)
    assert loaded.table_name == "test_table"
    assert loaded.column_names == ["id", "name"]
    assert loaded.column_dtypes == {"id": "int64", "name": "object"}
    assert loaded.column_nullables == {"id": False, "name": True}


def test_model_artifact_load_uses_restricted_unpickler(tmp_path: object) -> None:
    """ModelArtifact.load() MUST use RestrictedUnpickler, not plain pickle.loads.

    Verify that when ModelArtifact.load() processes a pickle payload, it goes
    through the restricted path and rejects malicious classes.
    """
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    signing_key = os.urandom(32)
    artifact_path = str(tmp_path / "artifact.pkl")

    # Save a legitimate artifact
    artifact = ModelArtifact(
        table_name="customers",
        model=None,
        column_names=["id"],
        column_dtypes={"id": "int64"},
        column_nullables={"id": False},
    )
    artifact.save(artifact_path, signing_key=signing_key)

    # Load should succeed (goes through RestrictedUnpickler)
    loaded = ModelArtifact.load(artifact_path, signing_key=signing_key)
    assert loaded.table_name == "customers"
    assert loaded.column_names == ["id"]


def test_model_artifact_load_rejects_malicious_signed_payload(tmp_path: object) -> None:
    """ModelArtifact.load() MUST reject malicious payload even when HMAC is valid.

    Scenario: attacker has obtained the signing key and uses it to sign a
    malicious pickle payload.  The HMAC would verify successfully, but the
    RestrictedUnpickler must still reject the non-allowlisted class.
    """
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    signing_key = os.urandom(32)
    artifact_path = str(tmp_path / "evil_artifact.pkl")

    # Craft a malicious payload and sign it with the "stolen" key
    malicious_payload = _craft_os_system_pickle("echo pwned")
    signed_data = _make_signed_payload(signing_key, malicious_payload)

    with open(artifact_path, "wb") as f:
        f.write(signed_data)

    # Should raise SecurityError from RestrictedUnpickler, not execute os.system
    with pytest.raises(SecurityError):
        ModelArtifact.load(artifact_path, signing_key=signing_key)


def test_hmac_still_verified_before_unpickling(tmp_path: object) -> None:
    """HMAC verification MUST still occur before RestrictedUnpickler runs.

    The defense-in-depth order is: HMAC check first, restricted unpickle second.
    A tampered payload (wrong HMAC) must be rejected at the HMAC stage, not the
    unpickle stage — the unpickler must never run on an unverified payload.
    """
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    signing_key = os.urandom(32)
    artifact_path = str(tmp_path / "tampered_artifact.pkl")

    # Create a valid artifact and save it
    artifact = ModelArtifact(table_name="t", model=None)
    artifact.save(artifact_path, signing_key=signing_key)

    # Tamper with the payload (flip bytes after the HMAC header)
    with open(artifact_path, "rb") as f:
        data = bytearray(f.read())

    # Flip the last byte — this corrupts the pickle payload but not the HMAC header
    data[-1] ^= 0xFF
    with open(artifact_path, "wb") as f:
        f.write(data)

    # Must raise SecurityError from HMAC failure, not from RestrictedUnpickler
    with pytest.raises(SecurityError, match="HMAC verification failed"):
        ModelArtifact.load(artifact_path, signing_key=signing_key)


def test_restricted_unpickler_accepts_builtin_types() -> None:
    """Basic builtins (dict, list, str, int) MUST be accepted by RestrictedUnpickler.

    The allowlist includes all standard Python builtins needed for the
    dataclass fields in ModelArtifact.
    """
    from synth_engine.modules.synthesizer.storage.models import RestrictedUnpickler

    # A simple dict containing common types
    data = {"key": "value", "count": 42, "items": [1, 2, 3], "flag": True}
    payload = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
    loaded = RestrictedUnpickler.loads(payload)

    assert loaded == data
    assert loaded["key"] == "value"
    assert loaded["count"] == 42
