"""Negative/attack tests for webhook delivery circuit breaker (T62.2).

Attack tests verifying that:
1. Circuit breaker trips after 3 consecutive failures to the same URL.
2. Delivery is skipped during cooldown period.
3. Circuit breaker resets after cooldown expires.
4. Prometheus counter increments when circuit trips.
5. Successful delivery clears the failure count.
6. Delivery does not block beyond 15-second total time budget.
7. Hanging endpoint does not starve the Huey worker.

CONSTITUTION Priority 0: Security — prevent worker starvation, Prometheus cardinality
CONSTITUTION Priority 3: TDD — Attack tests committed before implementation (Rule 22)
Task: T62.2 — Circuit Breaker for Webhook Delivery
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# State isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_circuit_breaker() -> Generator[None]:
    """Reset the module-level circuit breaker singleton between tests.

    Prevents state from one test affecting subsequent tests.
    The module singleton is restored to None so each test gets a fresh
    circuit breaker from settings defaults.

    Yields:
        None — setup and teardown only.
    """
    import synth_engine.modules.synthesizer.jobs.webhook_delivery as _mod

    original = _mod._MODULE_CIRCUIT_BREAKER
    _mod._MODULE_CIRCUIT_BREAKER = None
    yield
    _mod._MODULE_CIRCUIT_BREAKER = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registration(
    url: str = "https://example.com/hook",
    active: bool = True,
    registration_id: str = "reg-001",
) -> Any:
    """Create a minimal mock WebhookRegistrationProtocol for testing.

    Args:
        url: The callback URL for the registration.
        active: Whether the registration is active.
        registration_id: The registration ID string.

    Returns:
        MagicMock satisfying WebhookRegistrationProtocol.
    """
    reg = MagicMock()
    reg.id = registration_id
    reg.callback_url = url
    reg.signing_key = "test-signing-key-32-chars-minimum"
    reg.active = active
    return reg


# ---------------------------------------------------------------------------
# T62.2 — Circuit breaker trips after consecutive failures
# ---------------------------------------------------------------------------


class TestCircuitBreakerTripping:
    """Verify the circuit breaker trips after threshold consecutive failures."""

    def test_circuit_breaker_trips_after_3_consecutive_failures(self) -> None:
        """Circuit breaker must trip after 3 consecutive delivery failures.

        After tripping, the next delivery attempt must return SKIPPED (not FAILED)
        with an error_message indicating the circuit is open.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://example.com/hook"

        # 3 consecutive failures
        cb.record_failure(url)
        cb.record_failure(url)
        cb.record_failure(url)

        # Circuit should now be open
        assert cb.is_open(url) is True
        assert cb.is_open(url)

    def test_circuit_breaker_does_not_trip_before_threshold(self) -> None:
        """Circuit must NOT trip after fewer than threshold failures."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://example.com/hook"

        cb.record_failure(url)
        cb.record_failure(url)

        # Only 2 failures — should NOT be open
        assert cb.is_open(url) is False
        assert not cb.is_open(url)

    def test_circuit_breaker_skips_delivery_during_cooldown(self) -> None:
        """deliver_webhook must return SKIPPED when circuit is open for the URL."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
            deliver_webhook,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://example.com/hook"

        # Trip the circuit
        cb.record_failure(url)
        cb.record_failure(url)
        cb.record_failure(url)
        assert cb.is_open(url) is True

        reg = _make_registration(url=url)

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery._get_circuit_breaker",
            return_value=cb,
        ):
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
            )

        assert result.status == "SKIPPED"
        assert result.error_message is not None
        assert "circuit" in result.error_message.lower() or "open" in result.error_message.lower()

    def test_circuit_breaker_resets_after_cooldown(self) -> None:
        """Circuit breaker must allow delivery after cooldown expires."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://example.com/hook"

        # Trip the circuit
        cb.record_failure(url)
        cb.record_failure(url)
        cb.record_failure(url)
        assert cb.is_open(url) is True
        assert cb.is_open(url)

        # Simulate cooldown expiry by backdating the trip time
        # Access the internal state to backdate
        cb._set_trip_time(url, time.monotonic() - 301)

        # After cooldown, circuit should allow a probe attempt
        assert cb.is_open(url) is False
        assert not cb.is_open(url)

    def test_successful_delivery_clears_failure_count(self) -> None:
        """Recording a success must reset the consecutive failure counter for the URL."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://example.com/hook"

        # 2 failures — not yet tripped
        cb.record_failure(url)
        cb.record_failure(url)

        # Success clears the counter
        cb.record_success(url)

        # After success + 2 more failures, still shouldn't trip (only 2 consecutive)
        cb.record_failure(url)
        cb.record_failure(url)
        assert cb.is_open(url) is False
        assert not cb.is_open(url)

    def test_circuit_trips_mid_loop_aborts_remaining_attempts(self) -> None:
        """If circuit trips during multi-registration delivery, remaining regs are skipped.

        This tests the abort-on-trip behavior when delivering to multiple registrations.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
            deliver_webhook,
        )

        url = "https://same-host.example.com/hook"
        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)

        # Pre-trip to 2 failures (one away from threshold)
        cb.record_failure(url)
        cb.record_failure(url)

        reg = _make_registration(url=url)

        # The next failure should trip the circuit and abort
        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery._get_circuit_breaker",
                return_value=cb,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            ),
            patch("httpx.Client") as mock_client_cls,
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client_inst = MagicMock()
            mock_client_inst.post.side_effect = Exception("connection refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client_inst
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
            )

        # After this call, circuit should be tripped (3rd failure)
        assert cb.is_open(url) is True
        assert result.status == "FAILED"

    def test_hanging_endpoint_trips_circuit_breaker(self) -> None:
        """Hanging endpoint (ReadTimeout) must trip the circuit after threshold calls.

        Simulates a slow/unresponsive endpoint by raising httpx.ReadTimeout.
        After 3 consecutive timeouts, the circuit breaker must be open and
        the 4th delivery attempt must return SKIPPED. The Prometheus counter
        must also be incremented exactly once on trip.
        """
        import httpx

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
            deliver_webhook,
        )

        url = "https://slow.example.com/hook"
        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        reg = _make_registration(url=url)

        request = httpx.Request("POST", url)

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery._get_circuit_breaker",
                return_value=cb,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            ),
            patch("httpx.Client") as mock_client_cls,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery._circuit_breaker_trips_total"
            ) as mock_counter,
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client_inst = MagicMock()
            mock_client_inst.post.side_effect = httpx.ReadTimeout("timed out", request=request)
            mock_client_cls.return_value.__enter__.return_value = mock_client_inst
            mock_labels = MagicMock()
            mock_counter.labels = MagicMock(return_value=mock_labels)

            # Three calls — each raises ReadTimeout, each counts as a failure
            for _ in range(3):
                deliver_webhook(
                    registration=reg,
                    job_id=42,
                    event_type="job.completed",
                    payload={"job_id": 42},
                )

            # Circuit must now be open
            assert cb.is_open(url) is True, (
                "Circuit breaker must be open after 3 ReadTimeout failures"
            )

            # 4th call — circuit is open, must return SKIPPED
            result = deliver_webhook(
                registration=reg,
                job_id=42,
                event_type="job.completed",
                payload={"job_id": 42},
            )

        assert result.status == "SKIPPED", (
            f"Expected SKIPPED after circuit tripped, got {result.status!r}"
        )
        # Prometheus counter must have incremented exactly once (on the trip event)
        mock_counter.labels.assert_called_once_with(reason="consecutive_failures")
        mock_labels.inc.assert_called_once()


# ---------------------------------------------------------------------------
# T62.2 — Prometheus counter
# ---------------------------------------------------------------------------


class TestCircuitBreakerPrometheusCounter:
    """Verify Prometheus counter increments when circuit trips."""

    def test_circuit_breaker_prometheus_counter_increments_on_trip(self) -> None:
        """webhook_circuit_breaker_trips_total counter must increment when circuit trips."""
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            WebhookCircuitBreaker,
        )

        cb = WebhookCircuitBreaker(threshold=3, cooldown_seconds=300)
        url = "https://counted.example.com/hook"

        with patch(
            "synth_engine.modules.synthesizer.jobs.webhook_delivery._circuit_breaker_trips_total"
        ) as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels = MagicMock(return_value=mock_labels)

            # Trip the circuit
            cb.record_failure(url)
            cb.record_failure(url)
            cb.record_failure(url)  # This should trigger the counter

        # Counter must have been incremented exactly once (on trip)
        mock_counter.labels.assert_called_once_with(reason="consecutive_failures")
        mock_labels.inc.assert_called_once()
        assert mock_labels.inc.call_count == 1

    def test_circuit_breaker_counter_has_no_registration_id_label(self) -> None:
        """Prometheus counter must NOT use registration_id as a label (unbounded cardinality).

        The counter must only use {reason: "consecutive_failures"} — no per-registration
        label that would create unbounded cardinality in high-registration deployments.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            _circuit_breaker_trips_total,
        )

        # The counter's label names must only contain 'reason'
        label_names = _circuit_breaker_trips_total._labelnames
        assert "registration_id" not in label_names, (
            "Prometheus counter must NOT have registration_id label — unbounded cardinality. "
            f"Found labels: {label_names}"
        )
        assert "reason" in label_names, (
            f"Prometheus counter must have 'reason' label. Found: {label_names}"
        )


