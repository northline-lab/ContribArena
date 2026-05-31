from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.agent_result import (
    AgentFinalResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
    WorkspaceSummary,
)
from contribarena.models.tool_results import CommandResult


class RepoSummaryTest(unittest.TestCase):
    """Tests for RepoSummary Pydantic model."""

    def test_required_fields(self) -> None:
        obj = RepoSummary(owner="qWaitCrypto", name="ContribArena", url="https://github.com/qWaitCrypto/ContribArena")
        self.assertEqual("qWaitCrypto", obj.owner)
        self.assertEqual("ContribArena", obj.name)
        self.assertEqual("https://github.com/qWaitCrypto/ContribArena", obj.url)

    def test_default_branch_is_empty_string(self) -> None:
        obj = RepoSummary(owner="o", name="r", url="u")
        self.assertEqual("", obj.default_branch)

    def test_explicit_default_branch(self) -> None:
        obj = RepoSummary(owner="o", name="r", url="u", default_branch="main")
        self.assertEqual("main", obj.default_branch)

    def test_missing_owner_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoSummary(name="r", url="u")  # type: ignore[call-arg]

    def test_missing_name_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoSummary(owner="o", url="u")  # type: ignore[call-arg]

    def test_missing_url_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoSummary(owner="o", name="r")  # type: ignore[call-arg]


