from __future__ import annotations

import unittest

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


class GoalStatusLiteralTest(unittest.TestCase):
    """GoalStatus Literal accepts active/complete/abandoned/superseded."""

    def test_all_status_literals_accepted(self) -> None:
        for value in ("active", "complete", "abandoned", "superseded"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status=value,  # type: ignore[arg-type]
                scope="repo",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(value, goal.status)

    def test_invalid_status_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status="unknown",  # type: ignore[arg-type]
                scope="repo",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_expected_values_match_definition(self) -> None:
        from typing import get_args

        expected = frozenset({"active", "complete", "abandoned", "superseded"})
        self.assertEqual(expected, frozenset(get_args(GoalStatus)))


class GoalScopeLiteralTest(unittest.TestCase):
    """GoalScope Literal accepts repo/opportunity/contribution."""

    def test_all_scope_literals_accepted(self) -> None:
        for value in ("repo", "opportunity", "contribution"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status="active",
                scope=value,  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(value, goal.scope)

    def test_invalid_scope_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status="active",
                scope="unknown",  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_expected_values_match_definition(self) -> None:
        from typing import get_args

        expected = frozenset({"repo", "opportunity", "contribution"})
        self.assertEqual(expected, frozenset(get_args(GoalScope)))


class RunPhaseLiteralTest(unittest.TestCase):
    """RunPhase Literal accepts scout/work/review/completed."""

    def test_all_phase_literals_accepted(self) -> None:
        for value in ("scout", "work", "review", "completed"):
            ctx = GoalContext(
                short_term=ShortTermGoal(
                    goal_id="g-1",
                    objective="Test",
                    status="active",
                    scope="repo",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
                current_phase=value,  # type: ignore[arg-type]
            )
            self.assertEqual(value, ctx.current_phase)

    def test_invalid_phase_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalContext(
                short_term=ShortTermGoal(
                    goal_id="g-1",
                    objective="Test",
                    status="active",
                    scope="repo",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
                current_phase="unknown",  # type: ignore[arg-type]
            )

    def test_expected_values_match_definition(self) -> None:
        from typing import get_args

        expected = frozenset({"scout", "work", "review", "completed"})
        self.assertEqual(expected, frozenset(get_args(RunPhase)))


class ScoutSubPhaseLiteralTest(unittest.TestCase):
    """ScoutSubPhase Literal accepts project/opportunity."""

    def test_both_sub_phase_literals_accepted(self) -> None:
        for value in ("project", "opportunity"):
            ctx = GoalContext(
                short_term=ShortTermGoal(
                    goal_id="g-1",
                    objective="Test",
                    status="active",
                    scope="repo",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
                current_sub_phase=value,  # type: ignore[arg-type]
            )
            self.assertEqual(value, ctx.current_sub_phase)

    def test_invalid_sub_phase_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalContext(
                short_term=ShortTermGoal(
                    goal_id="g-1",
                    objective="Test",
                    status="active",
                    scope="repo",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
                current_sub_phase="unknown",  # type: ignore[arg-type]
            )

    def test_expected_values_match_definition(self) -> None:
        from typing import get_args

        expected = frozenset({"project", "opportunity"})
        self.assertEqual(expected, frozenset(get_args(ScoutSubPhase)))


class SubPhaseTypeTest(unittest.TestCase):
    """SubPhase is ScoutSubPhase | None."""

    def test_none_is_valid_sub_phase(self) -> None:
        ctx = GoalContext(
            short_term=ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status="active",
                scope="contribution",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            ),
            current_sub_phase=None,
        )
        self.assertIsNone(ctx.current_sub_phase)

    def test_scout_sub_phase_value_is_valid_sub_phase(self) -> None:
        ctx = GoalContext(
            short_term=ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status="active",
                scope="repo",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            ),
            current_sub_phase="project",
        )
        self.assertEqual("project", ctx.current_sub_phase)


class ShortTermGoalTest(unittest.TestCase):
    """ShortTermGoal Pydantic model: required fields, defaults, and constraints."""

    def test_required_fields(self) -> None:
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find an issue",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual("g-1", goal.goal_id)
        self.assertEqual("Find an issue", goal.objective)
        self.assertEqual("2026-01-01T00:00:00Z", goal.created_at)
        self.assertEqual("2026-01-01T00:00:00Z", goal.updated_at)

    def test_defaults(self) -> None:
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Find an issue",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual("1", goal.schema_version)
        self.assertEqual("active", goal.status)
        self.assertEqual("repo", goal.scope)
        self.assertEqual("", goal.evidence_summary)
        self.assertEqual([], goal.evidence_refs)
        self.assertEqual("", goal.next_objective)

    def test_explicit_values(self) -> None:
        goal = ShortTermGoal(
            goal_id="g-1",
            objective="Fix bug",
            status="complete",
            scope="contribution",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            evidence_summary="Done.",
            evidence_refs=["git:abc123"],
            next_objective="Next task",
        )
        self.assertEqual("complete", goal.status)
        self.assertEqual("contribution", goal.scope)
        self.assertEqual("Done.", goal.evidence_summary)
        self.assertEqual(["git:abc123"], goal.evidence_refs)
        self.assertEqual("Next task", goal.next_objective)

    def test_all_status_literals_via_construction(self) -> None:
        for status in ("active", "complete", "abandoned", "superseded"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                status=status,  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(status, goal.status)

    def test_all_scope_literals_via_construction(self) -> None:
        for scope in ("repo", "opportunity", "contribution"):
            goal = ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                scope=scope,  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(scope, goal.scope)

    def test_missing_goal_id_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                objective="Test",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_missing_objective_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                goal_id="g-1",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_missing_created_at_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_missing_updated_at_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ShortTermGoal(
                goal_id="g-1",
                objective="Test",
                created_at="2026-01-01T00:00:00Z",
            )

    def test_evidence_refs_factory_independence(self) -> None:
        a = ShortTermGoal(
            goal_id="g-1",
            objective="Test",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        b = ShortTermGoal(
            goal_id="g-2",
            objective="Test",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        a.evidence_refs.append("git:abc")
        self.assertEqual(0, len(b.evidence_refs))


class GoalContextTest(unittest.TestCase):
    """GoalContext Pydantic model: defaults, nested ShortTermGoal, phase/sub_phase."""

    def test_defaults(self) -> None:
        ctx = GoalContext()
        self.assertEqual("1", ctx.schema_version)
        self.assertTrue(ctx.enabled)
        self.assertEqual("", ctx.long_term_objective)
        self.assertIsNone(ctx.short_term)
        self.assertEqual("scout", ctx.current_phase)
        self.assertEqual("project", ctx.current_sub_phase)
        self.assertEqual("aci_goal_update", ctx.update_tool)
        self.assertIn("single mutable runtime objective", ctx.note)
        self.assertFalse(ctx.degraded)
        self.assertEqual("", ctx.error)

    def test_explicit_values_with_short_term(self) -> None:
        st = ShortTermGoal(
            goal_id="g-1",
            objective="Fix bug",
            status="complete",
            scope="contribution",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
        )
        ctx = GoalContext(
            long_term_objective="Own this identity",
            short_term=st,
            current_phase="work",
            current_sub_phase=None,
            degraded=True,
            error="some error",
        )
        self.assertEqual("Own this identity", ctx.long_term_objective)
        self.assertIsNotNone(ctx.short_term)
        self.assertEqual("Fix bug", ctx.short_term.objective)
        self.assertEqual("work", ctx.current_phase)
        self.assertIsNone(ctx.current_sub_phase)
        self.assertTrue(ctx.degraded)
        self.assertEqual("some error", ctx.error)

    def test_all_phase_literals_accepted(self) -> None:
        for phase in ("scout", "work", "review", "completed"):
            ctx = GoalContext(current_phase=phase)  # type: ignore[arg-type]
            self.assertEqual(phase, ctx.current_phase)

    def test_invalid_phase_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalContext(current_phase="unknown")  # type: ignore[arg-type]

    def test_sub_phase_defaults_to_project(self) -> None:
        ctx = GoalContext()
        self.assertEqual("project", ctx.current_sub_phase)

    def test_sub_phase_none_accepted(self) -> None:
        ctx = GoalContext(current_sub_phase=None)
        self.assertIsNone(ctx.current_sub_phase)


class GoalEventTest(unittest.TestCase):
    """GoalEvent Pydantic model: required fields, defaults, and literal constraints."""

    def test_required_fields(self) -> None:
        event = GoalEvent(
            event_id="ev-1",
            event_type="goal_created",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual("ev-1", event.event_id)
        self.assertEqual("goal_created", event.event_type)
        self.assertEqual("run-1", event.run_id)

    def test_defaults(self) -> None:
        event = GoalEvent(
            event_id="ev-1",
            event_type="goal_created",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
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

    def test_explicit_values(self) -> None:
        event = GoalEvent(
            event_id="ev-1",
            event_type="goal_completed",
            run_id="run-1",
            season_id="season_0",
            participant_id="p-1",
            goal_id="g-1",
            status="complete",
            scope="contribution",
            phase="work",
            sub_phase=None,
            objective="Fix bug",
            evidence_summary="Done.",
            evidence_refs=["git:abc"],
            next_objective="Next",
            created_at="2026-01-01T00:00:00Z",
            redacted=False,
        )
        self.assertEqual("season_0", event.season_id)
        self.assertEqual("p-1", event.participant_id)
        self.assertEqual("g-1", event.goal_id)
        self.assertEqual("complete", event.status)
        self.assertEqual("contribution", event.scope)
        self.assertEqual("work", event.phase)
        self.assertIsNone(event.sub_phase)
        self.assertEqual("Fix bug", event.objective)
        self.assertEqual("Done.", event.evidence_summary)
        self.assertEqual(["git:abc"], event.evidence_refs)
        self.assertEqual("Next", event.next_objective)
        self.assertFalse(event.redacted)

    def test_status_accepts_all_goal_status_values(self) -> None:
        for status in ("active", "complete", "abandoned", "superseded"):
            event = GoalEvent(
                event_id="ev-1",
                event_type="goal_updated",
                run_id="run-1",
                status=status,  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(status, event.status)

    def test_status_none_accepted(self) -> None:
        event = GoalEvent(
            event_id="ev-1",
            event_type="goal_updated",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
        )
        self.assertIsNone(event.status)

    def test_scope_accepts_all_goal_scope_values(self) -> None:
        for scope in ("repo", "opportunity", "contribution"):
            event = GoalEvent(
                event_id="ev-1",
                event_type="goal_updated",
                run_id="run-1",
                scope=scope,  # type: ignore[arg-type]
                created_at="2026-01-01T00:00:00Z",
            )
            self.assertEqual(scope, event.scope)

    def test_missing_event_id_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalEvent(
                event_type="goal_created",
                run_id="run-1",
                created_at="2026-01-01T00:00:00Z",
            )

    def test_missing_run_id_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalEvent(
                event_id="ev-1",
                event_type="goal_created",
                created_at="2026-01-01T00:00:00Z",
            )

    def test_missing_created_at_raises(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            GoalEvent(
                event_id="ev-1",
                event_type="goal_created",
                run_id="run-1",
            )

    def test_evidence_refs_factory_independence(self) -> None:
        a = GoalEvent(
            event_id="ev-1",
            event_type="goal_created",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
        )
        b = GoalEvent(
            event_id="ev-2",
            event_type="goal_created",
            run_id="run-2",
            created_at="2026-01-01T00:00:00Z",
        )
        a.evidence_refs.append("git:abc")
        self.assertEqual(0, len(b.evidence_refs))


class GoalStateTest(unittest.TestCase):
    """GoalState Pydantic model: persisted goal snapshot."""

    def test_defaults(self) -> None:
        state = GoalState()
        self.assertEqual("1", state.schema_version)
        self.assertEqual("", state.season_id)
        self.assertEqual("", state.participant_id)
        self.assertIsNone(state.short_term)
        self.assertEqual("scout", state.current_phase)
        self.assertEqual("project", state.current_sub_phase)

    def test_explicit_values(self) -> None:
        st = ShortTermGoal(
            goal_id="g-1",
            objective="Fix bug",
            status="complete",
            scope="contribution",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
        )
        state = GoalState(
            season_id="season_0",
            participant_id="p-1",
            short_term=st,
            current_phase="review",
            current_sub_phase=None,
        )
        self.assertEqual("season_0", state.season_id)
        self.assertEqual("p-1", state.participant_id)
        self.assertIsNotNone(state.short_term)
        self.assertEqual("Fix bug", state.short_term.objective)
        self.assertEqual("review", state.current_phase)
        self.assertIsNone(state.current_sub_phase)

    def test_phase_sub_phase_accept_all_literals(self) -> None:
        for phase in ("scout", "work", "review", "completed"):
            state = GoalState(current_phase=phase)  # type: ignore[arg-type]
            self.assertEqual(phase, state.current_phase)

    def test_sub_phase_none_accepted(self) -> None:
        state = GoalState(current_sub_phase=None)
        self.assertIsNone(state.current_sub_phase)


class GoalUpdateResultTest(unittest.TestCase):
    """GoalUpdateResult Pydantic model: the return value of GoalService.update."""

    def test_success_result(self) -> None:
        ctx = GoalContext(
            long_term_objective="Own this identity",
            short_term=ShortTermGoal(
                goal_id="g-1",
                objective="Fix bug",
                status="active",
                scope="contribution",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            ),
            current_phase="work",
            current_sub_phase=None,
        )
        event = GoalEvent(
            event_id="ev-1",
            event_type="goal_created",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
        )
        result = GoalUpdateResult(success=True, goals=ctx, event=event)
        self.assertTrue(result.success)
        self.assertEqual(ctx, result.goals)
        self.assertEqual(event, result.event)
        self.assertEqual("", result.error_kind)
        self.assertEqual("", result.error_message)

    def test_failure_result(self) -> None:
        ctx = GoalContext()
        result = GoalUpdateResult(
            success=False,
            goals=ctx,
            error_kind="missing_goal_evidence",
            error_message="evidence required for completion",
        )
        self.assertFalse(result.success)
        self.assertIsNone(result.event)
        self.assertEqual("missing_goal_evidence", result.error_kind)
        self.assertEqual("evidence required for completion", result.error_message)

    def test_event_can_be_none(self) -> None:
        result = GoalUpdateResult(success=True, goals=GoalContext())
        self.assertIsNone(result.event)

    def test_error_fields_default_to_empty_strings(self) -> None:
        result = GoalUpdateResult(success=True, goals=GoalContext())
        self.assertEqual("", result.error_kind)
        self.assertEqual("", result.error_message)


class GoalsImportTest(unittest.TestCase):
    """All goals models and Literal aliases are importable from contribarena.models."""

    def test_all_symbols_importable_from_package(self) -> None:
        from contribarena.models import (
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

        # Check that all are the same objects as the direct imports
        self.assertIs(GoalContext, GoalContext)
        self.assertIs(GoalEvent, GoalEvent)
        self.assertIs(GoalState, GoalState)
        self.assertIs(GoalUpdateResult, GoalUpdateResult)
        self.assertIs(ShortTermGoal, ShortTermGoal)
        self.assertIs(GoalStatus, GoalStatus)
        self.assertIs(GoalScope, GoalScope)
        self.assertIs(RunPhase, RunPhase)
        self.assertIs(ScoutSubPhase, ScoutSubPhase)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
