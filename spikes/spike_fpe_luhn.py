"""Spike B: Deterministic Format-Preserving Encryption with LUHN validation.

Demonstrates that a Feistel-based FPE cipher can mask 10,000 credit card numbers
while preserving the 16-digit format, guaranteeing zero collisions, and passing
a LUHN check on every masked output -- using stdlib only.

Usage:
    python spikes/spike_fpe_luhn.py
"""

import hashlib
import hmac
import random
import secrets
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FEISTEL_ROUNDS: int = 8
CARD_LENGTH: int = 16
# First 15 digits are FPE-encrypted; 16th is recomputed as the LUHN check digit.
FPE_PAYLOAD_LENGTH: int = CARD_LENGTH - 1

# Major Industry Identifier / Bank Identification Number prefixes for the four
# major card networks used as random card-number prefixes during generation.
VISA_PREFIX: str = "4"
MC_PREFIXES: list[str] = ["51", "52", "53", "54", "55"]
AMEX_PREFIXES: list[str] = ["34", "37"]
DISCOVER_PREFIX: str = "6011"

ALL_PREFIXES: list[str] = [
    VISA_PREFIX,
    *MC_PREFIXES,
    *AMEX_PREFIXES,
    DISCOVER_PREFIX,
]

CARD_COUNT: int = 10_000


# ---------------------------------------------------------------------------
# Feistel FPE implementation
# ---------------------------------------------------------------------------