class OpportunitySummaryTest(unittest.TestCase):
    """Tests for OpportunitySummary Pydantic model."""

    def test_required_fields(self) -> None:
        obj = OpportunitySummary(title="Add unit tests")
        self.assertEqual("Add unit tests", obj.title)

    def test_defaults(self) -> None:
        obj = OpportunitySummary(title="t")
        self.assertEqual("", obj.rationale)
        self.assertEqual("low", obj.risk)
        self.assertEqual("", obj.source)

    def test_explicit_values(self) -> None:
        obj = OpportunitySummary(title="t", rationale="r", risk="high", source="issue")
        self.assertEqual("r", obj.rationale)
        self.assertEqual("high", obj.risk)
        self.assertEqual("issue", obj.source)

    def test_all_risk_literals_accepted(self) -> None:
        for risk in ("low", "medium", "high"):
            OpportunitySummary(title="t", risk=risk)

    def test_invalid_risk_raises(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary(title="t", risk="critical")  # type: ignore[call-arg]

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary()  # type: ignore[call-arg]


class SelectedTaskTest(unittest.TestCase):
    """Tests for SelectedTask Pydantic model."""

    def test_required_fields(self) -> None:
        obj = SelectedTask(title="Add unit tests")
        self.assertEqual("Add unit tests", obj.title)

    def test_defaults(self) -> None:
        obj = SelectedTask(title="t")
        self.assertEqual("", obj.rationale)
        self.assertEqual("", obj.expected_change)
        self.assertEqual("low", obj.risk)

    def test_explicit_values(self) -> None:
        obj = SelectedTask(title="t", rationale="r", expected_change="ec", risk="medium")
        self.assertEqual("r", obj.rationale)
        self.assertEqual("ec", obj.expected_change)
        self.assertEqual("medium", obj.risk)

    def test_all_risk_literals_accepted(self) -> None:
        for risk in ("low", "medium", "high"):
            SelectedTask(title="t", risk=risk)

    def test_invalid_risk_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask(title="t", risk="unknown")  # type: ignore[call-arg]

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask()  # type: ignore[call-arg]


class WorkspaceSummaryTest(unittest.TestCase):
    """Tests for WorkspaceSummary Pydantic model."""

    def test_defaults(self) -> None:
        obj = WorkspaceSummary()
        self.assertEqual([], obj.commands_run)
        self.assertFalse(obj.patch_applied)
        self.assertEqual("", obj.notes)

    def test_explicit_values(self) -> None:
        cmd = CommandResult(command="ls", exit_code=0, duration_seconds=1.0)
        obj = WorkspaceSummary(commands_run=[cmd], patch_applied=True, notes="done")
        self.assertEqual(1, len(obj.commands_run))
        self.assertTrue(obj.patch_applied)
        self.assertEqual("done", obj.notes)

    def test_commands_run_factory_independence(self) -> None:
        a = WorkspaceSummary()
        b = WorkspaceSummary()
        a.commands_run.append(CommandResult(command="ls", exit_code=0, duration_seconds=1.0))
        self.assertEqual(0, len(b.commands_run))


class AgentFinalResultTest(unittest.TestCase):
    """Tests for AgentFinalResult Pydantic model."""

    def _make_repo(self) -> RepoSummary:
        return RepoSummary(owner="o", name="r", url="u")

    def _make_task(self) -> SelectedTask:
        return SelectedTask(title="t")

    def test_required_fields(self) -> None:
        obj = AgentFinalResult(
            status="completed",
            repo=self._make_repo(),
            repo_profile="profile",
            opportunities=[],
            selected_task=self._make_task(),
        )
        self.assertEqual("completed", obj.status)
        self.assertEqual("o", obj.repo.owner)
        self.assertEqual("profile", obj.repo_profile)
        self.assertEqual([], obj.opportunities)
        self.assertEqual("t", obj.selected_task.title)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("completed", "blocked", "failed"):
            AgentFinalResult(
                status=status,
                repo=self._make_repo(),
                repo_profile="p",
                opportunities=[],
                selected_task=self._make_task(),
            )

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(  # type: ignore[call-arg]
                status="running",
                repo=self._make_repo(),
                repo_profile="p",
                opportunities=[],
                selected_task=self._make_task(),
            )

    def test_defaults(self) -> None:
        obj = AgentFinalResult(
            status="completed",
            repo=self._make_repo(),
            repo_profile="p",
            opportunities=[],
            selected_task=self._make_task(),
        )
        self.assertIsInstance(obj.workspace_summary, WorkspaceSummary)
        self.assertEqual([], obj.blockers)
        self.assertEqual("", obj.problem_statement_summary)
        self.assertEqual("", obj.reproduction_notes)
        self.assertEqual("", obj.verification_summary)

    def test_explicit_values(self) -> None:
        ws = WorkspaceSummary(patch_applied=True, notes="n")
        opp = OpportunitySummary(title="opp", risk="medium")
        obj = AgentFinalResult(
            status="blocked",
            repo=self._make_repo(),
            repo_profile="p",
            opportunities=[opp],
            selected_task=self._make_task(),
            workspace_summary=ws,
            blockers=["b1"],
            problem_statement_summary="ps",
            reproduction_notes="rn",
            verification_summary="vs",
        )
        self.assertTrue(obj.workspace_summary.patch_applied)
        self.assertEqual(["b1"], obj.blockers)
        self.assertEqual("ps", obj.problem_statement_summary)
        self.assertEqual("rn", obj.reproduction_notes)
        self.assertEqual("vs", obj.verification_summary)
        self.assertEqual(1, len(obj.opportunities))

    def test_blockers_factory_independence(self) -> None:
        a = AgentFinalResult(
            status="completed",
            repo=self._make_repo(),
            repo_profile="p",
            opportunities=[],
            selected_task=self._make_task(),
        )
        b = AgentFinalResult(
            status="completed",
            repo=self._make_repo(),
            repo_profile="p",
            opportunities=[],
            selected_task=self._make_task(),
        )
        a.blockers.append("x")
        self.assertEqual([], b.blockers)

    def test_missing_required_fields_raise(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult()  # type: ignore[call-arg]

    def test_missing_repo_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(  # type: ignore[call-arg]
                status="completed",
                repo_profile="p",
                opportunities=[],
                selected_task=self._make_task(),
            )

    def test_missing_repo_profile_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(  # type: ignore[call-arg]
                status="completed",
                repo=self._make_repo(),
                opportunities=[],
                selected_task=self._make_task(),
            )

    def test_missing_selected_task_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(  # type: ignore[call-arg]
                status="completed",
                repo=self._make_repo(),
                repo_profile="p",
                opportunities=[],
            )

    def test_dict_input_coercion_for_nested_repo(self) -> None:
        obj = AgentFinalResult(
            status="completed",
            repo={"owner": "o", "name": "r", "url": "u"},
            repo_profile="p",
            opportunities=[],
            selected_task={"title": "t"},
        )
        self.assertIsInstance(obj.repo, RepoSummary)
        self.assertIsInstance(obj.selected_task, SelectedTask)


class AgentResultImportTest(unittest.TestCase):
    """All agent_result model classes are importable from contribarena.models."""

    def test_all_symbols_importable(self) -> None:
        from contribarena.models import (
            AgentFinalResult,
            OpportunitySummary,
            RepoSummary,
            SelectedTask,
            WorkspaceSummary,
        )

        symbols = [AgentFinalResult, OpportunitySummary, RepoSummary, SelectedTask, WorkspaceSummary]
        self.assertEqual(5, len(symbols))


if __name__ == "__main__":
    unittest.main()
