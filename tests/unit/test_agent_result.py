from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models import (
    AgentFinalResult,
    CommandResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.agent_result import WorkspaceSummary


class RepoSummaryTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        summary = RepoSummary(
            owner="example",
            name="repo",
            url="https://github.com/example/repo",
        )
        self.assertEqual("example", summary.owner)
        self.assertEqual("repo", summary.name)
        self.assertEqual("https://github.com/example/repo", summary.url)

    def test_default_branch_is_empty_string(self) -> None:
        summary = RepoSummary(
            owner="example",
            name="repo",
            url="https://github.com/example/repo",
        )
        self.assertEqual("", summary.default_branch)

    def test_default_branch_can_be_set(self) -> None:
        summary = RepoSummary(
            owner="example",
            name="repo",
            url="https://github.com/example/repo",
            default_branch="main",
        )
        self.assertEqual("main", summary.default_branch)

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoSummary(owner="example", name="repo")  # type: ignore[call-arg]


class OpportunitySummaryTest(unittest.TestCase):
    def test_required_field_only(self) -> None:
        opp = OpportunitySummary(title="Fix typo")
        self.assertEqual("Fix typo", opp.title)

    def test_default_values(self) -> None:
        opp = OpportunitySummary(title="Fix typo")
        self.assertEqual("", opp.rationale)
        self.assertEqual("low", opp.risk)
        self.assertEqual("", opp.source)

    def test_full_construction(self) -> None:
        opp = OpportunitySummary(
            title="Add tests",
            rationale="Coverage gap",
            risk="medium",
            source="issue-7",
        )
        self.assertEqual("Add tests", opp.title)
        self.assertEqual("Coverage gap", opp.rationale)
        self.assertEqual("medium", opp.risk)
        self.assertEqual("issue-7", opp.source)

    def test_risk_accepts_known_literals(self) -> None:
        for value in ("low", "medium", "high"):
            opp = OpportunitySummary(title="t", risk=value)
            self.assertEqual(value, opp.risk)

    def test_risk_rejects_unknown_literal(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary(title="t", risk="critical")  # type: ignore[arg-type]

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunitySummary()  # type: ignore[call-arg]


class SelectedTaskTest(unittest.TestCase):
    def test_required_field_only(self) -> None:
        task = SelectedTask(title="Add unit tests")
        self.assertEqual("Add unit tests", task.title)

    def test_default_values(self) -> None:
        task = SelectedTask(title="Add unit tests")
        self.assertEqual("", task.rationale)
        self.assertEqual("", task.expected_change)
        self.assertEqual("low", task.risk)

    def test_full_construction(self) -> None:
        task = SelectedTask(
            title="Add unit tests",
            rationale="Coverage gap",
            expected_change="new test file",
            risk="high",
        )
        self.assertEqual("Add unit tests", task.title)
        self.assertEqual("Coverage gap", task.rationale)
        self.assertEqual("new test file", task.expected_change)
        self.assertEqual("high", task.risk)

    def test_risk_rejects_unknown_literal(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask(title="t", risk="extreme")  # type: ignore[arg-type]

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SelectedTask()  # type: ignore[call-arg]


class WorkspaceSummaryTest(unittest.TestCase):
    def test_default_values(self) -> None:
        summary = WorkspaceSummary()
        self.assertEqual([], summary.commands_run)
        self.assertFalse(summary.patch_applied)
        self.assertEqual("", summary.notes)

    def test_default_commands_run_is_independent(self) -> None:
        a = WorkspaceSummary()
        b = WorkspaceSummary()
        a.commands_run.append(
            CommandResult(
                command="ls",
                stdout="",
                stderr="",
                exit_code=0,
                duration_seconds=0.01,
            )
        )
        self.assertEqual([], b.commands_run)

    def test_full_construction(self) -> None:
        cmd = CommandResult(
            command="pytest -q",
            stdout="1 passed",
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )
        summary = WorkspaceSummary(
            commands_run=[cmd],
            patch_applied=True,
            notes="verified",
        )
        self.assertEqual(1, len(summary.commands_run))
        self.assertEqual("pytest -q", summary.commands_run[0].command)
        self.assertTrue(summary.patch_applied)
        self.assertEqual("verified", summary.notes)


class AgentFinalResultTest(unittest.TestCase):
    def _repo(self) -> RepoSummary:
        return RepoSummary(
            owner="example",
            name="repo",
            url="https://github.com/example/repo",
        )

    def _task(self) -> SelectedTask:
        return SelectedTask(title="Add tests")

    def test_minimal_required_fields(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=self._repo(),
            repo_profile="profile",
            opportunities=[],
            selected_task=self._task(),
        )
        self.assertEqual("completed", result.status)
        self.assertEqual("profile", result.repo_profile)
        self.assertEqual([], result.opportunities)

    def test_default_values(self) -> None:
        result = AgentFinalResult(
            status="blocked",
            repo=self._repo(),
            repo_profile="",
            opportunities=[],
            selected_task=self._task(),
        )
        self.assertIsInstance(result.workspace_summary, WorkspaceSummary)
        self.assertFalse(result.workspace_summary.patch_applied)
        self.assertEqual([], result.blockers)
        self.assertEqual("", result.problem_statement_summary)
        self.assertEqual("", result.reproduction_notes)
        self.assertEqual("", result.verification_summary)

    def test_default_lists_are_independent(self) -> None:
        a = AgentFinalResult(
            status="failed",
            repo=self._repo(),
            repo_profile="",
            opportunities=[],
            selected_task=self._task(),
        )
        b = AgentFinalResult(
            status="failed",
            repo=self._repo(),
            repo_profile="",
            opportunities=[],
            selected_task=self._task(),
        )
        a.blockers.append("boom")
        self.assertEqual([], b.blockers)

    def test_status_accepts_known_literals(self) -> None:
        for status in ("completed", "blocked", "failed"):
            result = AgentFinalResult(
                status=status,
                repo=self._repo(),
                repo_profile="",
                opportunities=[],
                selected_task=self._task(),
            )
            self.assertEqual(status, result.status)

    def test_status_rejects_unknown_literal(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(
                status="in_progress",  # type: ignore[arg-type]
                repo=self._repo(),
                repo_profile="",
                opportunities=[],
                selected_task=self._task(),
            )

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinalResult(  # type: ignore[call-arg]
                status="completed",
                repo=self._repo(),
                repo_profile="",
                opportunities=[],
            )

    def test_full_construction(self) -> None:
        opportunity = OpportunitySummary(
            title="Add tests",
            rationale="coverage",
            risk="low",
            source="audit",
        )
        workspace = WorkspaceSummary(patch_applied=True, notes="ok")
        result = AgentFinalResult(
            status="completed",
            repo=self._repo(),
            repo_profile="python harness",
            opportunities=[opportunity],
            selected_task=self._task(),
            workspace_summary=workspace,
            blockers=["none"],
            problem_statement_summary="problem",
            reproduction_notes="steps",
            verification_summary="pytest 1 passed",
        )
        self.assertEqual(1, len(result.opportunities))
        self.assertEqual("Add tests", result.opportunities[0].title)
        self.assertTrue(result.workspace_summary.patch_applied)
        self.assertEqual(["none"], result.blockers)
        self.assertEqual("problem", result.problem_statement_summary)
        self.assertEqual("steps", result.reproduction_notes)
        self.assertEqual("pytest 1 passed", result.verification_summary)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
