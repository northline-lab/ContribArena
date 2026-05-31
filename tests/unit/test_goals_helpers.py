from __future__ import annotations

import unittest

from contribarena.engine.goals import (
    _event_type_for_status,
    _has_active_draft,
    _has_current_run_goal_event,
    _normalize_scope,
    phase_for_goal,
)
from contribarena.models.goals import GoalEvent, ShortTermGoal


# ---------------------------------------------------------------------------
# phase_for_goal
# ---------------------------------------------------------------------------


class PhaseForGoalTest(unittest.TestCase):
    def test_none_goal_scout_fallback(self) -> None:
        phase, sub_phase = phase_for_goal(None, fallback_phase="scout")
        self.assertEqual("scout", phase)
        self.assertEqual("project", sub_phase)

    def test_none_goal_work_fallback(self) -> None:
        phase, sub_phase = phase_for_goal(None, fallback_phase="work")
        self.assertEqual("work", phase)
        self.assertIsNone(sub_phase)

    def test_none_goal_review_fallback(self) -> None:
        phase, sub_phase = phase_for_goal(None, fallback_phase="review")
        self.assertEqual("review", phase)
        self.assertIsNone(sub_phase)

    def test_none_goal_completed_fallback(self) -> None:
        phase, sub_phase = phase_for_goal(None, fallback_phase="completed")
        self.assertEqual("completed", phase)
        self.assertIsNone(sub_phase)

    def test_none_goal_default_fallback(self) -> None:
        phase, sub_phase = phase_for_goal(None)
        self.assertEqual("scout", phase)
        self.assertEqual("project", sub_phase)

    def test_complete_status_overrides_everything(self) -> None:
        goal = _short_term(scope="repo", status="complete")
        phase, sub_phase = phase_for_goal(goal)
        self.assertEqual("completed", phase)
        self.assertIsNone(sub_phase)

    def test_contribution_scope_with_draft_submitted(self) -> None:
        goal = _short_term(scope="contribution", status="active")
        phase, sub_phase = phase_for_goal(goal, draft_submitted=True)
        self.assertEqual("review", phase)
        self.assertIsNone(sub_phase)

    def test_contribution_scope_without_draft_submitted(self) -> None:
        goal = _short_term(scope="contribution", status="active")
        phase, sub_phase = phase_for_goal(goal, draft_submitted=False)
        self.assertEqual("work", phase)
        self.assertIsNone(sub_phase)

    def test_contribution_scope_draft_submitted_defaults_false(self) -> None:
        goal = _short_term(scope="contribution", status="active")
        phase, sub_phase = phase_for_goal(goal)
        self.assertEqual("work", phase)
        self.assertIsNone(sub_phase)

    def test_repo_scope(self) -> None:
        goal = _short_term(scope="repo", status="active")
        phase, sub_phase = phase_for_goal(goal)
        self.assertEqual("scout", phase)
        self.assertEqual("project", sub_phase)

    def test_opportunity_scope(self) -> None:
        goal = _short_term(scope="opportunity", status="active")
        phase, sub_phase = phase_for_goal(goal)
        self.assertEqual("scout", phase)
        self.assertEqual("opportunity", sub_phase)

    def test_complete_status_overrides_draft_submitted(self) -> None:
        goal = _short_term(scope="contribution", status="complete")
        phase, sub_phase = phase_for_goal(goal, draft_submitted=True)
        self.assertEqual("completed", phase)
        self.assertIsNone(sub_phase)


# ---------------------------------------------------------------------------
# _normalize_scope
# ---------------------------------------------------------------------------


class NormalizeScopeTest(unittest.TestCase):
    def test_none_scope_with_current_returns_current_scope(self) -> None:
        current = _short_term(scope="repo")
        self.assertEqual("repo", _normalize_scope(None, current))

    def test_empty_string_scope_with_current_returns_current_scope(self) -> None:
        current = _short_term(scope="opportunity")
        self.assertEqual("opportunity", _normalize_scope("", current))

    def test_none_scope_with_none_current_defaults_to_contribution(self) -> None:
        self.assertEqual("contribution", _normalize_scope(None, None))

    def test_empty_string_scope_with_none_current_defaults_to_contribution(self) -> None:
        self.assertEqual("contribution", _normalize_scope("", None))

    def test_explicit_repo(self) -> None:
        self.assertEqual("repo", _normalize_scope("repo", None))

    def test_explicit_opportunity(self) -> None:
        self.assertEqual("opportunity", _normalize_scope("opportunity", None))

    def test_explicit_contribution(self) -> None:
        self.assertEqual("contribution", _normalize_scope("contribution", None))

    def test_explicit_scope_ignores_current(self) -> None:
        current = _short_term(scope="repo")
        self.assertEqual("contribution", _normalize_scope("contribution", current))

    def test_invalid_scope_returns_none(self) -> None:
        self.assertIsNone(_normalize_scope("unknown", None))

    def test_invalid_scope_with_current_returns_none(self) -> None:
        current = _short_term(scope="repo")
        self.assertIsNone(_normalize_scope("bogus", current))


