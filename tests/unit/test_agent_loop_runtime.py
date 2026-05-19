from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.agent import AgentInvocationResult
from contribarena.config.schema import ArtifactConfig, BudgetConfig, DiscoveryConfig, RepoCandidate
from contribarena.config.schema import RunConfig
from contribarena.config.schema import MemoryConfig, RunSection, WorkspaceConfig
from contribarena.engine.agent_loop import (
    AgentLoopState,
    agent_loop_progress,
    capture_cursor,
    derive_agent_result,
    render_continuation_context,
    review_invocation,
)
from contribarena.engine.goals import GoalService
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.errors import BudgetExhausted
from contribarena.models import (
    AciResult,
    AgentFinalResult,
    AgentStep,
    CommandResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.agent_result import WorkspaceSummary


class AgentLoopRuntimeTest(unittest.TestCase):
    def test_budget_steps_reset_per_invocation_and_total_steps_accumulate(self) -> None:
        budget = BudgetTracker(BudgetConfig(max_steps=2))

        budget.record_step()
        budget.record_step()
        with self.assertRaises(BudgetExhausted):
            budget.record_step()

        self.assertEqual(3, budget.total_steps)
        budget.reset_steps_for_invocation()
        budget.record_step()

        self.assertEqual(1, budget.steps)
        self.assertEqual(4, budget.total_steps)

    def test_plain_content_without_progress_continues_once_then_fails_to_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            goals.update(objective="Submit a small patch.", status="active")
            state = AgentLoopState()

            first = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(content="I will inspect next."),
            )
            second = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(content="Still thinking."),
            )

            self.assertEqual("continue", first.decision)
            self.assertEqual("terminal", second.decision)
            self.assertIsNotNone(second.terminal)
            self.assertEqual("failed_to_recover", second.terminal.reason)
            self.assertEqual("failed_to_recover.no_progress", second.sub_reason)

    def test_successful_patch_submission_terminals_into_downstream_gate_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()
            before = capture_cursor(capture, goals, None)
            capture.record_command(
                CommandResult(
                    command="git clone https://github.com/example/repo repo",
                    exit_code=0,
                    duration_seconds=1.0,
                )
            )
            capture.record_aci_result(AciResult(tool="aci_view", success=True, output="old"))
            capture.record_aci_result(AciResult(tool="aci_replace", success=True))
            capture.record_aci_result(AciResult(tool="aci_verify", success=True, output="ok"))
            capture.record_aci_result(
                AciResult(
                    tool="aci_submit_patch",
                    success=True,
                    output="diff --git a/repo/app.py b/repo/app.py\n",
                )
            )

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before,
                state=state,
                invocation=AgentInvocationResult(content="Patch submitted."),
            )
            result = derive_agent_result(
                config=config,
                capture=capture,
                goals=goals,
                loop_state=state,
            )

            self.assertEqual("terminal", review.decision)
            self.assertEqual("patch_submitted", review.reason)
            self.assertEqual("patch_submitted", review.outcome)
            self.assertEqual(1, state.counters.invocations_used)
            self.assertEqual("completed", result.status)
            self.assertTrue(result.workspace_summary.patch_applied)
            self.assertIn("aci_verify passed", result.verification_summary)

    def test_missing_terminal_and_missing_patch_derive_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()

            result = derive_agent_result(
                config=config,
                capture=capture,
                goals=goals,
                loop_state=state,
            )

            self.assertEqual("blocked", result.status)
            self.assertFalse(result.workspace_summary.patch_applied)

    def test_goal_abandon_limit_terminals_before_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()
            capture.record_aci_result(
                AciResult(
                    tool="aci_goal_update",
                    success=True,
                    terminal_status="goal_abandon_limit",
                    error="goal abandon limit reached",
                )
            )

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(content=""),
            )

            self.assertEqual("terminal", review.decision)
            self.assertIsNotNone(review.terminal)
            self.assertEqual("goal_abandon_limit", review.terminal.reason)
            self.assertEqual("goal_abandon_limit", review.outcome)

    def test_single_abandoned_goal_with_evidence_is_blocked_not_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()
            legacy = _legacy_result("blocked", blockers=["task abandoned with evidence"])

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(
                    content="Task abandoned.",
                    stopped_reason="legacy_final_result",
                    legacy_final_result=legacy,
                ),
            )
            result = derive_agent_result(
                config=config,
                capture=capture,
                goals=goals,
                loop_state=state,
            )

            self.assertEqual("terminal", review.decision)
            self.assertEqual("legacy_terminal", review.outcome)
            self.assertEqual("blocked", result.status)
            self.assertIn("task abandoned with evidence", result.blockers)

    def test_legacy_completed_without_patch_is_single_invocation_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(
                    content="local-stub done",
                    stopped_reason="local_stub",
                    legacy_final_result=_legacy_result("completed"),
                ),
            )

            self.assertEqual("terminal", review.decision)
            self.assertEqual("legacy_terminal", review.outcome)
            self.assertEqual(1, state.counters.invocations_used)

    def test_provider_error_terminals_as_model_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(
                    content="Provider invocation failed.",
                    stopped_reason="provider_error",
                    error_message="HTTP 400",
                ),
            )

            self.assertEqual("terminal", review.decision)
            self.assertEqual("model_runtime", review.outcome)
            self.assertIsNotNone(review.terminal)
            self.assertEqual("model_runtime", review.terminal.reason)
            self.assertEqual("model_runtime", review.terminal.layer)

    def test_partial_progress_then_continuation_can_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            goals.update(objective="Finish the patch.", status="active")
            state = AgentLoopState()
            before_first = capture_cursor(capture, goals, None)
            capture.record_command(
                CommandResult(
                    command="git clone https://github.com/example/repo repo",
                    exit_code=0,
                    duration_seconds=1.0,
                )
            )
            first = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before_first,
                state=state,
                invocation=AgentInvocationResult(content="Repo cloned; continuing."),
            )
            continuation = render_continuation_context(
                config=config,
                goals=goals,
                state=state,
                progress=first.progress,
            )
            before_second = capture_cursor(capture, goals, None)
            capture.record_aci_result(AciResult(tool="aci_view", success=True, output="old"))
            capture.record_aci_result(AciResult(tool="aci_replace", success=True))
            capture.record_aci_result(AciResult(tool="aci_verify", success=True, output="ok"))
            capture.record_aci_result(
                AciResult(
                    tool="aci_submit_patch",
                    success=True,
                    output="diff --git a/repo/app.py b/repo/app.py\n",
                )
            )
            second = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before_second,
                state=state,
                invocation=AgentInvocationResult(content="Patch submitted."),
            )

            self.assertEqual("continue", first.decision)
            self.assertIn("Lifecycle Gaps", continuation)
            self.assertEqual("terminal", second.decision)
            self.assertEqual("patch_submitted", second.outcome)

    def test_continuation_context_keeps_goal_and_gaps_when_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            goals.update(objective="Finish the patch.", status="active")
            state = AgentLoopState(
                last_invocation_note="x" * 2000,
                recent_tool_summaries=["old", "new"],
                recovery_warning="Previous invocation ended without progress.",
            )

            text = render_continuation_context(
                config=config,
                goals=goals,
                state=state,
                progress=agent_loop_progress(capture, goals),
                max_bytes=500,
            )

            self.assertIn("Current Goal", text)
            self.assertIn("Finish the patch.", text)
            self.assertIn("Lifecycle Gaps", text)
            self.assertLessEqual(len(text.encode("utf-8")), 500)

    def test_progressful_last_invocation_is_reviewed_before_budget_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp), max_invocations=1)
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()
            before = capture_cursor(capture, goals, None)
            capture.record_command(
                CommandResult(
                    command="git clone https://github.com/example/repo repo",
                    exit_code=0,
                    duration_seconds=1.0,
                )
            )

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before,
                state=state,
                invocation=AgentInvocationResult(content="Repository cloned."),
            )

            self.assertEqual("terminal", review.decision)
            self.assertEqual("budget_exhausted", review.outcome)
            self.assertIsNotNone(review.delta)
            self.assertEqual(1, review.delta.commands)
            self.assertTrue(review.delta.made_progress)
            self.assertEqual(0, state.counters.consecutive_no_progress)
            self.assertEqual(1, state.counters.invocations_used)

    def test_scout_invocation_budget_records_scout_budget_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp), max_invocations=5)
            config.run.budget.scout.max_scout_invocations = 1
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            goals.update(objective="Find a repository.", status="active", scope="repo")
            state = AgentLoopState()
            before = capture_cursor(capture, goals, None)
            capture.record_command(
                CommandResult(
                    command="git clone https://github.com/example/repo repo",
                    exit_code=0,
                    duration_seconds=1.0,
                )
            )

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before,
                state=state,
                invocation=AgentInvocationResult(content="Repository cloned."),
            )

            self.assertEqual("continue", review.decision)
            self.assertEqual("scout_budget_exhausted_select_or_abandon", review.reason)
            self.assertIsNone(review.terminal)
            self.assertIn("scout_budget_exhausted", goals.events_text())

    def test_phase_invocation_budgets_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp), max_invocations=5)
            config.run.budget.scout.max_scout_invocations = 2
            config.run.budget.work.max_invocations = 1
            capture = ArtifactCapture()
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()
            goals.update(objective="Find a repository.", status="active", scope="repo")
            capture.record_command(
                CommandResult(
                    command="git clone https://github.com/example/repo repo",
                    exit_code=0,
                    duration_seconds=1.0,
                )
            )
            scout_review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(capture, goals, None),
                state=state,
                invocation=AgentInvocationResult(content="Scout progress."),
            )
            goals.update(objective="Implement the fix.", status="active", scope="contribution")
            before_work = capture_cursor(capture, goals, None)
            capture.record_aci_result(AciResult(tool="aci_view", success=True, output="old"))

            work_review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before_work,
                state=state,
                invocation=AgentInvocationResult(content="Work progress."),
            )

            self.assertEqual("continue", scout_review.decision)
            self.assertEqual(1, state.counters.scout_invocations_used)
            self.assertEqual(1, state.counters.work_invocations_used)
            self.assertEqual("terminal", work_review.decision)
            self.assertEqual("budget_exhausted", work_review.reason)
            self.assertEqual("work max_invocations exceeded: 1", work_review.terminal.message)

    def test_continuation_state_redacts_notes_and_tool_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            capture = ArtifactCapture()
            capture.record_step(
                AgentStep(
                    step=1,
                    phase="coding",
                    tool="aci_verify",
                    result_summary="authorization: bearer ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    state="ok",
                    duration_seconds=0.1,
                )
            )
            goals = GoalService(config, run_id="run")
            state = AgentLoopState()

            review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=capture_cursor(ArtifactCapture(), goals, None),
                state=state,
                invocation=AgentInvocationResult(content="token=supersecretvalue123456"),
            )

            self.assertNotIn("supersecretvalue123456", state.last_invocation_note)
            self.assertIn("token=***", state.last_invocation_note)
            self.assertEqual(1, len(state.recent_tool_summaries))
            self.assertNotIn("ghp_", state.recent_tool_summaries[0])


def _config(tmp_path: Path, *, max_invocations: int = 5) -> RunConfig:
    return RunConfig(
        run=RunSection(
            mode="shadow",
            model="local-stub",
            budget=BudgetConfig(max_invocations=max_invocations),
        ),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        workspace=WorkspaceConfig(command_timeout_seconds=10),
        artifacts=ArtifactConfig(output_root=tmp_path / "runs"),
        memory=MemoryConfig(root=tmp_path / "memory"),
    )


def _legacy_result(status: str, blockers: list[str] | None = None) -> AgentFinalResult:
    return AgentFinalResult(
        status=status,  # type: ignore[arg-type]
        repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
        repo_profile="# Repo Profile\n",
        opportunities=[
            OpportunitySummary(title="Example", rationale="fixture", risk="low", source="test")
        ],
        selected_task=SelectedTask(
            title="Example",
            rationale="fixture",
            expected_change="change",
            risk="low",
        ),
        workspace_summary=WorkspaceSummary(patch_applied=False),
        blockers=blockers or [],
    )


if __name__ == "__main__":
    unittest.main()
