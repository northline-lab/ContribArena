from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.tool_results import (
    AciResult,
    AgentStep,
    CommandResult,
    EligibilityResult,
    IssueCandidate,
    IssueLinkage,
    PatchOperation,
    PatchResult,
    PullRequestCandidate,
    RepoMetadata,
    RepoReadmeResult,
    RepoSetupProbeResult,
)


class CommandResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = CommandResult(
            command="pytest -q",
            exit_code=0,
            duration_seconds=0.5,
        )
        self.assertEqual("pytest -q", result.command)
        self.assertEqual(0, result.exit_code)
        self.assertAlmostEqual(0.5, result.duration_seconds)

    def test_default_values(self) -> None:
        result = CommandResult(
            command="echo hello",
            exit_code=1,
            duration_seconds=2.0,
        )
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertFalse(result.timed_out)
        self.assertEqual("other", result.command_type)

    def test_command_type_literal_values(self) -> None:
        for ct in ("verification", "setup", "discovery", "other"):
            result = CommandResult(
                command="cmd",
                exit_code=0,
                duration_seconds=0.0,
                command_type=ct,
            )
            self.assertEqual(ct, result.command_type)

    def test_command_type_invalid_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(
                command="cmd",
                exit_code=0,
                duration_seconds=0.0,
                command_type="invalid",
            )

    def test_timed_out_explicit(self) -> None:
        result = CommandResult(
            command="long cmd",
            exit_code=137,
            duration_seconds=900.0,
            timed_out=True,
        )
        self.assertTrue(result.timed_out)

    def test_all_fields_explicit(self) -> None:
        result = CommandResult(
            command="ruff check .",
            stdout="All checks passed!",
            stderr="",
            exit_code=0,
            duration_seconds=1.23,
            timed_out=False,
            command_type="verification",
        )
        self.assertEqual("ruff check .", result.command)
        self.assertEqual("All checks passed!", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.exit_code)
        self.assertAlmostEqual(1.23, result.duration_seconds)
        self.assertFalse(result.timed_out)
        self.assertEqual("verification", result.command_type)

    def test_missing_command_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(exit_code=0, duration_seconds=0.0)

    def test_missing_exit_code_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(command="cmd", duration_seconds=0.0)

    def test_missing_duration_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(command="cmd", exit_code=0)


class PatchResultTest(unittest.TestCase):
    def test_success_with_files(self) -> None:
        result = PatchResult(
            success=True,
            files_modified=["repo/app.py", "repo/tests/test_app.py"],
        )
        self.assertTrue(result.success)
        self.assertEqual(["repo/app.py", "repo/tests/test_app.py"], result.files_modified)
        self.assertIsNone(result.error)

    def test_failure_with_error(self) -> None:
        result = PatchResult(
            success=False,
            error="context mismatch",
        )
        self.assertFalse(result.success)
        self.assertEqual([], result.files_modified)
        self.assertEqual("context mismatch", result.error)

    def test_default_values(self) -> None:
        result = PatchResult(success=True)
        self.assertTrue(result.success)
        self.assertEqual([], result.files_modified)
        self.assertIsNone(result.error)

    def test_missing_success_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PatchResult()


class PatchOperationTest(unittest.TestCase):
    def test_create_operation(self) -> None:
        op = PatchOperation(
            type="create_file",
            path="repo/new_file.py",
            content="# new file",
        )
        self.assertEqual("create_file", op.type)
        self.assertEqual("repo/new_file.py", op.path)
        self.assertEqual("# new file", op.content)
        self.assertIsNone(op.diff)
        self.assertIsNone(op.destination)

    def test_update_operation_with_diff(self) -> None:
        op = PatchOperation(
            type="update_file",
            path="repo/app.py",
            diff="*** Begin Patch *** End Patch",
        )
        self.assertEqual("update_file", op.type)
        self.assertIsNone(op.content)
        self.assertEqual("*** Begin Patch *** End Patch", op.diff)

    def test_move_operation(self) -> None:
        op = PatchOperation(
            type="move_file",
            path="repo/old.py",
            destination="repo/new.py",
        )
        self.assertEqual("move_file", op.type)
        self.assertEqual("repo/new.py", op.destination)

    def test_delete_operation(self) -> None:
        op = PatchOperation(
            type="delete_file",
            path="repo/old_file.py",
        )
        self.assertEqual("delete_file", op.type)
        self.assertIsNone(op.content)
        self.assertIsNone(op.diff)
        self.assertIsNone(op.destination)

    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            PatchOperation(path="repo/file.py")

    def test_required_path(self) -> None:
        with self.assertRaises(ValidationError):
            PatchOperation(type="update_file")

    def test_defaults_optional_fields(self) -> None:
        op = PatchOperation(type="create_file", path="repo/f.py")
        self.assertIsNone(op.content)
        self.assertIsNone(op.diff)
        self.assertIsNone(op.destination)


