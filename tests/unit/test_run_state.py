from __future__ import annotations

import unittest

from contribarena.models.run_state import RunState

# Expected member name -> value mapping, ordered by definition.
# Any addition or removal should update this dict; the count assertion
# is derived from the dict length, not hardcoded.
EXPECTED_MEMBERS: dict[str, str] = {
    "RUN_STARTED": "run_started",
    "CONFIG_LOADED": "config_loaded",
    "WORKSPACE_STARTING": "workspace_starting",
    "WORKSPACE_READY": "workspace_ready",
    "WORKSPACE_DIRTY": "workspace_dirty",
    "WORKSPACE_PATCH_CAPTURED": "workspace_patch_captured",
    "WORKSPACE_STOPPING": "workspace_stopping",
    "WORKSPACE_STOPPED": "workspace_stopped",
    "WORKSPACE_RETAINED": "workspace_retained",
    "WORKSPACE_CLEANUP_FAILED": "workspace_cleanup_failed",
    "AGENT_INITIALIZED": "agent_initialized",
    "AGENT_CONTEXT_LOADED": "agent_context_loaded",
    "AGENT_ACTING": "agent_acting",
    "AGENT_RECOVERING": "agent_recovering",
    "MODEL_TURN_STARTED": "model_turn_started",
    "MODEL_TURN_HEARTBEAT": "model_turn_heartbeat",
    "MODEL_TURN_FINISHED": "model_turn_finished",
    "MODEL_TURN_FAILED": "model_turn_failed",
    "AGENT_FINAL_RESULT": "agent_final_result",
    "AGENT_HARNESS_REVIEWED": "agent_harness_reviewed",
    "REPO_DISCOVERED": "repo_discovered",
    "REPO_ELIGIBLE": "repo_eligible",
    "REPO_PROFILED": "repo_profiled",
    "OPPORTUNITIES_RANKED": "opportunities_ranked",
    "TASK_SELECTED": "task_selected",
    "WORKSPACE_CHECKED": "workspace_checked",
    "CONTRIBUTION_REVIEWED": "contribution_reviewed",
    "PR_DRY_RUN_STARTED": "pr_dry_run_started",
    "PR_DRAFT_CREATED": "pr_draft_created",
    "CI_OBSERVED": "ci_observed",
    "POSTMORTEM_WRITTEN": "postmortem_written",
    "ARTIFACTS_WRITTEN": "artifacts_written",
    "RUN_COMPLETED": "run_completed",
    "RUN_TERMINAL": "run_terminal",
    "WORKSPACE_FAILED": "workspace_failed",
    "GOVERNANCE_BLOCKED": "governance_blocked",
    "BUDGET_EXHAUSTED": "budget_exhausted",
    "AGENT_ERROR": "agent_error",
}


class RunStateStrEnumTest(unittest.TestCase):
    """Unit tests for the RunState StrEnum contract.

    RunState values are the runtime state vocabulary used by the harness
    lifecycle, CLI exit codes, and season record fields. Pinning every
    member name and value against EXPECTED_MEMBERS guards against
    accidental renaming or value changes that would silently break
    state transitions.
    """

    # --- structural contract ---

    def test_member_count_matches_expected(self) -> None:
        """Enum size should equal the EXPECTED_MEMBERS dict length."""
        self.assertEqual(len(RunState), len(EXPECTED_MEMBERS))

    def test_is_str_enum(self) -> None:
        self.assertTrue(isinstance(RunState.RUN_STARTED, str))
        self.assertEqual(str(RunState.RUN_STARTED), "run_started")

    def test_all_values_unique(self) -> None:
        """Every StrEnum member should have a distinct string value."""
        values = [m.value for m in RunState]
        self.assertEqual(len(values), len(set(values)))

    def test_member_order_matches_definition(self) -> None:
        """Full iteration order should match the EXPECTED_MEMBERS key order."""
        actual_order = [m.name for m in RunState]
        expected_order = list(EXPECTED_MEMBERS.keys())
        self.assertEqual(actual_order, expected_order)

    # --- every expected member name and value ---

    def test_all_expected_members_present_and_correct(self) -> None:
        """Every entry in EXPECTED_MEMBERS must exist in RunState with the correct value."""
        for name, value in EXPECTED_MEMBERS.items():
            member = RunState[name]
            self.assertEqual(member, value, f"RunState.{name} expected '{value}'")

    def test_no_extra_members(self) -> None:
        """RunState should not contain members beyond EXPECTED_MEMBERS."""
        actual_names = {m.name for m in RunState}
        expected_names = set(EXPECTED_MEMBERS.keys())
        self.assertEqual(actual_names, expected_names)

    # --- enum behavior ---

    def test_value_lookup(self) -> None:
        """RunState('run_started') should resolve to RUN_STARTED."""
        self.assertEqual(RunState("run_started"), RunState.RUN_STARTED)

    def test_name_lookup(self) -> None:
        """RunState['RUN_STARTED'] should resolve to RUN_STARTED."""
        self.assertEqual(RunState["RUN_STARTED"], RunState.RUN_STARTED)

    def test_invalid_value_raises(self) -> None:
        """Lookup by a value not in the enum should raise ValueError."""
        with self.assertRaises(ValueError):
            RunState("nonexistent_state")

    def test_invalid_name_raises(self) -> None:
        with self.assertRaises(KeyError):
            RunState["NONEXISTENT_STATE"]

    def test_importable_from_models_package(self) -> None:
        """RunState should be importable from the top-level models package."""
        from contribarena.models import RunState as ImportedRunState
        self.assertIs(ImportedRunState, RunState)
