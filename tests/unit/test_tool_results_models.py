from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.models.tool_results import (
    AciResult,
    AgentStep,
    CommandType,
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


class CommandTypeLiteralTest(unittest.TestCase):
    """The CommandType Literal alias accepts only its declared values."""

    def test_all_literal_values_accepted(self) -> None:
        for value in get_args(CommandType):
            result = CommandResult(
                command="pytest",
                exit_code=0,
                duration_seconds=1.0,
                command_type=value,
            )
            self.assertEqual(value, result.command_type)

    def test_invalid_command_type_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(
                command="pytest",
                exit_code=0,
                duration_seconds=1.0,
                command_type="invalid",
            )

    def test_expected_literal_values(self) -> None:
        self.assertEqual(
            ("verification", "setup", "discovery", "other"),
            get_args(CommandType),
        )


class CommandResultTest(unittest.TestCase):
    """CommandResult holds required fields and defaults."""

    def test_required_fields(self) -> None:
        result = CommandResult(
            command="pytest -q",
            exit_code=0,
            duration_seconds=1.23,
        )
        self.assertEqual("pytest -q", result.command)
        self.assertEqual(0, result.exit_code)
        self.assertAlmostEqual(1.23, result.duration_seconds)

    def test_defaults(self) -> None:
        result = CommandResult(
            command="pytest -q",
            exit_code=0,
            duration_seconds=1.0,
        )
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertFalse(result.timed_out)
        self.assertEqual("other", result.command_type)

    def test_explicit_values(self) -> None:
        result = CommandResult(
            command="pytest -q",
            stdout="passed",
            stderr="warnings",
            exit_code=1,
            duration_seconds=5.0,
            timed_out=True,
            command_type="verification",
        )
        self.assertEqual("passed", result.stdout)
        self.assertEqual("warnings", result.stderr)
        self.assertTrue(result.timed_out)
        self.assertEqual("verification", result.command_type)

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            CommandResult(exit_code=0, duration_seconds=1.0)  # type: ignore[call-arg]


class PatchResultTest(unittest.TestCase):
    """PatchResult holds success, modified files, and optional error."""

    def test_required_fields(self) -> None:
        result = PatchResult(success=True)
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = PatchResult(success=True)
        self.assertEqual([], result.files_modified)
        self.assertIsNone(result.error)

    def test_explicit_values(self) -> None:
        result = PatchResult(
            success=False,
            files_modified=["app.py", "test_app.py"],
            error="context mismatch",
        )
        self.assertFalse(result.success)
        self.assertEqual(["app.py", "test_app.py"], result.files_modified)
        self.assertEqual("context mismatch", result.error)

    def test_missing_success_raises(self) -> None:
        with self.assertRaises(ValidationError):
            PatchResult()  # type: ignore[call-arg]


class PatchOperationTest(unittest.TestCase):
    """PatchOperation describes a single edit operation."""

    def test_required_fields(self) -> None:
        op = PatchOperation(type="update_file", path="repo/app.py")
        self.assertEqual("update_file", op.type)
        self.assertEqual("repo/app.py", op.path)

    def test_optional_fields_default_none(self) -> None:
        op = PatchOperation(type="delete_file", path="repo/old.py")
        self.assertIsNone(op.content)
        self.assertIsNone(op.diff)
        self.assertIsNone(op.destination)

    def test_explicit_optional_fields(self) -> None:
        op = PatchOperation(
            type="move_file",
            path="repo/a.py",
            destination="repo/b.py",
            content="new text",
            diff="patch text",
        )
        self.assertEqual("new text", op.content)
        self.assertEqual("patch text", op.diff)
        self.assertEqual("repo/b.py", op.destination)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            PatchOperation(type="update_file")  # type: ignore[call-arg]


class AciResultTest(unittest.TestCase):
    """AciResult records ACI tool outcomes."""

    def test_required_fields(self) -> None:
        result = AciResult(tool="aci_verify", success=True)
        self.assertEqual("aci_verify", result.tool)
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = AciResult(tool="aci_verify", success=True)
        self.assertEqual("", result.output)
        self.assertEqual([], result.files_modified)
        self.assertIsNone(result.error)
        self.assertIsNone(result.error_kind)
        self.assertIsNone(result.recovery_kind)
        self.assertIsNone(result.terminal_status)
        self.assertEqual("", result.review_notes)
        self.assertEqual(0, result.retry_count)
        self.assertFalse(result.terminal_after_retries)

    def test_explicit_values(self) -> None:
        result = AciResult(
            tool="aci_verify",
            success=False,
            output="patch_parse_error",
            files_modified=["app.py"],
            error="context mismatch",
            error_kind="patch_failure",
            recovery_kind="format_error",
            terminal_status="goal_abandon_limit",
            review_notes="Patch rejected",
            retry_count=2,
            terminal_after_retries=True,
        )
        self.assertFalse(result.success)
        self.assertEqual("patch_parse_error", result.output)
        self.assertEqual(["app.py"], result.files_modified)
        self.assertEqual("context mismatch", result.error)
        self.assertEqual("patch_failure", result.error_kind)
        self.assertEqual("format_error", result.recovery_kind)
        self.assertEqual("goal_abandon_limit", result.terminal_status)
        self.assertEqual("Patch rejected", result.review_notes)
        self.assertEqual(2, result.retry_count)
        self.assertTrue(result.terminal_after_retries)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AciResult(tool="aci_verify")  # type: ignore[call-arg]

    def test_files_modified_factory_independence(self) -> None:
        a = AciResult(tool="t", success=True)
        b = AciResult(tool="t", success=True)
        a.files_modified.append("file.py")
        self.assertEqual([], b.files_modified)


class AgentStepTest(unittest.TestCase):
    """AgentStep records one agent loop iteration."""

    def test_required_fields(self) -> None:
        step = AgentStep(step=1, phase="work", tool="aci_verify", state="completed", duration_seconds=2.5)
        self.assertEqual(1, step.step)
        self.assertEqual("work", step.phase)
        self.assertEqual("aci_verify", step.tool)
        self.assertEqual("completed", step.state)
        self.assertAlmostEqual(2.5, step.duration_seconds)

    def test_defaults(self) -> None:
        step = AgentStep(step=1, phase="work", tool="aci_verify", state="completed", duration_seconds=1.0)
        self.assertEqual("builtin", step.agent)
        self.assertEqual("", step.input_summary)
        self.assertEqual("", step.result_summary)
        self.assertIsNone(step.error)
        self.assertTrue(step.accepted)
        self.assertIsNone(step.recovery_kind)
        self.assertIsNone(step.terminal_status)
        self.assertEqual(0, step.retry_count)
        self.assertFalse(step.terminal_after_retries)

    def test_explicit_values(self) -> None:
        step = AgentStep(
            step=5,
            agent="custom",
            phase="review",
            tool="aci_undo",
            input_summary="undo last edit",
            result_summary="edit reverted",
            state="failed",
            duration_seconds=0.1,
            error="undo failed",
            accepted=False,
            recovery_kind="patch_failure",
            terminal_status="goal_abandon_limit",
            retry_count=1,
            terminal_after_retries=False,
        )
        self.assertEqual("custom", step.agent)
        self.assertFalse(step.accepted)
        self.assertEqual("undo failed", step.error)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            AgentStep(step=1)  # type: ignore[call-arg]


class RepoMetadataTest(unittest.TestCase):
    """RepoMetadata holds repository identity and statistics."""

    def test_required_fields(self) -> None:
        meta = RepoMetadata(
            owner="example",
            repo="project",
            full_name="example/project",
            url="https://github.com/example/project",
        )
        self.assertEqual("example", meta.owner)
        self.assertEqual("project", meta.repo)
        self.assertEqual("example/project", meta.full_name)
        self.assertEqual("https://github.com/example/project", meta.url)

    def test_defaults(self) -> None:
        meta = RepoMetadata(
            owner="example",
            repo="project",
            full_name="example/project",
            url="https://github.com/example/project",
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

    def test_explicit_values(self) -> None:
        meta = RepoMetadata(
            owner="qWaitCrypto",
            repo="ContribArena",
            full_name="qWaitCrypto/ContribArena",
            url="https://github.com/qWaitCrypto/ContribArena",
            description="The real-world arena",
            stars=5,
            forks=2,
            language="Python",
            last_push="2026-05-29T03:57:01Z",
            created_at="2026-05-09T03:53:20Z",
            open_issues=38,
            default_branch="main",
            fallback=False,
        )
        self.assertEqual("Python", meta.language)
        self.assertEqual(38, meta.open_issues)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RepoMetadata(owner="example")  # type: ignore[call-arg]

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import RepoMetadata as PackageRepoMetadata

        self.assertIs(RepoMetadata, PackageRepoMetadata)


class IssueCandidateTest(unittest.TestCase):
    """IssueCandidate holds issue metadata from repository mining."""

    def test_required_fields(self) -> None:
        issue = IssueCandidate(number=42, title="Fix typo")
        self.assertEqual(42, issue.number)
        self.assertEqual("Fix typo", issue.title)

    def test_defaults(self) -> None:
        issue = IssueCandidate(number=1, title="Test")
        self.assertEqual("", issue.url)
        self.assertEqual("", issue.body)
        self.assertEqual([], issue.labels)
        self.assertIsNone(issue.created_at)
        self.assertIsNone(issue.updated_at)

    def test_explicit_values(self) -> None:
        issue = IssueCandidate(
            number=7,
            title="Question about early contributor discovery",
            url="https://github.com/example/repo/issues/7",
            body="Hi there",
            labels=["question"],
            created_at="2026-05-21T07:54:55Z",
            updated_at="2026-05-27T09:47:58Z",
        )
        self.assertEqual("question", issue.labels[0])

    def test_labels_factory_independence(self) -> None:
        a = IssueCandidate(number=1, title="A")
        b = IssueCandidate(number=2, title="B")
        a.labels.append("bug")
        self.assertEqual([], b.labels)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import IssueCandidate as PackageIssueCandidate

        self.assertIs(IssueCandidate, PackageIssueCandidate)


class PullRequestCandidateTest(unittest.TestCase):
    """PullRequestCandidate holds PR metadata for duplicate checking."""

    def test_required_fields(self) -> None:
        pr = PullRequestCandidate(number=81, title="Fix exports")
        self.assertEqual(81, pr.number)
        self.assertEqual("Fix exports", pr.title)

    def test_defaults(self) -> None:
        pr = PullRequestCandidate(number=1, title="Test")
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

    def test_explicit_values(self) -> None:
        pr = PullRequestCandidate(
            number=81,
            title="Fix exports",
            url="https://github.com/example/repo/pull/81",
            state="open",
            author="northline-lab",
            body="## Summary",
            labels=["fix"],
            created_at="2026-05-29T07:40:43Z",
            updated_at="2026-05-29T07:40:43Z",
            merged_at=None,
            draft=False,
            linked_issues=[7],
        )
        self.assertEqual("open", pr.state)
        self.assertEqual([7], pr.linked_issues)

    def test_labels_factory_independence(self) -> None:
        a = PullRequestCandidate(number=1, title="A")
        b = PullRequestCandidate(number=2, title="B")
        a.labels.append("bug")
        self.assertEqual([], b.labels)

    def test_linked_issues_factory_independence(self) -> None:
        a = PullRequestCandidate(number=1, title="A")
        b = PullRequestCandidate(number=2, title="B")
        a.linked_issues.append(42)
        self.assertEqual([], b.linked_issues)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import PullRequestCandidate as PackagePR

        self.assertIs(PullRequestCandidate, PackagePR)


class IssueLinkageTest(unittest.TestCase):
    """IssueLinkage aggregates assignees, linked PRs, and comments."""

    def test_required_fields(self) -> None:
        linkage = IssueLinkage(issue_number=7)
        self.assertEqual(7, linkage.issue_number)

    def test_defaults(self) -> None:
        linkage = IssueLinkage(issue_number=7)
        self.assertEqual([], linkage.assignees)
        self.assertEqual([], linkage.linked_prs)
        self.assertEqual([], linkage.recent_comments)

    def test_explicit_values_with_nested_prs(self) -> None:
        linkage = IssueLinkage(
            issue_number=7,
            assignees=["maintainer1"],
            linked_prs=[
                PullRequestCandidate(
                    number=81,
                    title="Fix exports",
                    state="open",
                ),
            ],
            recent_comments=["Looks good"],
        )
        self.assertEqual("maintainer1", linkage.assignees[0])
        self.assertEqual(81, linkage.linked_prs[0].number)
        self.assertEqual("Looks good", linkage.recent_comments[0])

    def test_assignees_factory_independence(self) -> None:
        a = IssueLinkage(issue_number=1)
        b = IssueLinkage(issue_number=2)
        a.assignees.append("user")
        self.assertEqual([], b.assignees)

    def test_linked_prs_factory_independence(self) -> None:
        a = IssueLinkage(issue_number=1)
        b = IssueLinkage(issue_number=2)
        a.linked_prs.append(PullRequestCandidate(number=1, title="PR"))
        self.assertEqual([], b.linked_prs)

    def test_recent_comments_factory_independence(self) -> None:
        a = IssueLinkage(issue_number=1)
        b = IssueLinkage(issue_number=2)
        a.recent_comments.append("comment")
        self.assertEqual([], b.recent_comments)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import IssueLinkage as PackageIssueLinkage

        self.assertIs(IssueLinkage, PackageIssueLinkage)


class RepoSetupProbeResultTest(unittest.TestCase):
    """RepoSetupProbeResult records repository setup probing results."""

    def test_required_fields(self) -> None:
        result = RepoSetupProbeResult(full_name="example/repo", success=True)
        self.assertEqual("example/repo", result.full_name)
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = RepoSetupProbeResult(full_name="example/repo", success=True)
        self.assertFalse(result.probe_failed)
        self.assertEqual("main", result.default_branch)
        self.assertEqual([], result.package_managers)
        self.assertEqual([], result.test_commands)
        self.assertEqual([], result.ci_files)
        self.assertEqual("unknown", result.setup_difficulty)
        self.assertAlmostEqual(0.0, result.duration_seconds)
        self.assertEqual("", result.error)

    def test_explicit_values(self) -> None:
        result = RepoSetupProbeResult(
            full_name="example/repo",
            success=False,
            probe_failed=True,
            default_branch="develop",
            package_managers=["pip", "npm"],
            test_commands=["pytest", "npm test"],
            ci_files=[".github/workflows/ci.yml"],
            setup_difficulty="easy",
            duration_seconds=14.6,
            error="timeout",
        )
        self.assertTrue(result.probe_failed)
        self.assertEqual("develop", result.default_branch)
        self.assertEqual(["pip", "npm"], result.package_managers)

    def test_list_factory_independence(self) -> None:
        a = RepoSetupProbeResult(full_name="a/repo", success=True)
        b = RepoSetupProbeResult(full_name="b/repo", success=True)
        a.package_managers.append("pip")
        self.assertEqual([], b.package_managers)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import RepoSetupProbeResult as PackageProbe

        self.assertIs(RepoSetupProbeResult, PackageProbe)


class RepoReadmeResultTest(unittest.TestCase):
    """RepoReadmeResult records README fetch results."""

    def test_required_fields(self) -> None:
        result = RepoReadmeResult(full_name="example/repo", success=True)
        self.assertEqual("example/repo", result.full_name)
        self.assertTrue(result.success)

    def test_defaults(self) -> None:
        result = RepoReadmeResult(full_name="example/repo", success=True)
        self.assertEqual("README", result.path)
        self.assertEqual("", result.content)
        self.assertEqual("", result.error)

    def test_explicit_values(self) -> None:
        result = RepoReadmeResult(
            full_name="example/repo",
            success=False,
            path="README.md",
            content="",
            error="file not found",
        )
        self.assertEqual("README.md", result.path)
        self.assertEqual("file not found", result.error)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import RepoReadmeResult as PackageReadme

        self.assertIs(RepoReadmeResult, PackageReadme)


class EligibilityResultTest(unittest.TestCase):
    """EligibilityResult records repository eligibility checks."""

    def test_required_fields(self) -> None:
        result = EligibilityResult(eligible=True)
        self.assertTrue(result.eligible)

    def test_defaults(self) -> None:
        result = EligibilityResult(eligible=True)
        self.assertEqual([], result.reasons)
        self.assertEqual([], result.warnings)
        self.assertEqual([], result.checks_performed)

    def test_explicit_values(self) -> None:
        result = EligibilityResult(
            eligible=True,
            reasons=["eligible for M0.1 shadow-mode inspection"],
            warnings=[],
            checks_performed=["activity", "contribution_acceptance", "code_size"],
        )
        self.assertEqual(1, len(result.reasons))
        self.assertEqual(3, len(result.checks_performed))

    def test_reasons_factory_independence(self) -> None:
        a = EligibilityResult(eligible=True)
        b = EligibilityResult(eligible=True)
        a.reasons.append("test")
        self.assertEqual([], b.reasons)

    def test_warnings_factory_independence(self) -> None:
        a = EligibilityResult(eligible=True)
        b = EligibilityResult(eligible=True)
        a.warnings.append("test")
        self.assertEqual([], b.warnings)

    def test_checks_performed_factory_independence(self) -> None:
        a = EligibilityResult(eligible=True)
        b = EligibilityResult(eligible=True)
        a.checks_performed.append("activity")
        self.assertEqual([], b.checks_performed)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import EligibilityResult as PackageEligibility

        self.assertIs(EligibilityResult, PackageEligibility)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
