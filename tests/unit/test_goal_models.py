from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.models.goals import (
    GoalContext,
    GoalEvent,
    GoalScope,
    GoalState,
    GoalStatus,
    GoalUpdateResult,
    RunPhase,
    ScoutSubPhase,
    ShortTermGoal,
)


class LiteralAliasTest(unittest.TestCase):
    """The Literal type aliases pin the public vocabulary used by the goal API."""

    def test_goal_status_values(self) -> None:
        self.assertEqual(
            ("active", "complete", "abandoned", "superseded"),
            get_args(GoalStatus),
        )

    def test_goal_scope_values(self) -> None:
        self.assertEqual(
            ("repo", "opportunity", "contribution"),
            get_args(GoalScope),
        )

    def test_run_phase_values(self) -> None:
        self.assertEqual(
            ("scout", "work", "review", "completed"),
            get_args(RunPhase),
        )

    def test_scout_sub_phase_values(self) -> None:
        self.assertEqual(("project", "opportunity"), get_args(ScoutSubPhase))


class ShortTermGoalTest(unittest.TestCase):
    """ShortTermGoal records the single mutable runtime objective."""

    def _minimal_kwargs(self) -> dict:
        return {
            "goal_id": "g-1",
            "objective": "Land a small docs fix.",
            "created_at": "2026-05-29T00:00:00+00:00",
            "updated_at": "2026-05-29T00:00:00+00:00",
        }

    def test_minimal_construction_applies_defaults(self) -> None:
        goal = ShortTermGoal(**self._minimal_kwargs())

        self.assertEqual("1", goal.schema_version)
        self.assertEqual("active", goal.status)
        self.assertEqual("repo", goal.scope)
        self.assertEqual("", goal.evidence_summary)
        self.assertEqual([], goal.evidence_refs)
        self.assertEqual("", goal.next_objective)

    def test_missing_required_field_raises(self) -> None:
        for missing in ("goal_id", "objective", "created_at", "updated_at"):
            kwargs = self._minimal_kwargs()
            kwargs.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(ValidationError):
                    ShortTermGoal(**kwargs)

    def test_all_status_literals_accepted(self) -> None:
        for status in get_args(GoalStatus):
            with self.subTest(status=status):
                goal = ShortTermGoal(status=status, **self._minimal_kwargs())
                self.assertEqual(status, goal.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ShortTermGoal(status="in_progress", **self._minimal_kwargs())

    def test_all_scope_literals_accepted(self) -> None:
        for scope in get_args(GoalScope):
            with self.subTest(scope=scope):
                goal = ShortTermGoal(scope=scope, **self._minimal_kwargs())
                self.assertEqual(scope, goal.scope)

    def test_invalid_scope_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ShortTermGoal(scope="global", **self._minimal_kwargs())

    def test_evidence_refs_passthrough(self) -> None:
        refs = ["tool_call:aci_submit_patch:1", "workspace:repo/foo.py"]
        goal = ShortTermGoal(evidence_refs=refs, **self._minimal_kwargs())
        self.assertEqual(refs, goal.evidence_refs)

    def test_schema_version_locked_to_one(self) -> None:
        with self.assertRaises(ValidationError):
            ShortTermGoal(schema_version="2", **self._minimal_kwargs())


class GoalContextTest(unittest.TestCase):
    """GoalContext is the runtime view returned to the agent."""

    def test_defaults(self) -> None:
        context = GoalContext()

        self.assertEqual("1", context.schema_version)
        self.assertTrue(context.enabled)
        self.assertEqual("", context.long_term_objective)
        self.assertIsNone(context.short_term)
        self.assertEqual("scout", context.current_phase)
        self.assertEqual("project", context.current_sub_phase)
        self.assertEqual("aci_goal_update", context.update_tool)
        self.assertFalse(context.degraded)
        self.assertEqual("", context.error)
        self.assertIn("Long-term goal is config-owned", context.note)

    def test_all_phase_literals_accepted(self) -> None:
        for phase in get_args(RunPhase):
            with self.subTest(phase=phase):
                context = GoalContext(current_phase=phase)
                self.assertEqual(phase, context.current_phase)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GoalContext(current_phase="shipping")

    def test_sub_phase_accepts_none(self) -> None:
        context = GoalContext(current_sub_phase=None)
        self.assertIsNone(context.current_sub_phase)

    def test_sub_phase_literals_accepted(self) -> None:
        for sub in get_args(ScoutSubPhase):
            with self.subTest(sub_phase=sub):
                context = GoalContext(current_sub_phase=sub)
                self.assertEqual(sub, context.current_sub_phase)

    def test_invalid_sub_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GoalContext(current_sub_phase="triage")

    def test_nested_short_term_composition(self) -> None:
        short_term = ShortTermGoal(
            goal_id="g-2",
            objective="Pin model defaults.",
            created_at="2026-05-29T00:00:00+00:00",
            updated_at="2026-05-29T00:00:00+00:00",
        )
        context = GoalContext(short_term=short_term)
        self.assertEqual("g-2", context.short_term.goal_id)


class GoalEventTest(unittest.TestCase):
    """GoalEvent records lifecycle transitions to the event log."""

    def _minimal_kwargs(self) -> dict:
        return {
            "event_id": "evt-1",
            "event_type": "goal_created",
            "run_id": "run-1",
            "created_at": "2026-05-29T00:00:00+00:00",
        }

    def test_minimal_construction_applies_defaults(self) -> None:
        event = GoalEvent(**self._minimal_kwargs())

        self.assertEqual("1", event.schema_version)
        self.assertEqual("", event.season_id)
        self.assertEqual("", event.participant_id)
        self.assertEqual("", event.goal_id)
        self.assertIsNone(event.status)
        self.assertIsNone(event.scope)
        self.assertEqual("scout", event.phase)
        self.assertEqual("project", event.sub_phase)
        self.assertEqual("", event.objective)
        self.assertEqual("", event.evidence_summary)
        self.assertEqual([], event.evidence_refs)
        self.assertEqual("", event.next_objective)
        self.assertTrue(event.redacted)

    def test_missing_required_field_raises(self) -> None:
        for missing in ("event_id", "event_type", "run_id", "created_at"):
            kwargs = self._minimal_kwargs()
            kwargs.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(ValidationError):
                    GoalEvent(**kwargs)

    def test_status_accepts_none_and_all_literals(self) -> None:
        event = GoalEvent(status=None, **self._minimal_kwargs())
        self.assertIsNone(event.status)
        for status in get_args(GoalStatus):
            with self.subTest(status=status):
                event = GoalEvent(status=status, **self._minimal_kwargs())
                self.assertEqual(status, event.status)

    def test_scope_accepts_none_and_all_literals(self) -> None:
        event = GoalEvent(scope=None, **self._minimal_kwargs())
        self.assertIsNone(event.scope)
        for scope in get_args(GoalScope):
            with self.subTest(scope=scope):
                event = GoalEvent(scope=scope, **self._minimal_kwargs())
                self.assertEqual(scope, event.scope)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GoalEvent(phase="unknown", **self._minimal_kwargs())


class GoalStateTest(unittest.TestCase):
    """GoalState is the persisted shape of the runtime goal state file."""

    def test_defaults(self) -> None:
        state = GoalState()

        self.assertEqual("1", state.schema_version)
        self.assertEqual("", state.season_id)
        self.assertEqual("", state.participant_id)
        self.assertIsNone(state.short_term)
        self.assertEqual("scout", state.current_phase)
        self.assertEqual("project", state.current_sub_phase)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GoalState(current_phase="merging")

    def test_invalid_sub_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GoalState(current_sub_phase="draft")


class GoalUpdateResultTest(unittest.TestCase):
    """GoalUpdateResult is the structured response returned by aci_goal_update."""

    def test_minimal_construction_requires_success_and_goals(self) -> None:
        result = GoalUpdateResult(success=True, goals=GoalContext())

        self.assertTrue(result.success)
        self.assertIsInstance(result.goals, GoalContext)
        self.assertIsNone(result.event)
        self.assertEqual("", result.error_kind)
        self.assertEqual("", result.error_message)

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GoalUpdateResult(goals=GoalContext())
        with self.assertRaises(ValidationError):
            GoalUpdateResult(success=True)

    def test_event_passthrough(self) -> None:
        event = GoalEvent(
            event_id="evt-1",
            event_type="goal_created",
            run_id="run-1",
            created_at="2026-05-29T00:00:00+00:00",
        )
        result = GoalUpdateResult(success=True, goals=GoalContext(), event=event)

        self.assertIs(event, result.event)

    def test_error_fields_passthrough(self) -> None:
        result = GoalUpdateResult(
            success=False,
            goals=GoalContext(),
            error_kind="missing_goal_evidence",
            error_message="evidence required",
        )

        self.assertFalse(result.success)
        self.assertEqual("missing_goal_evidence", result.error_kind)
        self.assertEqual("evidence required", result.error_message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