class AciResultTest(unittest.TestCase):
    def test_success_result(self) -> None:
        result = AciResult(tool="aci_view", success=True, output="file contents")
        self.assertEqual("aci_view", result.tool)
        self.assertTrue(result.success)
        self.assertEqual("file contents", result.output)
        self.assertEqual([], result.files_modified)
        self.assertIsNone(result.error)
        self.assertIsNone(result.error_kind)
        self.assertIsNone(result.recovery_kind)
        self.assertIsNone(result.terminal_status)
        self.assertEqual("", result.review_notes)
        self.assertEqual(0, result.retry_count)
        self.assertFalse(result.terminal_after_retries)

    def test_failure_result(self) -> None:
        result = AciResult(
            tool="aci_apply_patch",
            success=False,
            error="context mismatch",
            error_kind="patch_parse_error",
            recovery_kind="patch_failure",
        )
        self.assertFalse(result.success)
        self.assertEqual("context mismatch", result.error)
        self.assertEqual("patch_parse_error", result.error_kind)
        self.assertEqual("patch_failure", result.recovery_kind)

    def test_terminal_result(self) -> None:
        result = AciResult(
            tool="aci_goal_update",
            success=False,
            terminal_status="goal_abandon_limit",
            terminal_after_retries=True,
            retry_count=3,
        )
        self.assertEqual("goal_abandon_limit", result.terminal_status)
        self.assertTrue(result.terminal_after_retries)
        self.assertEqual(3, result.retry_count)

    def test_default_values(self) -> None:
        result = AciResult(tool="aci_search", success=True)
        self.assertEqual("", result.output)
        self.assertEqual([], result.files_modified)
        self.assertEqual("", result.review_notes)

    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AciResult(tool="aci_view")

    def test_required_tool(self) -> None:
        with self.assertRaises(ValidationError):
            AciResult(success=True)


class AgentStepTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        step = AgentStep(
            step=1,
            phase="scout",
            tool="repo_search",
            state="completed",
            duration_seconds=2.5,
        )
        self.assertEqual(1, step.step)
        self.assertEqual("scout", step.phase)
        self.assertEqual("repo_search", step.tool)
        self.assertEqual("completed", step.state)
        self.assertAlmostEqual(2.5, step.duration_seconds)

    def test_default_values(self) -> None:
        step = AgentStep(
            step=5,
            phase="work",
            tool="aci_apply_patch",
            state="failed",
            duration_seconds=0.1,
        )
        self.assertEqual("builtin", step.agent)
        self.assertEqual("", step.input_summary)
        self.assertEqual("", step.result_summary)
        self.assertIsNone(step.error)
        self.assertTrue(step.accepted)
        self.assertIsNone(step.recovery_kind)
        self.assertIsNone(step.terminal_status)
        self.assertEqual(0, step.retry_count)
        self.assertFalse(step.terminal_after_retries)

    def test_step_with_error_and_recovery(self) -> None:
        step = AgentStep(
            step=10,
            phase="work",
            tool="workspace_run",
            state="rejected",
            duration_seconds=30.0,
            error="command blocked",
            accepted=False,
            recovery_kind="blocked_command",
        )
        self.assertEqual("command blocked", step.error)
        self.assertFalse(step.accepted)
        self.assertEqual("blocked_command", step.recovery_kind)

    def test_missing_step_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AgentStep(
                phase="scout",
                tool="repo_search",
                state="completed",
                duration_seconds=1.0,
            )

    def test_missing_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AgentStep(
                step=1,
                tool="repo_search",
                state="completed",
                duration_seconds=1.0,
            )


class RepoMetadataTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        meta = RepoMetadata(
            owner="qWaitCrypto",
            repo="ContribArena",
            full_name="qWaitCrypto/ContribArena",
            url="https://github.com/qWaitCrypto/ContribArena",
        )
        self.assertEqual("qWaitCrypto", meta.owner)
        self.assertEqual("ContribArena", meta.repo)
        self.assertEqual("qWaitCrypto/ContribArena", meta.full_name)
        self.assertEqual("https://github.com/qWaitCrypto/ContribArena", meta.url)

    def test_default_values(self) -> None:
        meta = RepoMetadata(
            owner="owner",
            repo="repo",
            full_name="owner/repo",
            url="https://github.com/owner/repo",
        )
        self.assertEqual("", meta.description)
        self.assertEqual(0, meta.stars)
        self.assertEqual(0, meta.forks)
        self.assertEqual("", meta.language)
        self.assertIsNone(meta.last_push)
        self.assertIsNone(meta.created_at)
        self.assertEqual(0, meta.open_issues)
        self.assertEqual("main", meta.default_branch)
        self.assertFalse(meta.fallback)

    def test_full_metadata(self) -> None:
        meta = RepoMetadata(
            owner="example",
            repo="project",
            full_name="example/project",
            url="https://github.com/example/project",
            description="A test project",
            stars=42,
            forks=7,
            language="Python",
            last_push="2026-05-26T00:00:00Z",
            created_at="2026-01-01T00:00:00Z",
            open_issues=5,
            default_branch="develop",
            fallback=True,
        )
        self.assertEqual("A test project", meta.description)
        self.assertEqual(42, meta.stars)
        self.assertEqual(7, meta.forks)
        self.assertEqual("Python", meta.language)
        self.assertEqual("2026-05-26T00:00:00Z", meta.last_push)
        self.assertEqual("2026-01-01T00:00:00Z", meta.created_at)
        self.assertEqual(5, meta.open_issues)
        self.assertEqual("develop", meta.default_branch)
        self.assertTrue(meta.fallback)


class IssueCandidateTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        issue = IssueCandidate(number=7, title="Bug report")
        self.assertEqual(7, issue.number)
        self.assertEqual("Bug report", issue.title)

    def test_default_values(self) -> None:
        issue = IssueCandidate(number=1, title="Issue")
        self.assertEqual("", issue.url)
        self.assertEqual("", issue.body)
        self.assertEqual([], issue.labels)
        self.assertIsNone(issue.created_at)
        self.assertIsNone(issue.updated_at)

    def test_full_issue(self) -> None:
        issue = IssueCandidate(
            number=42,
            title="Feature request",
            url="https://github.com/o/r/issues/42",
            body="Please add feature X",
            labels=["enhancement", "good-first-issue"],
            created_at="2026-01-15T10:00:00Z",
            updated_at="2026-05-20T12:00:00Z",
        )
        self.assertEqual("https://github.com/o/r/issues/42", issue.url)
        self.assertEqual("Please add feature X", issue.body)
        self.assertEqual(["enhancement", "good-first-issue"], issue.labels)


class PullRequestCandidateTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        pr = PullRequestCandidate(number=48, title="Fix bug")
        self.assertEqual(48, pr.number)
        self.assertEqual("Fix bug", pr.title)

    def test_default_values(self) -> None:
        pr = PullRequestCandidate(number=1, title="PR")
        self.assertEqual("", pr.url)
        self.assertEqual("", pr.state)
        self.assertEqual("", pr.author)
        self.assertEqual("", pr.body)
        self.assertEqual([], pr.labels)
        self.assertIsNone(pr.created_at)
        self.assertIsNone(pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)
        self.assertEqual([], pr.linked_issues)

    def test_full_pr(self) -> None:
        pr = PullRequestCandidate(
            number=50,
            title="Add tests",
            url="https://github.com/o/r/pull/50",
            state="open",
            author="agent",
            body="Test-only change",
            labels=["test"],
            created_at="2026-05-25T08:00:00Z",
            updated_at="2026-05-26T08:00:00Z",
            merged_at=None,
            draft=False,
            linked_issues=[7, 12],
        )
        self.assertEqual("open", pr.state)
        self.assertEqual("agent", pr.author)
        self.assertEqual([7, 12], pr.linked_issues)
        self.assertFalse(pr.draft)