# ---------------------------------------------------------------------------
# _has_active_draft
# ---------------------------------------------------------------------------


class HasActiveDraftTest(unittest.TestCase):
    def test_empty_events_is_false(self) -> None:
        self.assertFalse(_has_active_draft([]))

    def test_single_draft_submitted_is_true(self) -> None:
        events = [_event("draft_submitted")]
        self.assertTrue(_has_active_draft(events))

    def test_draft_submitted_then_goal_created_is_false(self) -> None:
        events = [_event("draft_submitted"), _event("goal_created")]
        self.assertFalse(_has_active_draft(events))

    def test_goal_created_then_draft_submitted_is_true(self) -> None:
        events = [_event("goal_created"), _event("draft_submitted")]
        self.assertTrue(_has_active_draft(events))

    def test_draft_submitted_then_goal_completed_is_false(self) -> None:
        events = [_event("draft_submitted"), _event("goal_completed")]
        self.assertFalse(_has_active_draft(events))

    def test_draft_submitted_then_goal_abandoned_is_false(self) -> None:
        events = [_event("draft_submitted"), _event("goal_abandoned")]
        self.assertFalse(_has_active_draft(events))

    def test_draft_submitted_then_goal_superseded_is_false(self) -> None:
        events = [_event("draft_submitted"), _event("goal_superseded")]
        self.assertFalse(_has_active_draft(events))

    def test_goal_updated_then_draft_submitted_is_true(self) -> None:
        events = [_event("goal_updated"), _event("draft_submitted")]
        self.assertTrue(_has_active_draft(events))

    def test_multiple_draft_submitted_last_wins(self) -> None:
        events = [
            _event("draft_submitted"),
            _event("goal_created"),
            _event("draft_submitted"),
        ]
        self.assertTrue(_has_active_draft(events))

    def test_no_draft_events_is_false(self) -> None:
        events = [_event("goal_created"), _event("goal_updated"), _event("goal_completed")]
        self.assertFalse(_has_active_draft(events))


# ---------------------------------------------------------------------------
# _has_current_run_goal_event
# ---------------------------------------------------------------------------


class HasCurrentRunGoalEventTest(unittest.TestCase):
    def test_empty_events_is_false(self) -> None:
        self.assertFalse(_has_current_run_goal_event([]))

    def test_goal_created_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("goal_created")]))

    def test_goal_updated_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("goal_updated")]))

    def test_goal_superseded_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("goal_superseded")]))

    def test_goal_abandoned_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("goal_abandoned")]))

    def test_goal_completed_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("goal_completed")]))

    def test_draft_submitted_is_true(self) -> None:
        self.assertTrue(_has_current_run_goal_event([_event("draft_submitted")]))

    def test_unknown_event_type_is_false(self) -> None:
        self.assertFalse(_has_current_run_goal_event([_event("some_other_event")]))

    def test_mixed_events_any_match_is_true(self) -> None:
        events = [
            _event("some_other_event"),
            _event("goal_created"),
        ]
        self.assertTrue(_has_current_run_goal_event(events))

    def test_multiple_events_all_valid_is_true(self) -> None:
        events = [
            _event("goal_created"),
            _event("goal_updated"),
            _event("draft_submitted"),
            _event("goal_completed"),
        ]
        self.assertTrue(_has_current_run_goal_event(events))


# ---------------------------------------------------------------------------
# _event_type_for_status
# ---------------------------------------------------------------------------


class EventTypeForStatusTest(unittest.TestCase):
    def test_complete(self) -> None:
        self.assertEqual("goal_completed", _event_type_for_status("complete"))

    def test_abandoned(self) -> None:
        self.assertEqual("goal_abandoned", _event_type_for_status("abandoned"))

    def test_superseded(self) -> None:
        self.assertEqual("goal_superseded", _event_type_for_status("superseded"))

    def test_active_defaults_to_goal_updated(self) -> None:
        self.assertEqual("goal_updated", _event_type_for_status("active"))

    def test_unknown_status_defaults_to_goal_updated(self) -> None:
        self.assertEqual("goal_updated", _event_type_for_status("some_unknown_status"))

    def test_empty_string_defaults_to_goal_updated(self) -> None:
        self.assertEqual("goal_updated", _event_type_for_status(""))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _short_term(
    *,
    scope: str = "repo",
    status: str = "active",
) -> ShortTermGoal:
    return ShortTermGoal(
        goal_id="g1",
        objective="test objective",
        status=status,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _event(event_type: str) -> GoalEvent:
    return GoalEvent(
        event_id="e1",
        event_type=event_type,
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
    )
