from __future__ import annotations

import unittest
from enum import StrEnum

from contribarena.models import RunState as RunStateFromPackage
from contribarena.models.run_state import RunState


# RunState values are part of the trace event contract written to
# trace.jsonl by TraceWriter and consumed by downstream tooling, so
# pinning the full {name: value} mapping guards against accidental
# renames or value drift. New states are appended to this map alongside
# the enum.
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


class RunStateTypeTest(unittest.TestCase):
    """RunState is a StrEnum so members are usable as plain strings."""

    def test_runstate_is_str_enum(self) -> None:
        self.assertTrue(issubclass(RunState, StrEnum))

    def test_member_is_a_string(self) -> None:
        self.assertIsInstance(RunState.RUN_STARTED, str)

    def test_member_compares_equal_to_string_value(self) -> None:
        # StrEnum members compare equal to their string value, which is
        # what TraceWriter relies on when serializing event state.
        self.assertEqual("run_started", RunState.RUN_STARTED)
        self.assertEqual(RunState.RUN_STARTED, "run_started")

    def test_member_value_attribute(self) -> None:
        self.assertEqual("agent_error", RunState.AGENT_ERROR.value)


class RunStateMembersTest(unittest.TestCase):
    """Pin the full set of RunState members and their string values."""

    def test_all_expected_members_present(self) -> None:
        actual = {m.name: m.value for m in RunState}
        self.assertEqual(EXPECTED_MEMBERS, actual)

    def test_values_are_unique(self) -> None:
        values = [m.value for m in RunState]
        self.assertEqual(len(values), len(set(values)))


class RunStateLookupTest(unittest.TestCase):
    """RunState supports value-based and name-based lookup."""

    def test_lookup_by_value(self) -> None:
        self.assertIs(RunState.RUN_STARTED, RunState("run_started"))

    def test_lookup_by_name(self) -> None:
        self.assertIs(RunState.RUN_STARTED, RunState["RUN_STARTED"])

    def test_unknown_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            RunState("definitely_not_a_run_state")

    def test_unknown_name_raises(self) -> None:
        with self.assertRaises(KeyError):
            RunState["DEFINITELY_NOT_A_RUN_STATE"]


class RunStatePackageExportTest(unittest.TestCase):
    """RunState is exported from the top-level contribarena.models package."""

    def test_package_export_is_same_class(self) -> None:
        self.assertIs(RunState, RunStateFromPackage)


if __name__ == "__main__":
    unittest.main()
