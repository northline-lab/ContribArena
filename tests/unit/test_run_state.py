from __future__ import annotations

import unittest
from enum import StrEnum

from contribarena.models.run_state import RunState


class RunStateEnumTest(unittest.TestCase):
    """RunState is a StrEnum that enumerates the discrete lifecycle states
    emitted by the harness, workspace, agent loop, and governance layers."""

    def test_run_state_is_str_enum(self) -> None:
        self.assertTrue(issubclass(RunState, StrEnum))
        self.assertTrue(issubclass(RunState, str))

    def test_every_member_is_a_string(self) -> None:
        for member in RunState:
            self.assertIsInstance(member, str)
            self.assertEqual(member, member.value)

    def test_all_values_are_unique(self) -> None:
        values = [m.value for m in RunState]
        self.assertEqual(len(values), len(set(values)))

    def test_all_values_are_non_empty_strings(self) -> None:
        for member in RunState:
            self.assertIsInstance(member.value, str)
            self.assertTrue(member.value)

    def test_all_values_are_snake_case_lowercase(self) -> None:
        for member in RunState:
            self.assertEqual(member.value, member.value.lower())
            self.assertNotIn(" ", member.value)

    def test_lookup_by_name_and_value_round_trips(self) -> None:
        for member in RunState:
            self.assertIs(RunState[member.name], member)
            self.assertIs(RunState(member.value), member)

    def test_str_representation_matches_value(self) -> None:
        for member in RunState:
            self.assertEqual(str(member), member.value)

    def test_membership_via_in_operator(self) -> None:
        self.assertIn(RunState.RUN_STARTED, RunState)
        self.assertIn("run_started", [m.value for m in RunState])
        self.assertNotIn("nonexistent_state", [m.value for m in RunState])

    def test_expected_total_member_count(self) -> None:
        # Regression guard: if a new state is added or removed, this count
        # must be updated alongside the enum definition.
        self.assertEqual(38, len(RunState))


class RunStateCoverageTest(unittest.TestCase):
    """Spot-check the canonical states referenced across the engine modules
    so that accidental renames surface as test failures."""

    EXPECTED_STATES = {
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

    def test_canonical_state_names_are_present(self) -> None:
        for name, value in self.EXPECTED_STATES.items():
            with self.subTest(name=name):
                self.assertIs(RunState[name].value, value)
                self.assertEqual(value, RunState[name])

    def test_no_unexpected_members_outside_canonical_set(self) -> None:
        actual = {m.name for m in RunState}
        expected = set(self.EXPECTED_STATES.keys())
        self.assertEqual(actual, expected)

    def test_terminal_states_are_present(self) -> None:
        terminal_names = {
            "RUN_TERMINAL",
            "WORKSPACE_FAILED",
            "GOVERNANCE_BLOCKED",
            "BUDGET_EXHAUSTED",
            "AGENT_ERROR",
        }
        actual = {m.name for m in RunState}
        self.assertTrue(terminal_names.issubset(actual))

    def test_run_states_can_be_used_as_plain_strings(self) -> None:
        # Callers frequently pass the value through to JSON / log payloads
        # where a plain str is required.
        state: str = RunState.RUN_COMPLETED
        self.assertEqual("run_completed", state)
        self.assertEqual("run_completed", f"{state}")
        self.assertTrue(state.startswith("run_"))