class IssueLinkageTest(unittest.TestCase):
    def test_default_values(self) -> None:
        linkage = IssueLinkage(issue_number=7)
        self.assertEqual(7, linkage.issue_number)
        self.assertEqual([], linkage.assignees)
        self.assertEqual([], linkage.linked_prs)
        self.assertEqual([], linkage.recent_comments)

    def test_with_linked_prs(self) -> None:
        pr = PullRequestCandidate(number=43, title="Fix issue", state="open")
        linkage = IssueLinkage(
            issue_number=7,
            assignees=["maintainer1"],
            linked_prs=[pr],
            recent_comments=["Please review"],
        )
        self.assertEqual(["maintainer1"], linkage.assignees)
        self.assertEqual(1, len(linkage.linked_prs))
        self.assertEqual(43, linkage.linked_prs[0].number)
        self.assertEqual(["Please review"], linkage.recent_comments)

    def test_required_issue_number(self) -> None:
        with self.assertRaises(ValidationError):
            IssueLinkage()


class RepoSetupProbeResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        probe = RepoSetupProbeResult(full_name="owner/repo", success=True)
        self.assertEqual("owner/repo", probe.full_name)
        self.assertTrue(probe.success)

    def test_default_values(self) -> None:
        probe = RepoSetupProbeResult(full_name="o/r", success=True)
        self.assertFalse(probe.probe_failed)
        self.assertEqual("main", probe.default_branch)
        self.assertEqual([], probe.package_managers)
        self.assertEqual([], probe.test_commands)
        self.assertEqual([], probe.ci_files)
        self.assertEqual("unknown", probe.setup_difficulty)
        self.assertAlmostEqual(0.0, probe.duration_seconds)
        self.assertEqual("", probe.error)

    def test_full_probe_result(self) -> None:
        probe = RepoSetupProbeResult(
            full_name="example/project",
            success=True,
            probe_failed=False,
            default_branch="main",
            package_managers=["uv", "pip"],
            test_commands=["pytest -q tests/unit"],
            ci_files=[".github/workflows/ci.yml"],
            setup_difficulty="easy",
            duration_seconds=5.2,
        )
        self.assertEqual(["uv", "pip"], probe.package_managers)
        self.assertEqual(["pytest -q tests/unit"], probe.test_commands)
        self.assertEqual("easy", probe.setup_difficulty)

    def test_failed_probe(self) -> None:
        probe = RepoSetupProbeResult(
            full_name="bad/repo",
            success=False,
            probe_failed=True,
            error="clone timed out",
        )
        self.assertFalse(probe.success)
        self.assertTrue(probe.probe_failed)
        self.assertEqual("clone timed out", probe.error)


class RepoReadmeResultTest(unittest.TestCase):
    def test_success_result(self) -> None:
        result = RepoReadmeResult(full_name="o/r", success=True, content="# Project")
        self.assertTrue(result.success)
        self.assertEqual("# Project", result.content)

    def test_default_values(self) -> None:
        result = RepoReadmeResult(full_name="o/r", success=True)
        self.assertEqual("README", result.path)
        self.assertEqual("", result.content)
        self.assertEqual("", result.error)

    def test_failed_result(self) -> None:
        result = RepoReadmeResult(
            full_name="o/r",
            success=False,
            error="README not found",
        )
        self.assertFalse(result.success)
        self.assertEqual("README not found", result.error)


class EligibilityResultTest(unittest.TestCase):
    def test_eligible(self) -> None:
        result = EligibilityResult(
            eligible=True,
            reasons=["eligible for M0.1 shadow-mode inspection"],
            checks_performed=["activity", "bot_policy"],
        )
        self.assertTrue(result.eligible)
        self.assertEqual(
            ["eligible for M0.1 shadow-mode inspection"], result.reasons
        )
        self.assertEqual([], result.warnings)
        self.assertEqual(["activity", "bot_policy"], result.checks_performed)

    def test_not_eligible(self) -> None:
        result = EligibilityResult(
            eligible=False,
            reasons=["no recent activity"],
            warnings=["low stars"],
            checks_performed=["activity"],
        )
        self.assertFalse(result.eligible)
        self.assertEqual(["no recent activity"], result.reasons)
        self.assertEqual(["low stars"], result.warnings)

    def test_default_values(self) -> None:
        result = EligibilityResult(eligible=True)
        self.assertEqual([], result.reasons)
        self.assertEqual([], result.warnings)
        self.assertEqual([], result.checks_performed)

    def test_required_eligible(self) -> None:
        with self.assertRaises(ValidationError):
            EligibilityResult()


if __name__ == "__main__":
    unittest.main()
