from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from contribarena.models.goals import (
    GoalContext,
    GoalEvent,
    GoalState,
    GoalUpdateResult,
    ShortTermGoal,
)


class ShortTermGoalTest(unittest.TestCase):
    """Tests for contribarena.models.goals.ShortTermGoal."""

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def test_required_fields(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find a small issue.",
            created_at=now,
            updated_at=now,
        )

        self.assertEqual("g-1", goal.goal_id)
        self.assertEqual("Find a small issue.", goal.objective)
        self.assertEqual(now, goal.created_at)
        self.assertEqual(now, goal.updated_at)

    def test_default_status(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find a small issue.",
            created_at=now,
            updated_at=now,
        )

        self.assertEqual("active", goal.status)

    def test_default_scope(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find a small issue.",
            created_at=now,
            updated_at=now,
        )

        self.assertEqual("repo", goal.scope)

    def test_default_evidence_fields(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find a small issue.",
            created_at=now,
            updated_at=now,
        )

        self.assertEqual("", goal.evidence_summary)
        self.assertEqual([], goal.evidence_refs)
        self.assertEqual("", goal.next_objective)

    def test_all_status_literals(self) -> None:
        now = self._now()
        for status in ("active", "complete", "abandoned", "superseded"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="x",
                created_at=now,
                updated_at=now,
                status=status,  # type: ignore[arg-type]
            )
            self.assertEqual(status, goal.status)

    def test_all_scope_literals(self) -> None:
        now = self._now()
        for scope in ("repo", "opportunity", "contribution"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="x",
                created_at=now,
                updated_at=now,
                scope=scope,  # type: ignore[arg-type]
            )
            self.assertEqual(scope, goal.scope)

    def test_explicit_values(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-2",
            objective="Implement fix for race condition.",
            created_at=now,
            updated_at=now,
            status="complete",
            scope="contribution",
            evidence_summary="Patch submitted and verified.",
            evidence_refs=["workspace:repo/fix.py", "tool_call:aci_submit_patch:1"],
            next_objective="Run integration tests.",
        )

        self.assertEqual("complete", goal.status)
        self.assertEqual("contribution", goal.scope)
        self.assertEqual("Patch submitted and verified.", goal.evidence_summary)
        self.assertEqual(2, len(goal.evidence_refs))
        self.assertEqual("Run integration tests.", goal.next_objective)

    def test_missing_goal_id_raises(self) -> None:
        now = self._now()
        with self.assertRaises(ValidationError):
            ShortTermGoal(  # type: ignore[call-arg]
                objective="Find a small issue.",
                created_at=now,
                updated_at=now,
            )

    def test_missing_objective_raises(self) -> None:
        now = self._now()
        with self.assertRaises(ValidationError):
            ShortTermGoal(  # type: ignore[call-arg]
                goal_id="g-1",
                created_at=now,
                updated_at=now,
            )

    def test_missing_created_at_raises(self) -> None:
        with self.assertRaises(ValidationError):
            ShortTermGoal(  # type: ignore[call-arg]
                goal_id="g-1",
                objective="Find a small issue.",
                updated_at=self._now(),
            )

    def test_schema_version_default(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="x",
            created_at=now,
            updated_at=now,
        )
        self.assertEqual("1", goal.schema_version)


class GoalContextTest(unittest.TestCase):
    """Tests for contribarena.models.goals.GoalContext."""

    def test_defaults(self) -> None:
        ctx = GoalContext()

        self.assertEqual("1", ctx.schema_version)
        self.assertTrue(ctx.enabled)
        self.assertEqual("", ctx.long_term_objective)
        self.assertIsNone(ctx.short_term)
        self.assertEqual("scout", ctx.current_phase)
        self.assertEqual("project", ctx.current_sub_phase)
        self.assertEqual("aci_goal_update", ctx.update_tool)
        self.assertIn("Long-term goal is config-owned", ctx.note)
        self.assertFalse(ctx.degraded)
        self.assertEqual("", ctx.error)

    def test_with_short_term_goal(self) -> None:
        now = datetime.now(UTC).isoformat()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find a small issue.",
            created_at=now,
            updated_at=now,
        )
        ctx = GoalContext(short_term=goal)

        self.assertEqual("g-1", ctx.short_term.goal_id)
        self.assertEqual("scout", ctx.current_phase)

    def test_explicit_phase(self) -> None:
        for phase in ("scout", "work", "review", "completed"):
            ctx = GoalContext(current_phase=phase)  # type: ignore[arg-type]
            self.assertEqual(phase, ctx.current_phase)

    def test_degraded_flag(self) -> None:
        ctx = GoalContext(degraded=True, error="guidance install failed")
        self.assertTrue(ctx.degraded)
        self.assertEqual("guidance install failed", ctx.error)


