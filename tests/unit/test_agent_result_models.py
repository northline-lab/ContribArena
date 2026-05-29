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


def _make_repo_summary() -> RepoSummary:
    return RepoSummary(owner="example", name="repo", url="https://github.com/example/repo")


def _make_opportunity() -> OpportunitySummary:
    return OpportunitySummary(title="Fix a bug")


def _make_selected_task() -> SelectedTask:
    return SelectedTask(title="Fix a bug")


class RepoSummaryTest(unittest.TestCase):
    """Tests for the RepoSummary Pydantic model."""

    def test_required_fields(self) -> None:
        rs = RepoSummary(owner="example", name="repo", url="https://github.com/example/repo")
        self.assertEqual("example", rs.owner)
        self.assertEqual("repo", rs.name)
        self.assertEqual("https://github.com/example/repo", rs.url)

    def test_default_branch_defaults_to_empty(self) -> None:
        rs = RepoSummary(owner="example", name="repo", url="https://github.com/example/repo")
        self.assertEqual("", rs.default_branch)

    def test_explicit_default_branch(self) -> None:
        rs = RepoSummary(owner="example", name="repo", url="https://github.com/example/repo", default_branch="main")
        self.assertEqual("main", rs.default_branch)

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoSummary(owner="example", name="repo")  # missing url

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import RepoSummary as RS
        self.assertIs(RS, RepoSummary)


class OpportunitySummaryTest(unittest.TestCase):
    """Tests for the OpportunitySummary Pydantic model."""

    def test_required_fields(self) -> None:
        os_ = OpportunitySummary(title="Fix a bug")
        self.assertEqual("Fix a bug", os_.title)

    def test_defaults(self) -> None:
        os_ = OpportunitySummary(title="Fix a bug")
        self.assertEqual("", os_.rationale)
        self.assertEqual("low", os_.risk)
        self.assertEqual("", os_.source)

    def test_explicit_values(self) -> None:
        os_ = OpportunitySummary(title="Fix a bug", rationale="Good reason", risk="high", source="issue")
        self.assertEqual("Good reason", os_.rationale)
        self.assertEqual("high", os_.risk)
        self.assertEqual("issue", os_.source)

    def test_all_risk_literals_accepted(self) -> None:
        for risk in ("low", "medium", "high"):
            os_ = OpportunitySummary(title="t", risk=risk)
            self.assertEqual(risk, os_.risk)

    def test_invalid_risk_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary(title="t", risk="critical")

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary()

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import OpportunitySummary as OS
        self.assertIs(OS, OpportunitySummary)


class SelectedTaskTest(unittest.TestCase):
    """Tests for the SelectedTask Pydantic model."""

    def test_required_fields(self) -> None:
        st = SelectedTask(title="Fix a bug")
        self.assertEqual("Fix a bug", st.title)

    def test_defaults(self) -> None:
        st = SelectedTask(title="Fix a bug")
        self.assertEqual("", st.rationale)
        self.assertEqual("", st.expected_change)
        self.assertEqual("low", st.risk)

    def test_explicit_values(self) -> None:
        st = SelectedTask(title="Fix a bug", rationale="Reason", expected_change="Add tests", risk="medium")
        self.assertEqual("Reason", st.rationale)
        self.assertEqual("Add tests", st.expected_change)
        self.assertEqual("medium", st.risk)

    def test_all_risk_literals_accepted(self) -> None:
        for risk in ("low", "medium", "high"):
            st = SelectedTask(title="t", risk=risk)
            self.assertEqual(risk, st.risk)

    def test_invalid_risk_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask(title="t", risk="critical")

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask()

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import SelectedTask as ST
        self.assertIs(ST, SelectedTask)


class WorkspaceSummaryTest(unittest.TestCase):
    """Tests for the WorkspaceSummary Pydantic model."""

    def test_defaults(self) -> None:
        ws = WorkspaceSummary()
        self.assertEqual([], ws.commands_run)
        self.assertFalse(ws.patch_applied)
        self.assertEqual("", ws.notes)

    def test_explicit_values(self) -> None:
        cmd = CommandResult(command="ls", exit_code=0, duration_seconds=0.5)
        ws = WorkspaceSummary(commands_run=[cmd], patch_applied=True, notes="patch applied")
        self.assertEqual(1, len(ws.commands_run))
        self.assertTrue(ws.patch_applied)
        self.assertEqual("patch applied", ws.notes)

    def test_commands_run_factory_independence(self) -> None:
        ws1 = WorkspaceSummary()
        ws2 = WorkspaceSummary()
        ws1.commands_run.append(CommandResult(command="ls", exit_code=0, duration_seconds=0.1))
        self.assertEqual(0, len(ws2.commands_run))

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import WorkspaceSummary as WS
        self.assertIs(WS, WorkspaceSummary)


class AgentFinalResultTest(unittest.TestCase):
    """Tests for the AgentFinalResult Pydantic model."""

    def test_required_fields(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=_make_repo_summary(),
            repo_profile="A test repo",
            opportunities=[_make_opportunity()],
            selected_task=_make_selected_task(),
        )
        self.assertEqual("completed", result.status)
        self.assertEqual("example", result.repo.owner)
        self.assertEqual("A test repo", result.repo_profile)
        self.assertEqual(1, len(result.opportunities))
        self.assertEqual("Fix a bug", result.selected_task.title)

    def test_defaults(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=_make_repo_summary(),
            repo_profile="A test repo",
            opportunities=[],
            selected_task=_make_selected_task(),
        )
        self.assertIsInstance(result.workspace_summary, WorkspaceSummary)
        self.assertEqual([], result.blockers)
        self.assertEqual("", result.problem_statement_summary)
        self.assertEqual("", result.reproduction_notes)
        self.assertEqual("", result.verification_summary)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("completed", "blocked", "failed"):
            result = AgentFinalResult(
                status=status,
                repo=_make_repo_summary(),
                repo_profile="A test repo",
                opportunities=[],
                selected_task=_make_selected_task(),
            )
            self.assertEqual(status, result.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(
                status="running",
                repo=_make_repo_summary(),
                repo_profile="A test repo",
                opportunities=[],
                selected_task=_make_selected_task(),
            )

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(status="completed")  # missing repo, repo_profile, opportunities, selected_task

    def test_blockers_factory_independence(self) -> None:
        result1 = AgentFinalResult(
            status="completed",
            repo=_make_repo_summary(),
            repo_profile="A test repo",
            opportunities=[],
            selected_task=_make_selected_task(),
        )
        result2 = AgentFinalResult(
            status="completed",
            repo=_make_repo_summary(),
            repo_profile="A test repo",
            opportunities=[],
            selected_task=_make_selected_task(),
        )
        result1.blockers.append("something")
        self.assertEqual(0, len(result2.blockers))

    def test_explicit_optional_values(self) -> None:
        result = AgentFinalResult(
            status="blocked",
            repo=_make_repo_summary(),
            repo_profile="A test repo",
            opportunities=[],
            selected_task=_make_selected_task(),
            blockers=["CI failure"],
            problem_statement_summary="Issue summary",
            reproduction_notes="Steps to reproduce",
            verification_summary="All tests pass",
        )
        self.assertEqual("blocked", result.status)
        self.assertEqual(["CI failure"], result.blockers)
        self.assertEqual("Issue summary", result.problem_statement_summary)
        self.assertEqual("Steps to reproduce", result.reproduction_notes)
        self.assertEqual("All tests pass", result.verification_summary)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import AgentFinalResult as AFR
        self.assertIs(AFR, AgentFinalResult)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
