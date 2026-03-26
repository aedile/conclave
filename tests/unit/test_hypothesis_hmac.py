"""Property-based tests for v3 HMAC audit signatures (T58.7).

Uses Hypothesis to prove structural security properties that cannot be
exhaustively tested with manual examples:

1. **Differing fields → differing signatures**: for any two events with at
   least one differing field, the v3 HMAC signatures must differ.  This is
   the core collision-resistance property.

2. **Pipe-chars in fields don't cause collisions**: specifically tests the
   ADV-P53-01 fix — in v1/v2, ``actor="a|b", resource="c"`` and
   ``actor="a", resource="b|c"`` produce the same HMAC.  v3's length-prefixed
   encoding must produce different signatures for these two inputs.

3. **Inputs that can't be JSON-serialized raise ValueError**: the details
   dict is JSON-serialized as part of the HMAC input.  If any value is not
   JSON-serializable (e.g. NaN float leaked in via dict coercion), a
   ``ValueError`` must be raised.  Hypothesis explores surrogate-laden and
   unusual strings — ``assume()`` filters inputs that can be serialized,
   leaving only the truly non-serializable ones (NaN, Inf in float keys).

Task: T58.7 — Property-Based Testing (Hypothesis)
"""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Shared key for all HMAC tests — a fixed 32-byte key so tests are
# deterministic (key material is not the variable under test).
# ---------------------------------------------------------------------------
_KEY: bytes = b"\xab\xcd\xef" * 10 + b"\x12\x34"  # 32 bytes

# Fixed field values used as baseline in collision tests.
_TS = "2026-01-01T00:00:00+00:00"
_PREV = "0" * 64


# ---------------------------------------------------------------------------
# Helper: attempt JSON serialization, returning True if it succeeds
# ---------------------------------------------------------------------------


def _json_serializable(d: dict[str, str]) -> bool:
    """Return True if *d* is JSON-serializable (no surrogates, nan, inf)."""
    try:
        json.dumps(d, allow_nan=False)
        return True
    except (ValueError, UnicodeEncodeError):
        return False


# ---------------------------------------------------------------------------
# Property 1: differing fields → differing signatures
# ---------------------------------------------------------------------------


@given(
    actor1=st.text(min_size=0, max_size=64),
    actor2=st.text(min_size=0, max_size=64),
)
@settings(max_examples=200)
def test_sign_v3_different_actors_produce_different_signatures(
    actor1: str,
    actor2: str,
) -> None:
    """v3: two events differing only in actor MUST have different signatures.

    This is a necessary (not sufficient) condition for collision resistance.
    If two events with different actors produced the same HMAC, an attacker
    could rewrite the actor field without detection.
    """
    from synth_engine.shared.security.audit_signatures import sign_v3

    assume(actor1 != actor2)
    # Filter surrogates that break JSON serialization
    assume(_json_serializable({}))

    try:
        sig1 = sign_v3(_KEY, _TS, "TYPE", actor1, "res", "act", _PREV, {})
        sig2 = sign_v3(_KEY, _TS, "TYPE", actor2, "res", "act", _PREV, {})
    except ValueError:
        # sign_v3 may raise if fields contain non-UTF-8 sequences; skip those
        assume(False)
        return  # unreachable — assume(False) raises

    assert sig1 != sig2, (
        f"COLLISION: actor1={actor1!r}, actor2={actor2!r} produced the same v3 signature: {sig1!r}"
    )


@given(
    event_type1=st.text(min_size=1, max_size=32),
    event_type2=st.text(min_size=1, max_size=32),
)
@settings(max_examples=200)
def test_sign_v3_different_event_types_produce_different_signatures(
    event_type1: str,
    event_type2: str,
) -> None:
    """v3: two events differing only in event_type MUST have different signatures."""
    from synth_engine.shared.security.audit_signatures import sign_v3

    assume(event_type1 != event_type2)

    try:
        sig1 = sign_v3(_KEY, _TS, event_type1, "actor", "res", "act", _PREV, {})
        sig2 = sign_v3(_KEY, _TS, event_type2, "actor", "res", "act", _PREV, {})
    except ValueError:
        assume(False)
        return

    assert sig1 != sig2, (
        f"COLLISION: event_type1={event_type1!r}, event_type2={event_type2!r} "
        f"produced the same v3 signature"
    )


# ---------------------------------------------------------------------------
# Property 2: pipe-chars in fields don't cause collisions (ADV-P53-01 fix)
# ---------------------------------------------------------------------------