class GoalEventTest(unittest.TestCase):
    """Tests for contribarena.models.goals.GoalEvent."""

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def test_required_fields(self) -> None:
        now = self._now()
        event = GoalEvent(
            event_id="evt-1",
            event_type="goal_created",
            run_id="run-1",
            created_at=now,
        )

        self.assertEqual("evt-1", event.event_id)
        self.assertEqual("goal_created", event.event_type)
        self.assertEqual("run-1", event.run_id)
        self.assertEqual(now, event.created_at)

    def test_default_values(self) -> None:
        now = self._now()
        event = GoalEvent(
            event_id="evt-1",
            event_type="goal_created",
            run_id="run-1",
            created_at=now,
        )

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

    def test_all_status_literals(self) -> None:
        now = self._now()
        for status in ("active", "complete", "abandoned", "superseded"):
            event = GoalEvent(
                event_id="evt-1",
                event_type="goal_updated",
                run_id="run-1",
                created_at=now,
                status=status,  # type: ignore[arg-type]
            )
            self.assertEqual(status, event.status)

    def test_all_scope_literals(self) -> None:
        now = self._now()
        for scope in ("repo", "opportunity", "contribution"):
            event = GoalEvent(
                event_id="evt-1",
                event_type="goal_updated",
                run_id="run-1",
                created_at=now,
                scope=scope,  # type: ignore[arg-type]
            )
            self.assertEqual(scope, event.scope)

    def test_all_phase_literals(self) -> None:
        now = self._now()
        for phase in ("scout", "work", "review", "completed"):
            event = GoalEvent(
                event_id="evt-1",
                event_type="goal_updated",
                run_id="run-1",
                created_at=now,
                phase=phase,  # type: ignore[arg-type]
            )
            self.assertEqual(phase, event.phase)

    def test_full_construction(self) -> None:
        now = self._now()
        event = GoalEvent(
            event_id="evt-2",
            event_type="goal_completed",
            run_id="run-1",
            season_id="season_0",
            participant_id="season_0:local-stub",
            goal_id="g-1",
            status="complete",
            scope="contribution",
            phase="completed",
            sub_phase=None,
            objective="Submit a verified PR.",
            evidence_summary="Patch submitted and tests pass.",
            evidence_refs=["workspace:repo/fix.py", "tool_call:aci_submit_patch:1"],
            next_objective="",
            created_at=now,
            redacted=False,
        )

        self.assertEqual("complete", event.status)
        self.assertEqual("contribution", event.scope)
        self.assertIsNone(event.sub_phase)
        self.assertFalse(event.redacted)
        self.assertEqual(2, len(event.evidence_refs))

    def test_missing_event_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GoalEvent(  # type: ignore[call-arg]
                event_type="goal_created",
                run_id="run-1",
                created_at=self._now(),
            )

    def test_missing_event_type_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GoalEvent(  # type: ignore[call-arg]
                event_id="evt-1",
                run_id="run-1",
                created_at=self._now(),
            )


class GoalStateTest(unittest.TestCase):
    """Tests for contribarena.models.goals.GoalState."""

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def test_defaults(self) -> None:
        state = GoalState()

        self.assertEqual("1", state.schema_version)
        self.assertEqual("", state.season_id)
        self.assertEqual("", state.participant_id)
        self.assertIsNone(state.short_term)
        self.assertEqual("scout", state.current_phase)
        self.assertEqual("project", state.current_sub_phase)

    def test_with_short_term_goal(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="x",
            created_at=now,
            updated_at=now,
            status="complete",
            scope="contribution",
        )
        state = GoalState(
            season_id="season_0",
            participant_id="season_0:local-stub",
            short_term=goal,
            current_phase="work",
        )

        self.assertEqual("season_0", state.season_id)
        self.assertEqual("season_0:local-stub", state.participant_id)
        self.assertEqual("complete", state.short_term.status)
        self.assertEqual("work", state.current_phase)

    def test_explicit_phase(self) -> None:
        for phase in ("scout", "work", "review", "completed"):
            state = GoalState(current_phase=phase)  # type: ignore[arg-type]
            self.assertEqual(phase, state.current_phase)


class GoalUpdateResultTest(unittest.TestCase):
    """Tests for contribarena.models.goals.GoalUpdateResult."""

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def test_required_fields(self) -> None:
        ctx = GoalContext()
        result = GoalUpdateResult(success=True, goals=ctx)

        self.assertTrue(result.success)
        self.assertEqual(ctx, result.goals)

    def test_default_values(self) -> None:
        ctx = GoalContext()
        result = GoalUpdateResult(success=True, goals=ctx)

        self.assertIsNone(result.event)
        self.assertEqual("", result.error_kind)
        self.assertEqual("", result.error_message)

    def test_success_with_event(self) -> None:
        now = self._now()
        ctx = GoalContext()
        event = GoalEvent(
            event_id="evt-1",
            event_type="goal_created",
            run_id="run-1",
            created_at=now,
        )
        result = GoalUpdateResult(success=True, goals=ctx, event=event)

        self.assertTrue(result.success)
        self.assertEqual("goal_created", result.event.event_type)

    def test_failure_with_error(self) -> None:
        ctx = GoalContext()
        result = GoalUpdateResult(
            success=False,
            goals=ctx,
            error_kind="missing_goal_evidence",
            error_message="complete status requires evidence",
        )

        self.assertFalse(result.success)
        self.assertEqual("missing_goal_evidence", result.error_kind)
        self.assertEqual("complete status requires evidence", result.error_message)

    def test_full_construction(self) -> None:
        now = self._now()
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="x",
            created_at=now,
            updated_at=now,
        )
        ctx = GoalContext(short_term=goal)
        event = GoalEvent(
            event_id="evt-1",
            event_type="goal_completed",
            run_id="run-1",
            created_at=now,
            status="complete",
        )
        result = GoalUpdateResult(
            success=True,
            goals=ctx,
            event=event,
            error_kind="",
            error_message="",
        )

        self.assertTrue(result.success)
        self.assertEqual("g-1", result.goals.short_term.goal_id)
        self.assertEqual("complete", result.event.status)