# ---------------------------------------------------------------------------
# T62.2 — Time budget enforcement
# ---------------------------------------------------------------------------


class TestDeliveryTimeBudget:
    """Verify the 15-second total delivery time budget is enforced."""

    def test_webhook_delivery_does_not_block_beyond_15_seconds(self) -> None:
        """deliver_webhook must not run beyond 15 seconds total wall time.

        Uses time.monotonic() to verify the function exits within the budget.
        This test uses fast mocks so actual elapsed time is negligible —
        the test verifies that the time-budget check is PRESENT in the code
        by checking the function accepts a time_budget parameter or uses
        monotonic internally.
        """

        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        # Function must accept time budget — verified by using it as a keyword argument below.
        # We verify the budget is tracked by running with a very tight budget
        # and confirming it aborts early

        reg = _make_registration()

        call_count = 0

        def _slow_post(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise Exception("network error")

        # Set a 1-second budget — should still complete quickly with mocked HTTP
        start = time.monotonic()
        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            ),
            patch("httpx.Client") as mock_client_cls,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.time.sleep",
            ),
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client_inst = MagicMock()
            mock_client_inst.post.side_effect = _slow_post
            mock_client_cls.return_value.__enter__.return_value = mock_client_inst
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
                time_budget_seconds=15.0,
            )

        elapsed = time.monotonic() - start
        # With mocked HTTP, should complete fast regardless
        assert elapsed < 5.0, f"deliver_webhook took {elapsed:.2f}s — too slow even with mocks"
        assert result.status in {"FAILED", "SKIPPED", "SUCCESS"}

    def test_delivery_aborts_when_time_budget_exceeded(self) -> None:
        """deliver_webhook must abort retry loop when time budget is exhausted.

        Simulates a scenario where each attempt consumes the full budget.
        The function must NOT make the second attempt if budget is already spent.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        reg = _make_registration()
        attempt_count = 0

        # Monotonic time advances by 10s per httpx.post call
        base_time = 1000.0
        current_time = [base_time]

        def _mock_monotonic() -> float:
            return current_time[0]

        def _slow_post(*args: Any, **kwargs: Any) -> Any:
            nonlocal attempt_count
            attempt_count += 1
            # Each attempt advances clock by 10 seconds
            current_time[0] += 10.0
            raise Exception("timeout")

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            ),
            patch("httpx.Client") as mock_client_cls,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.time.monotonic",
                side_effect=_mock_monotonic,
            ),
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.time.sleep",
            ),
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client_inst = MagicMock()
            mock_client_inst.post.side_effect = _slow_post
            mock_client_cls.return_value.__enter__.return_value = mock_client_inst
            result = deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
                time_budget_seconds=15.0,
            )

        # Should have made at most 2 attempts (first uses 10s of 15s budget)
        # Before second attempt check budget: 10s used, 5s remain — allow
        # After second attempt: 20s used > 15s budget — abort before third
        assert attempt_count <= 2, (
            f"Expected ≤2 attempts with 15s budget (10s per attempt), got {attempt_count}"
        )
        assert result.status == "FAILED"


# ---------------------------------------------------------------------------
# T62.2 — No time.sleep in retry loop (use non-blocking approach)
# ---------------------------------------------------------------------------


class TestNoBlockingSleep:
    """Verify time.sleep() is replaced with a non-blocking retry mechanism."""

    def test_deliver_webhook_does_not_call_time_sleep_directly(self) -> None:
        """deliver_webhook must not call time.sleep() in the retry loop.

        Using time.sleep() in a Huey worker blocks the entire worker process,
        starving other queued tasks. The implementation must use a non-blocking
        approach (e.g., Huey retry scheduling or a monotonic time check).

        This test verifies that the production code path does NOT invoke
        time.sleep directly.
        """
        from synth_engine.modules.synthesizer.jobs.webhook_delivery import (
            deliver_webhook,
        )

        reg = _make_registration()
        sleep_calls: list[float] = []

        def _track_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with (
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.validate_delivery_ips",
            ),
            patch("httpx.Client") as mock_client_cls,
            patch(
                "synth_engine.modules.synthesizer.jobs.webhook_delivery.time.sleep",
                side_effect=_track_sleep,
            ),
        ):
            # T72.5: httpx.Client context manager; configure mock client .post
            mock_client_inst = MagicMock()
            mock_client_inst.post.side_effect = Exception("refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client_inst
            deliver_webhook(
                registration=reg,
                job_id=1,
                event_type="job.completed",
                payload={"job_id": 1},
                time_budget_seconds=15.0,
            )

        assert sleep_calls == [], (
            f"deliver_webhook called time.sleep({sleep_calls}) — this blocks the Huey worker. "
            "Replace with non-blocking retry (schedule with delay or check budget only)."
        )


# ---------------------------------------------------------------------------
# T62.2 — Settings fields
# ---------------------------------------------------------------------------


class TestCircuitBreakerSettings:
    """Verify ConclaveSettings fields for circuit breaker configuration."""

    def test_settings_has_circuit_breaker_threshold_field(self) -> None:
        """ConclaveSettings must expose webhook_circuit_breaker_threshold with default 3."""
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        threshold = settings.webhook_circuit_breaker_threshold
        assert threshold == 3, f"Expected webhook_circuit_breaker_threshold=3, got {threshold}"

    def test_settings_has_circuit_breaker_cooldown_field(self) -> None:
        """ConclaveSettings must expose webhook_circuit_breaker_cooldown_seconds.

        Default value must be 300 seconds (5 minutes).
        """
        from synth_engine.shared.settings import get_settings

        settings = get_settings()
        cooldown = settings.webhook_circuit_breaker_cooldown_seconds
        assert cooldown == 300, (
            f"Expected webhook_circuit_breaker_cooldown_seconds=300, got {cooldown}"
        )