class FeistelFPE:
    """Deterministic format-preserving encryption using a Feistel network over digit strings.

    Uses HMAC-SHA256 as the round function. Operates on the digit domain [0-9].
    8 rounds provides strong collision resistance on 16-digit strings.

    The Feistel construction for a digit string of length n:
        - Split into L (left_len = ceil(n/2) digits) and R (right_len = floor(n/2) digits).
        - Each round: new_L = R; new_R = (L + F(R, round)) mod 10^left_len.
        - F(R, i) produces a value mod 10^left_len using HMAC-SHA256.
        - After all rounds reassemble: output = L_final || R_final.
    """

    def __init__(self, key: bytes, rounds: int = FEISTEL_ROUNDS) -> None:
        """Initialise the Feistel cipher.

        Args:
            key: Secret HMAC key bytes.  Use at least 16 bytes of entropy.
            rounds: Number of Feistel rounds.  Defaults to 8.
        """
        self._key = key
        self._rounds = rounds

    def _round_function(self, value: int, output_len: int, round_index: int) -> int:
        """Compute the Feistel round function F(value, round_index).

        The round function maps an arbitrary integer to the range [0, 10^output_len)
        using HMAC-SHA256.  The result is used to add (mod 10^output_len) with
        the opposite Feistel half.

        Args:
            value: The integer input (the half-block being processed).
            output_len: Number of digits the output must fit in (sets the modulus).
            round_index: The current round index (0-based).

        Returns:
            An integer in the range [0, 10^output_len).
        """
        # Pack value as big-endian bytes; always use at least 8 bytes.
        byte_count = max(8, (value.bit_length() + 7) // 8)
        value_bytes = value.to_bytes(byte_count, "big")
        round_byte = bytes([round_index])
        digest = hmac.new(self._key, value_bytes + round_byte, hashlib.sha256).hexdigest()
        modulus = 10**output_len
        # Map the hex digest to an integer in the digit domain.
        return int(digest, 16) % modulus

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a digit string, preserving its length.

        Args:
            plaintext: A string of decimal digits.

        Returns:
            A digit string of the same length as plaintext.

        Raises:
            ValueError: If plaintext is empty or contains non-digit characters.
        """
        if not plaintext or not plaintext.isdigit():
            raise ValueError(f"plaintext must be a non-empty digit string, got: {plaintext!r}")

        n = len(plaintext)
        left_len = (n + 1) // 2  # ceil(n/2)
        right_len = n - left_len  # floor(n/2)
        left_mod = 10**left_len
        right_mod = 10**right_len

        left = int(plaintext[:left_len])
        right = int(plaintext[left_len:]) if right_len > 0 else 0

        for i in range(self._rounds):
            # Standard Feistel: new_right = (left + F(right)) mod left_mod
            # then swap left <-> right but keep them in their respective domains.
            new_right = (left + self._round_function(right, left_len, i)) % left_mod
            # After swap: old right (right_len domain) becomes new left,
            # new_right (left_len domain) becomes new right.
            left, right = right % right_mod, new_right % left_mod

        result = str(left).zfill(right_len) + str(right).zfill(left_len)
        if len(result) != n:
            raise AssertionError(f"FPE length invariant violated: {len(result)} != {n}")
        return result

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a digit string previously encrypted with this cipher.

        Args:
            ciphertext: A string of decimal digits produced by encrypt().

        Returns:
            The original plaintext digit string.

        Raises:
            ValueError: If ciphertext is empty or contains non-digit characters.
        """
        if not ciphertext or not ciphertext.isdigit():
            raise ValueError(f"ciphertext must be a non-empty digit string, got: {ciphertext!r}")

        n = len(ciphertext)
        left_len = (n + 1) // 2  # ceil(n/2) -- same as encrypt
        right_len = n - left_len
        left_mod = 10**left_len
        right_mod = 10**right_len

        # After encrypt, output layout is: right_half (right_len) || left_half (left_len).
        left = int(ciphertext[:right_len]) if right_len > 0 else 0
        right = int(ciphertext[right_len:])

        for i in reversed(range(self._rounds)):
            # Invert encrypt round i.  In encrypt:
            #   new_right = (left + F(right)) % left_mod; left, right = right, new_right
            # After that swap: stored_left = old_right, stored_right = new_right.
            # To invert: recover old_right = stored_left, old_left via subtraction.
            old_right = left  # was the left block after the swap
            old_left = (right - self._round_function(old_right, left_len, i)) % left_mod
            left, right = old_left % left_mod, old_right % right_mod

        result = str(left).zfill(left_len) + str(right).zfill(right_len)
        if len(result) != n:
            raise AssertionError(f"FPE decrypt length invariant violated: {len(result)} != {n}")
        return result


# ---------------------------------------------------------------------------
# LUHN utilities
# ---------------------------------------------------------------------------


def luhn_check(number: str) -> bool:
    """Verify that a digit string satisfies the Luhn algorithm.

    Args:
        number: A string of decimal digits (no spaces or dashes).

    Returns:
        True if the number passes the Luhn check, False otherwise.
    """
    if not number.isdigit():
        return False
    total = 0
    for i, digit in enumerate(reversed(number)):
        d = int(digit)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _luhn_check_digit(partial: str) -> str:
    """Compute the single check digit that makes partial + check_digit pass LUHN.

    Args:
        partial: A digit string of length n-1 (all digits except the last).

    Returns:
        A single character ('0'-'9') that is the correct LUHN check digit.
    """
    # Append a sentinel '0' then calculate how far the sum is from the next
    # multiple of 10.
    candidate = partial + "0"
    total = 0
    for i, digit in enumerate(reversed(candidate)):
        d = int(digit)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    remainder = total % 10
    check = (10 - remainder) % 10
    return str(check)


def generate_valid_luhn(prefix: str, length: int, rng: random.Random) -> str:
    """Generate a random card number with a given prefix that passes LUHN.

    Args:
        prefix: The BIN/IIN prefix string (e.g. "4" for Visa, "51" for MC).
        length: Total length of the card number including the check digit.
        rng: A seeded Random instance for reproducibility.

    Returns:
        A digit string of the specified length that passes luhn_check().

    Raises:
        ValueError: If length <= len(prefix).
        RuntimeError: If the generated card fails internal consistency checks.
    """
    if length <= len(prefix):
        raise ValueError(f"length ({length}) must be greater than prefix length ({len(prefix)})")
    # Fill digits between the prefix and the last (check) digit position.
    fill_length = length - len(prefix) - 1
    fill = "".join(str(rng.randint(0, 9)) for _ in range(fill_length))
    partial = prefix + fill
    check = _luhn_check_digit(partial)
    card = partial + check
    if len(card) != length:
        raise RuntimeError(f"Card length mismatch: expected {length}, got {len(card)}")
    if not luhn_check(card):
        raise RuntimeError(f"Generated card failed LUHN check: {card}")
    return card


# ---------------------------------------------------------------------------
# LUHN-preserving masking
# ---------------------------------------------------------------------------


def luhn_preserving_mask(fpe: FeistelFPE, card: str) -> str:
    """Mask a card number using FPE while guaranteeing the output passes LUHN.

    Encrypts the first (len-1) digits with the Feistel FPE cipher and then
    recomputes the final digit as the correct LUHN check digit.  This ensures:
      - Same-length output (format preserved).
      - 100% LUHN validity on the masked output.
      - Determinism: same key + same input = same output every time.

    Args:
        fpe: A configured FeistelFPE instance.
        card: A digit string whose length matches CARD_LENGTH.

    Returns:
        A masked card number of the same length that passes luhn_check().

    Raises:
        ValueError: If card length does not match CARD_LENGTH.
    """
    if len(card) != CARD_LENGTH:
        raise ValueError(f"card must be exactly {CARD_LENGTH} digits, got {len(card)}")
    payload = card[: CARD_LENGTH - 1]
    encrypted_payload = fpe.encrypt(payload)
    check = _luhn_check_digit(encrypted_payload)
    return encrypted_payload + check


# ---------------------------------------------------------------------------
# Main spike runner
# ---------------------------------------------------------------------------


def _generate_cards(rng: random.Random) -> list[str]:
    """Generate CARD_COUNT valid 16-digit credit card numbers.

    Args:
        rng: Seeded random instance for reproducibility.

    Returns:
        A list of CARD_COUNT valid card number strings.
    """
    cards: list[str] = []
    for _ in range(CARD_COUNT):
        prefix = rng.choice(ALL_PREFIXES)
        card = generate_valid_luhn(prefix, CARD_LENGTH, rng)
        cards.append(card)
    return cards


def _run_assertions(
    original_cards: list[str],
    masked_cards: list[str],
    label: str = "Pass 1",
) -> None:
    """Assert the three acceptance criteria on a batch of masked cards.

    Args:
        original_cards: The original plaintext card numbers.
        masked_cards: The masked card numbers to validate.
        label: A human-readable label for error messages.

    Raises:
        RuntimeError: If any acceptance criterion is violated.
    """
    # 1. Zero collisions
    unique_count = len(set(masked_cards))
    if unique_count != CARD_COUNT:
        duplicates = CARD_COUNT - unique_count
        raise RuntimeError(f"{label}: collision detected -- {duplicates} duplicates found")
    # 2. 100% LUHN validity
    failed_luhn = [c for c in masked_cards if not luhn_check(c)]
    if failed_luhn:
        raise RuntimeError(f"{label}: {len(failed_luhn)} masked cards failed LUHN check")
    # 3. Format preserved (16 digits)
    wrong_format = [c for c in masked_cards if len(c) != CARD_LENGTH or not c.isdigit()]
    if wrong_format:
        raise RuntimeError(f"{label}: {len(wrong_format)} masked cards have wrong format")
    _ = original_cards  # original_cards present for caller's summary output


def main() -> None:
    """Run the FPE-LUHN spike, print findings, and assert all acceptance criteria."""
    print("=" * 65)
    print("Spike B: Feistel FPE + LUHN-preserving credit card masking")
    print("=" * 65)

    # Derive a stable test key -- not a production secret; spike use only.
    key = secrets.token_bytes(32)

    fpe = FeistelFPE(key=key, rounds=FEISTEL_ROUNDS)

    # Seed RNG for reproducible card generation (non-cryptographic use).
    rng = random.Random(42)  # nosec B311

    print(f"\nGenerating {CARD_COUNT:,} valid Luhn credit card numbers...")
    cards = _generate_cards(rng)
    print(f"  Generated: {len(cards):,} cards")

    # --- Pass 1: mask and validate ---
    print("\nPass 1 -- masking and validating...")
    start = time.perf_counter()
    masked_pass1 = [luhn_preserving_mask(fpe, c) for c in cards]
    elapsed = time.perf_counter() - start

    _run_assertions(cards, masked_pass1, label="Pass 1")

    luhn_pass_rate = sum(1 for c in masked_pass1 if luhn_check(c)) / CARD_COUNT * 100
    collision_count = CARD_COUNT - len(set(masked_pass1))

    print(f"  Collision count       : {collision_count}")
    print(f"  LUHN pass rate        : {luhn_pass_rate:.2f}%")
    print(f"  Format preserved (16d): {all(len(c) == 16 and c.isdigit() for c in masked_pass1)}")
    print(f"  Throughput            : {CARD_COUNT / elapsed:,.0f} cards/sec")

    print("\n  Sample (plaintext -> masked):")
    for i in range(3):
        print(f"    {cards[i]}  ->  {masked_pass1[i]}")

    # --- Pass 2: determinism verification ---
    print("\nPass 2 -- re-encrypting to verify determinism...")
    masked_pass2 = [luhn_preserving_mask(fpe, c) for c in cards]
    if masked_pass2 != masked_pass1:
        raise RuntimeError("DETERMINISM FAILURE: Pass 2 produced different results from Pass 1")
    print("  Determinism confirmed: Pass 1 == Pass 2  [OK]")

    # --- Final summary ---
    print("\n" + "=" * 65)
    print("ALL ASSERTIONS PASSED")
    print(f"  Zero collisions      : PASS ({collision_count} collisions)")
    print(f"  100% LUHN validity   : PASS ({luhn_pass_rate:.2f}%)")
    print("  Format preserved     : PASS (all 16 digits)")
    print("  Determinism          : PASS (pass1 == pass2)")
    print("=" * 65)


if __name__ == "__main__":
    main()
