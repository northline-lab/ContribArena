from __future__ import annotations

import typing
import unittest

from contribarena.models import (
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
from contribarena.models.tool_results import CommandType


class CommandTypeLiteralTest(unittest.TestCase):
    def test_all_command_type_literals_accepted(self) -> None:
        expected = ("verification", "setup", "discovery", "other")
        args = typing.get_args(CommandType)
        self.assertEqual(args, expected)

    def test_invalid_command_type_rejected(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            CommandResult(command="ls", exit_code=0, duration_seconds=1.0, command_type="invalid")


class CommandResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = CommandResult(command="ls", exit_code=0, duration_seconds=1.5)
        self.assertEqual(result.command, "ls")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.duration_seconds, 1.5)

    def test_defaults(self) -> None:
        result = CommandResult(command="ls", exit_code=0, duration_seconds=1.0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.timed_out, False)
        self.assertEqual(result.command_type, "other")

    def test_explicit_values(self) -> None:
        result = CommandResult(
            command="pytest",
            stdout="3 passed",
            stderr="",
            exit_code=1,
            duration_seconds=5.3,
            timed_out=True,
            command_type="verification",
        )
        self.assertEqual(result.command, "pytest")
        self.assertEqual(result.stdout, "3 passed")
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.command_type, "verification")

    def test_all_command_type_literals(self) -> None:
        for ct in ("verification", "setup", "discovery", "other"):
            result = CommandResult(command="cmd", exit_code=0, duration_seconds=0.0, command_type=ct)
            self.assertEqual(result.command_type, ct)


class PatchResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = PatchResult(success=True)
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = PatchResult(success=True)
        self.assertEqual(result.files_modified, [])
        self.assertIsNone(result.error)

    def test_explicit_values(self) -> None:
        result = PatchResult(success=False, files_modified=["a.py", "b.py"], error="conflict")
        self.assertFalse(result.success)
        self.assertEqual(result.files_modified, ["a.py", "b.py"])
        self.assertEqual(result.error, "conflict")

    def test_files_modified_factory_independence(self) -> None:
        r1 = PatchResult(success=True)
        r2 = PatchResult(success=True)
        r1.files_modified.append("x.py")
        self.assertEqual(r2.files_modified, [])


class PatchOperationTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        op = PatchOperation(type="update_file", path="src/app.py")
        self.assertEqual(op.type, "update_file")
        self.assertEqual(op.path, "src/app.py")

    def test_defaults(self) -> None:
        op = PatchOperation(type="create_file", path="new.py")
        self.assertIsNone(op.content)
        self.assertIsNone(op.diff)
        self.assertIsNone(op.destination)

    def test_explicit_values(self) -> None:
        op = PatchOperation(
            type="move_file",
            path="old.py",
            content="new text",
            diff="*** Begin Patch\n*** End Patch",
            destination="new.py",
        )
        self.assertEqual(op.content, "new text")
        self.assertEqual(op.diff, "*** Begin Patch\n*** End Patch")
        self.assertEqual(op.destination, "new.py")


class AciResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = AciResult(tool="aci_view", success=True)
        self.assertEqual(result.tool, "aci_view")
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = AciResult(tool="aci_view", success=True)
        self.assertEqual(result.output, "")
        self.assertEqual(result.files_modified, [])
        self.assertIsNone(result.error)
        self.assertIsNone(result.error_kind)
        self.assertIsNone(result.recovery_kind)
        self.assertIsNone(result.terminal_status)
        self.assertEqual(result.review_notes, "")
        self.assertEqual(result.retry_count, 0)
        self.assertFalse(result.terminal_after_retries)

    def test_explicit_values(self) -> None:
        result = AciResult(
            tool="aci_apply_patch",
            success=False,
            output="patch_parse_error",
            files_modified=["repo/app.py"],
            error="context mismatch",
            error_kind="patch_parse_error",
            recovery_kind="format_error",
            terminal_status="goal_abandon_limit",
            review_notes="fix context",
            retry_count=3,
            terminal_after_retries=True,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "context mismatch")
        self.assertEqual(result.error_kind, "patch_parse_error")
        self.assertEqual(result.terminal_status, "goal_abandon_limit")
        self.assertTrue(result.terminal_after_retries)

    def test_files_modified_factory_independence(self) -> None:
        r1 = AciResult(tool="aci_view", success=True)
        r2 = AciResult(tool="aci_view", success=True)
        r1.files_modified.append("x.py")
        self.assertEqual(r2.files_modified, [])


class AgentStepTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        step = AgentStep(step=1, phase="scout", tool="repo_search", state="active", duration_seconds=2.5)
        self.assertEqual(step.step, 1)
        self.assertEqual(step.phase, "scout")
        self.assertEqual(step.tool, "repo_search")
        self.assertEqual(step.state, "active")
        self.assertEqual(step.duration_seconds, 2.5)

    def test_defaults(self) -> None:
        step = AgentStep(step=1, phase="work", tool="aci_view", state="active", duration_seconds=1.0)
        self.assertEqual(step.agent, "builtin")
        self.assertEqual(step.input_summary, "")
        self.assertEqual(step.result_summary, "")
        self.assertIsNone(step.error)
        self.assertTrue(step.accepted)
        self.assertIsNone(step.recovery_kind)
        self.assertIsNone(step.terminal_status)
        self.assertEqual(step.retry_count, 0)
        self.assertFalse(step.terminal_after_retries)

    def test_explicit_values(self) -> None:
        step = AgentStep(
            step=5,
            agent="custom",
            phase="review",
            tool="aci_apply_patch",
            input_summary="edit file",
            result_summary="success",
            state="blocked",
            duration_seconds=10.5,
            error="timeout",
            accepted=False,
            recovery_kind="command_timeout",
            terminal_status="goal_abandon_limit",
            retry_count=2,
            terminal_after_retries=False,
        )
        self.assertEqual(step.agent, "custom")
        self.assertFalse(step.accepted)
        self.assertEqual(step.error, "timeout")
        self.assertEqual(step.recovery_kind, "command_timeout")


class RepoMetadataTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        meta = RepoMetadata(owner="octocat", repo="Hello-World", full_name="octocat/Hello-World", url="https://github.com/octocat/Hello-World")
        self.assertEqual(meta.owner, "octocat")
        self.assertEqual(meta.repo, "Hello-World")

    def test_defaults(self) -> None:
        meta = RepoMetadata(owner="o", repo="r", full_name="o/r", url="https://github.com/o/r")
        self.assertEqual(meta.description, "")
        self.assertEqual(meta.stars, 0)
        self.assertEqual(meta.forks, 0)
        self.assertEqual(meta.language, "")
        self.assertIsNone(meta.last_push)
        self.assertIsNone(meta.created_at)
        self.assertEqual(meta.open_issues, 0)
        self.assertEqual(meta.default_branch, "main")
        self.assertFalse(meta.fallback)

    def test_explicit_values(self) -> None:
        meta = RepoMetadata(
            owner="qWaitCrypto",
            repo="ContribArena",
            full_name="qWaitCrypto/ContribArena",
            url="https://github.com/qWaitCrypto/ContribArena",
            description="Arena for AI agents",
            stars=100,
            forks=50,
            language="Python",
            last_push="2026-05-29",
            created_at="2026-05-09",
            open_issues=72,
            default_branch="develop",
            fallback=True,
        )
        self.assertEqual(meta.stars, 100)
        self.assertEqual(meta.forks, 50)
        self.assertEqual(meta.language, "Python")
        self.assertEqual(meta.open_issues, 72)
        self.assertEqual(meta.default_branch, "develop")
        self.assertTrue(meta.fallback)


class IssueCandidateTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        issue = IssueCandidate(number=42, title="Fix bug")
        self.assertEqual(issue.number, 42)
        self.assertEqual(issue.title, "Fix bug")

    def test_defaults(self) -> None:
        issue = IssueCandidate(number=1, title="Bug")
        self.assertEqual(issue.url, "")
        self.assertEqual(issue.body, "")
        self.assertEqual(issue.labels, [])
        self.assertIsNone(issue.created_at)
        self.assertIsNone(issue.updated_at)

    def test_explicit_values(self) -> None:
        issue = IssueCandidate(
            number=99,
            title="Add feature",
            url="https://github.com/o/r/issues/99",
            body="Please add this",
            labels=["enhancement", "good-first-issue"],
            created_at="2026-01-01",
            updated_at="2026-02-01",
        )
        self.assertEqual(issue.url, "https://github.com/o/r/issues/99")
        self.assertEqual(issue.labels, ["enhancement", "good-first-issue"])

    def test_labels_factory_independence(self) -> None:
        i1 = IssueCandidate(number=1, title="A")
        i2 = IssueCandidate(number=2, title="B")
        i1.labels.append("bug")
        self.assertEqual(i2.labels, [])


class PullRequestCandidateTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        pr = PullRequestCandidate(number=10, title="Fix typo")
        self.assertEqual(pr.number, 10)
        self.assertEqual(pr.title, "Fix typo")

    def test_defaults(self) -> None:
        pr = PullRequestCandidate(number=1, title="PR")
        self.assertEqual(pr.url, "")
        self.assertEqual(pr.state, "")
        self.assertEqual(pr.author, "")
        self.assertEqual(pr.body, "")
        self.assertEqual(pr.labels, [])
        self.assertIsNone(pr.created_at)
        self.assertIsNone(pr.updated_at)
        self.assertIsNone(pr.merged_at)
        self.assertFalse(pr.draft)
        self.assertEqual(pr.linked_issues, [])

    def test_explicit_values(self) -> None:
        pr = PullRequestCandidate(
            number=50,
            title="Add tests",
            url="https://github.com/o/r/pull/50",
            state="open",
            author="dev",
            body="Test body",
            labels=["test"],
            created_at="2026-01-01",
            updated_at="2026-02-01",
            merged_at="2026-03-01",
            draft=True,
            linked_issues=[42, 43],
        )
        self.assertEqual(pr.state, "open")
        self.assertEqual(pr.author, "dev")
        self.assertTrue(pr.draft)
        self.assertEqual(pr.linked_issues, [42, 43])

    def test_labels_factory_independence(self) -> None:
        p1 = PullRequestCandidate(number=1, title="A")
        p2 = PullRequestCandidate(number=2, title="B")
        p1.labels.append("bug")
        self.assertEqual(p2.labels, [])

    def test_linked_issues_factory_independence(self) -> None:
        p1 = PullRequestCandidate(number=1, title="A")
        p2 = PullRequestCandidate(number=2, title="B")
        p1.linked_issues.append(1)
        self.assertEqual(p2.linked_issues, [])


class IssueLinkageTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        linkage = IssueLinkage(issue_number=42)
        self.assertEqual(linkage.issue_number, 42)

    def test_defaults(self) -> None:
        linkage = IssueLinkage(issue_number=1)
        self.assertEqual(linkage.assignees, [])
        self.assertEqual(linkage.linked_prs, [])
        self.assertEqual(linkage.recent_comments, [])

    def test_explicit_values_with_nested_prs(self) -> None:
        pr = PullRequestCandidate(number=10, title="Fix", state="open")
        linkage = IssueLinkage(
            issue_number=42,
            assignees=["dev1", "dev2"],
            linked_prs=[pr],
            recent_comments=["Please fix", "Working on it"],
        )
        self.assertEqual(linkage.assignees, ["dev1", "dev2"])
        self.assertEqual(len(linkage.linked_prs), 1)
        self.assertEqual(linkage.linked_prs[0].number, 10)
        self.assertEqual(linkage.recent_comments, ["Please fix", "Working on it"])

    def test_assignees_factory_independence(self) -> None:
        l1 = IssueLinkage(issue_number=1)
        l2 = IssueLinkage(issue_number=2)
        l1.assignees.append("dev")
        self.assertEqual(l2.assignees, [])

    def test_linked_prs_factory_independence(self) -> None:
        l1 = IssueLinkage(issue_number=1)
        l2 = IssueLinkage(issue_number=2)
        l1.linked_prs.append(PullRequestCandidate(number=1, title="A"))
        self.assertEqual(l2.linked_prs, [])

    def test_recent_comments_factory_independence(self) -> None:
        l1 = IssueLinkage(issue_number=1)
        l2 = IssueLinkage(issue_number=2)
        l1.recent_comments.append("comment")
        self.assertEqual(l2.recent_comments, [])


class RepoSetupProbeResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = RepoSetupProbeResult(full_name="o/r", success=True)
        self.assertEqual(result.full_name, "o/r")
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = RepoSetupProbeResult(full_name="o/r", success=True)
        self.assertFalse(result.probe_failed)
        self.assertEqual(result.default_branch, "main")
        self.assertEqual(result.package_managers, [])
        self.assertEqual(result.test_commands, [])
        self.assertEqual(result.ci_files, [])
        self.assertEqual(result.setup_difficulty, "unknown")
        self.assertEqual(result.duration_seconds, 0.0)
        self.assertEqual(result.error, "")

    def test_explicit_values(self) -> None:
        result = RepoSetupProbeResult(
            full_name="qWaitCrypto/ContribArena",
            success=True,
            probe_failed=False,
            default_branch="develop",
            package_managers=["uv"],
            test_commands=["pytest -q tests/unit"],
            ci_files=[".github/workflows/ci.yml"],
            setup_difficulty="easy",
            duration_seconds=30.5,
            error="",
        )
        self.assertEqual(result.default_branch, "develop")
        self.assertEqual(result.package_managers, ["uv"])
        self.assertEqual(result.test_commands, ["pytest -q tests/unit"])
        self.assertEqual(result.setup_difficulty, "easy")
        self.assertEqual(result.duration_seconds, 30.5)

    def test_package_managers_factory_independence(self) -> None:
        r1 = RepoSetupProbeResult(full_name="a/b", success=True)
        r2 = RepoSetupProbeResult(full_name="c/d", success=True)
        r1.package_managers.append("pip")
        self.assertEqual(r2.package_managers, [])

    def test_test_commands_factory_independence(self) -> None:
        r1 = RepoSetupProbeResult(full_name="a/b", success=True)
        r2 = RepoSetupProbeResult(full_name="c/d", success=True)
        r1.test_commands.append("pytest")
        self.assertEqual(r2.test_commands, [])

    def test_ci_files_factory_independence(self) -> None:
        r1 = RepoSetupProbeResult(full_name="a/b", success=True)
        r2 = RepoSetupProbeResult(full_name="c/d", success=True)
        r1.ci_files.append("ci.yml")
        self.assertEqual(r2.ci_files, [])


class RepoReadmeResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = RepoReadmeResult(full_name="o/r", success=True)
        self.assertEqual(result.full_name, "o/r")
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = RepoReadmeResult(full_name="o/r", success=True)
        self.assertEqual(result.path, "README")
        self.assertEqual(result.content, "")
        self.assertEqual(result.error, "")

    def test_explicit_values(self) -> None:
        result = RepoReadmeResult(
            full_name="qWaitCrypto/ContribArena",
            success=True,
            path="README.md",
            content="# ContribArena\nArena for AI agents",
            error="",
        )
        self.assertEqual(result.path, "README.md")
        self.assertEqual(result.content, "# ContribArena\nArena for AI agents")

    def test_unsuccessful_result_with_error(self) -> None:
        result = RepoReadmeResult(
            full_name="o/r",
            success=False,
            error="404 Not Found",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "404 Not Found")


class EligibilityResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        result = EligibilityResult(eligible=True)
        self.assertTrue(result.eligible)

    def test_defaults(self) -> None:
        result = EligibilityResult(eligible=True)
        self.assertEqual(result.reasons, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.checks_performed, [])

    def test_explicit_values(self) -> None:
        result = EligibilityResult(
            eligible=True,
            reasons=["eligible for M0.1 shadow-mode inspection"],
            warnings=["large repo size"],
            checks_performed=["activity", "contribution_acceptance", "code_size"],
        )
        self.assertEqual(result.reasons, ["eligible for M0.1 shadow-mode inspection"])
        self.assertEqual(result.warnings, ["large repo size"])
        self.assertEqual(result.checks_performed, ["activity", "contribution_acceptance", "code_size"])

    def test_reasons_factory_independence(self) -> None:
        r1 = EligibilityResult(eligible=True)
        r2 = EligibilityResult(eligible=True)
        r1.reasons.append("reason")
        self.assertEqual(r2.reasons, [])

    def test_warnings_factory_independence(self) -> None:
        r1 = EligibilityResult(eligible=True)
        r2 = EligibilityResult(eligible=True)
        r1.warnings.append("warning")
        self.assertEqual(r2.warnings, [])

    def test_checks_performed_factory_independence(self) -> None:
        r1 = EligibilityResult(eligible=True)
        r2 = EligibilityResult(eligible=True)
        r1.checks_performed.append("check")
        self.assertEqual(r2.checks_performed, [])


class ToolResultsImportTest(unittest.TestCase):
    def test_all_symbols_importable_from_models_package(self) -> None:
        from contribarena.models import (
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
        symbols = [
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
        ]
        self.assertEqual(len(symbols), 12)
        for sym in symbols:
            self.assertTrue(hasattr(sym, "__name__"))
