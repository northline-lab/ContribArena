from __future__ import annotations

import unittest
from unittest.mock import patch

from contribarena.config.schema import BudgetConfig
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.errors import BudgetExhausted


class BudgetTrackerStepsTest(unittest.TestCase):
    def test_record_step_increments_steps_and_total_steps(self) -> None:
        tracker = BudgetTracker(BudgetConfig(max_steps=5))

        tracker.record_step()
        tracker.record_step()

        self.assertEqual(2, tracker.steps)
        self.assertEqual(2, tracker.total_steps)

    def test_record_step_raises_when_max_steps_exceeded(self) -> None:
        tracker = BudgetTracker(BudgetConfig(max_steps=2))

        tracker.record_step()
        tracker.record_step()
        with self.assertRaises(BudgetExhausted) as ctx:
            tracker.record_step()

        self.assertIn("max_steps", str(ctx.exception))
        # total_steps still records the over-budget call before the raise.
        self.assertEqual(3, tracker.total_steps)


class BudgetTrackerWallTimeTest(unittest.TestCase):
    def test_record_step_does_not_check_wall_time_when_unset(self) -> None:
        tracker = BudgetTracker(
            BudgetConfig(max_steps=10, max_wall_time_seconds=None)
        )

        with patch(
            "contribarena.engine.middleware.budget.monotonic",
            return_value=tracker.started_at + 10_000.0,
        ):
            # Should not raise even though a huge elapsed time has passed,
            # because max_wall_time_seconds is None.
            tracker.record_step()

        self.assertEqual(1, tracker.steps)

    def test_record_step_raises_when_wall_time_exceeded(self) -> None:
        tracker = BudgetTracker(
            BudgetConfig(max_steps=10, max_wall_time_seconds=30)
        )

        with patch(
            "contribarena.engine.middleware.budget.monotonic",
            return_value=tracker.started_at + 31.0,
        ):
            with self.assertRaises(BudgetExhausted) as ctx:
                tracker.record_step()

        self.assertIn("max_wall_time_seconds", str(ctx.exception))

    def test_record_step_passes_when_wall_time_within_limit(self) -> None:
        tracker = BudgetTracker(
            BudgetConfig(max_steps=10, max_wall_time_seconds=30)
        )

        with patch(
            "contribarena.engine.middleware.budget.monotonic",
            return_value=tracker.started_at + 5.0,
        ):
            tracker.record_step()

        self.assertEqual(1, tracker.steps)


class BudgetTrackerResetTest(unittest.TestCase):
    def test_reset_for_invocation_resets_steps_only_and_preserves_total(self) -> None:
        tracker = BudgetTracker(BudgetConfig(max_steps=2))

        tracker.record_step()
        tracker.record_step()
        self.assertEqual(2, tracker.steps)
        self.assertEqual(2, tracker.total_steps)

        tracker.reset_for_invocation()

        self.assertEqual(0, tracker.steps)
        self.assertEqual(2, tracker.total_steps)

        tracker.record_step()
        self.assertEqual(1, tracker.steps)
        self.assertEqual(3, tracker.total_steps)

    def test_reset_for_invocation_resets_wall_time_window(self) -> None:
        tracker = BudgetTracker(
            BudgetConfig(max_steps=10, max_wall_time_seconds=30)
        )

        # Advance the clock past the budget, then reset to start a fresh window.
        with patch(
            "contribarena.engine.middleware.budget.monotonic",
            return_value=tracker.started_at + 100.0,
        ):
            tracker.reset_for_invocation()
            # After reset, started_at equals the patched "now"; a new record
            # at the same "now" should report zero elapsed and not raise.
            tracker.record_step()

        self.assertEqual(1, tracker.steps)

    def test_reset_steps_for_invocation_is_alias_of_reset_for_invocation(self) -> None:
        tracker = BudgetTracker(BudgetConfig(max_steps=5))

        tracker.record_step()
        tracker.record_step()
        previous_started_at = tracker.started_at

        with patch(
            "contribarena.engine.middleware.budget.monotonic",
            return_value=previous_started_at + 50.0,
        ):
            tracker.reset_steps_for_invocation()

        self.assertEqual(0, tracker.steps)
        self.assertEqual(2, tracker.total_steps)
        # started_at was refreshed by the alias just like reset_for_invocation.
        self.assertGreater(tracker.started_at, previous_started_at)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    unittest.main()