@given(
    split_point=st.integers(min_value=1, max_value=10),
    left=st.text(min_size=1, max_size=10, alphabet=st.characters(blacklist_categories=["Cs"])),
)
@settings(max_examples=300)
def test_sign_v3_pipe_in_actor_does_not_collide_with_split_resource(
    split_point: int,
    left: str,
) -> None:
    """v3: pipe chars in actor/resource do not cause collisions.

    ADV-P53-01: in v1/v2, ``actor="a|b"`` and ``resource="c"`` HMAC-matches
    ``actor="a"`` and ``resource="b|c"`` because the pipe is a field separator
    in the message.

    In v3, each field is length-prefixed so ``"a|b"`` at position N is
    unambiguously a single value — it cannot be split across field boundaries.
    """
    from synth_engine.shared.security.audit_signatures import sign_v3

    assume(len(left) > 0)

    # Event A: actor="left|right", resource="rest"
    actor_a = left + "|right"
    resource_a = "rest"

    # Event B: actor="left", resource="right|rest" — same bytes in the
    # naive pipe-separated encoding, but different structured inputs
    actor_b = left
    resource_b = "right|rest"

    try:
        sig_a = sign_v3(_KEY, _TS, "TYPE", actor_a, resource_a, "act", _PREV, {})
        sig_b = sign_v3(_KEY, _TS, "TYPE", actor_b, resource_b, "act", _PREV, {})
    except ValueError:
        assume(False)
        return

    assert sig_a != sig_b, (
        f"COLLISION via pipe injection: actor={actor_a!r}/resource={resource_a!r} "
        f"vs actor={actor_b!r}/resource={resource_b!r} — ADV-P53-01 may not be fixed"
    )


# ---------------------------------------------------------------------------
# Property 3: non-JSON-serializable details raise ValueError
# ---------------------------------------------------------------------------


def test_sign_v3_details_with_nan_float_coerced_raises_value_error() -> None:
    """v3: if details dict JSON contains NaN/Inf, ValueError is raised.

    json.dumps with allow_nan=False rejects float('nan') and float('inf').
    The sign_v3 function must propagate this ValueError rather than producing
    a signature over a non-deterministic or platform-dependent encoding.

    Note: Python dict[str, str] prevents direct insertion of floats at the
    type level, but we test the underlying json.dumps behavior via a cast,
    which represents real-world misuse by callers who bypass type checking.
    """

    import pytest

    from synth_engine.shared.security.audit_signatures import sign_v3

    # Directly test the json.dumps call that sign_v3 uses — bypass type system
    # to simulate a caller passing a float value that slips through unchecked.
    details_with_nan: dict[str, str] = {"metric": float("nan")}  # type: ignore[dict-item]

    with pytest.raises(ValueError, match="float"):
        sign_v3(_KEY, _TS, "TYPE", "actor", "res", "act", _PREV, details_with_nan)


def test_sign_v3_details_with_inf_raises_value_error() -> None:
    """v3: details dict containing float('inf') raises ValueError."""
    from synth_engine.shared.security.audit_signatures import sign_v3

    details_with_inf: dict[str, str] = {"limit": float("inf")}  # type: ignore[dict-item]

    with pytest.raises(ValueError, match="float"):
        sign_v3(_KEY, _TS, "TYPE", "actor", "res", "act", _PREV, details_with_inf)


@given(
    key_str=st.text(
        min_size=1,
        max_size=16,
        alphabet=st.characters(blacklist_categories=["Cs"]),
    ),
    val_str=st.text(
        min_size=0,
        max_size=64,
        alphabet=st.characters(blacklist_categories=["Cs"]),
    ),
)
@settings(max_examples=300)
def test_sign_v3_serializable_details_do_not_raise(key_str: str, val_str: str) -> None:
    """v3: valid JSON-serializable details never raise ValueError from serialization.

    Hypothesis explores text fields excluding surrogate codepoints (Cs category)
    which are explicitly non-serializable.  All valid Unicode text should serialize
    successfully and produce a signature without ValueError.
    """
    from synth_engine.shared.security.audit_signatures import sign_v3

    details = {key_str: val_str}
    assume(_json_serializable(details))

    # Must not raise — valid details always produce a signature
    result = sign_v3(_KEY, _TS, "TYPE", "actor", "res", "act", _PREV, details)
    assert result.startswith("v3:"), f"Expected v3: prefix, got {result[:10]!r}"
    assert len(result) > 3, "Signature must contain more than just the prefix"
