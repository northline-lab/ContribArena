from __future__ import annotations

import unittest

from contribarena.engine.lifecycle import (
    _branch_name,
    _changed_line_count,
    _has_patch_diff,
    _patch_paths,
    _postmortem_lesson,
    _suspicious_patch_paths,
    live_action_log_entries,
    render_postmortem,
    render_pr_description,
)
from contribarena.models import (
    AgentFinalResult,
    CiStatus,
    PullRequestDraft,
    QualityGateResult,
    RepoSummary,
    SelectedTask,
    TerminalState,
)


class HasPatchDiffTest(unittest.TestCase):
    """Test _has_patch_diff helper for detecting git diffs."""

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_has_patch_diff(""))

    def test_whitespace_only_returns_false(self) -> None:
        self.assertFalse(_has_patch_diff("   \n\t  "))

    def test_no_diff_marker_returns_false(self) -> None:
        self.assertFalse(_has_patch_diff("some random text"))

    def test_starts_with_diff_marker_returns_true(self) -> None:
        self.assertTrue(_has_patch_diff("diff --git a/file.py b/file.py\n..."))

    def test_contains_diff_marker_returns_true(self) -> None:
        patch = "some header\ndiff --git a/file.py b/file.py\n..."
        self.assertTrue(_has_patch_diff(patch))

    def test_diff_without_leading_space_not_matched(self) -> None:
        # Must be "diff --git " with trailing space
        self.assertFalse(_has_patch_diff("diff--git a/file.py"))


class PatchPathsTest(unittest.TestCase):
    """Test _patch_paths helper for extracting file paths from patches."""

    def test_empty_patch_returns_empty_list(self) -> None:
        self.assertEqual([], _patch_paths(""))

    def test_no_diff_lines_returns_empty_list(self) -> None:
        self.assertEqual([], _patch_paths("some text\nmore text"))

    def test_single_file_extraction(self) -> None:
        patch = "diff --git a/src/file.py b/src/file.py\n..."
        self.assertEqual(["src/file.py"], _patch_paths(patch))

    def test_multiple_files_sorted_and_deduplicated(self) -> None:
        patch = (
            "diff --git a/z.py b/z.py\n...\n"
            "diff --git a/a.py b/a.py\n...\n"
            "diff --git a/m.py b/m.py\n...\n"
            "diff --git a/z.py b/z.py\n...\n"
        )
        self.assertEqual(["a.py", "m.py", "z.py"], _patch_paths(patch))

    def test_strips_b_prefix(self) -> None:
        patch = "diff --git a/file.py b/file.py\n..."
        self.assertEqual(["file.py"], _patch_paths(patch))

    def test_handles_nested_paths(self) -> None:
        patch = "diff --git a/src/subdir/file.py b/src/subdir/file.py\n..."
        self.assertEqual(["src/subdir/file.py"], _patch_paths(patch))


class SuspiciousPatchPathsTest(unittest.TestCase):
    """Test _suspicious_patch_paths helper for detecting problematic paths."""

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual([], _suspicious_patch_paths([]))

    def test_normal_paths_not_flagged(self) -> None:
        self.assertEqual([], _suspicious_patch_paths(["src/file.py", "tests/test.py"]))

    def test_pycache_flagged(self) -> None:
        self.assertEqual(
            ["src/__pycache__/file.py"],
            _suspicious_patch_paths(["src/__pycache__/file.py"]),
        )

    def test_pyc_file_flagged(self) -> None:
        self.assertEqual(["file.pyc"], _suspicious_patch_paths(["file.pyc"]))

    def test_pyo_file_flagged(self) -> None:
        self.assertEqual(["file.pyo"], _suspicious_patch_paths(["file.pyo"]))

    def test_ds_store_flagged(self) -> None:
        self.assertEqual([".DS_Store"], _suspicious_patch_paths([".DS_Store"]))

    def test_egg_info_flagged(self) -> None:
        self.assertEqual(
            ["pkg.egg-info/PKG-INFO"],
            _suspicious_patch_paths(["pkg.egg-info/PKG-INFO"]),
        )

    def test_pytest_cache_flagged(self) -> None:
        self.assertEqual(
            [".pytest_cache/v/cache/lastfailed"],
            _suspicious_patch_paths([".pytest_cache/v/cache/lastfailed"]),
        )

    def test_multiple_suspicious_paths(self) -> None:
        paths = ["src/__pycache__/a.py", "file.pyc", "normal.py"]
        result = _suspicious_patch_paths(paths)
        self.assertEqual(["src/__pycache__/a.py", "file.pyc"], result)


class ChangedLineCountTest(unittest.TestCase):
    """Test _changed_line_count helper for counting changed lines in patches."""

    def test_empty_patch_returns_zero(self) -> None:
        self.assertEqual(0, _changed_line_count(""))

    def test_no_changed_lines_returns_zero(self) -> None:
        self.assertEqual(0, _changed_line_count("some text\nmore text"))

    def test_added_lines_counted(self) -> None:
        patch = "@@ line @@\n+added line 1\n+added line 2"
        self.assertEqual(2, _changed_line_count(patch))

    def test_removed_lines_counted(self) -> None:
        patch = "@@ line @@\n-removed line 1\n-removed line 2"
        self.assertEqual(2, _changed_line_count(patch))

    def test_mixed_changes_counted(self) -> None:
        patch = "@@ line @@\n-removed\n+added\n-removed2\n+added2"
        self.assertEqual(4, _changed_line_count(patch))

    def test_file_headers_not_counted(self) -> None:
        patch = "--- a/file.py\n+++ b/file.py\n+added line"
        self.assertEqual(1, _changed_line_count(patch))

    def test_context_lines_not_counted(self) -> None:
        patch = "@@ line @@\n context line\n+added line"
        self.assertEqual(1, _changed_line_count(patch))


class BranchNameTest(unittest.TestCase):
    """Test _branch_name helper for generating branch names."""

    def test_simple_title_with_run_id(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="test", name="repo", url="https://github.com/test/repo"),
            repo_profile="test profile",
            opportunities=[],
            selected_task=SelectedTask(title="Add unit tests"),
        )
        branch = _branch_name(result, run_id="abc123")
        self.assertEqual("contribarena/abc123-add-unit-tests", branch)

    def test_title_with_special_characters(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="test", name="repo", url="https://github.com/test/repo"),
            repo_profile="test profile",
            opportunities=[],
            selected_task=SelectedTask(title="Fix bug: #123 (urgent!)"),
        )
        branch = _branch_name(result, run_id="run1")
        self.assertEqual("contribarena/run1-fix-bug-123-urgent", branch)

    def test_empty_title_uses_dry_run(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="test", name="repo", url="https://github.com/test/repo"),
            repo_profile="test profile",
            opportunities=[],
            selected_task=SelectedTask(title=""),
        )
        branch = _branch_name(result, run_id="run1")
        self.assertEqual("contribarena/run1-dry-run", branch)

    def test_no_run_id_uses_title_only(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="test", name="repo", url="https://github.com/test/repo"),
            repo_profile="test profile",
            opportunities=[],
            selected_task=SelectedTask(title="Update docs"),
        )
        branch = _branch_name(result)
        self.assertEqual("contribarena/update-docs", branch)

    def test_collapses_multiple_dashes(self) -> None:
        result = AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="test", name="repo", url="https://github.com/test/repo"),
            repo_profile="test profile",
            opportunities=[],
            selected_task=SelectedTask(title="Fix   multiple   spaces"),
        )
        branch = _branch_name(result, run_id="r1")
        self.assertEqual("contribarena/r1-fix-multiple-spaces", branch)


class RenderPrDescriptionTest(unittest.TestCase):
    """Test render_pr_description helper for formatting PR descriptions."""

    def test_basic_rendering(self) -> None:
        draft = PullRequestDraft(
            title="Add tests",
            branch="contribarena/run1-add-tests",
            labels=["contribarena-dry-run", "low-risk"],
            body="Test body content",
        )
        result = render_pr_description(draft)
        self.assertIn("# Add tests", result)
        self.assertIn("contribarena/run1-add-tests", result)
        self.assertIn("contribarena-dry-run, low-risk", result)
        self.assertIn("Test body content", result)

    def test_empty_labels_renders_na(self) -> None:
        draft = PullRequestDraft(
            title="Test",
            branch="test-branch",
            labels=[],
            body="body",
        )
        result = render_pr_description(draft)
        self.assertIn("Labels: n/a", result)


class LiveActionLogEntriesTest(unittest.TestCase):
    """Test live_action_log_entries helper for generating log entries."""

    def test_none_draft_returns_skipped_entry(self) -> None:
        entries = live_action_log_entries(None)
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("dry_run", entry["mode"])
        self.assertEqual("github.open_pr", entry["action"])
        self.assertEqual("skipped", entry["status"])
        self.assertEqual("quality_gate_not_passed", entry["reason"])
        self.assertFalse(entry["external_write"])

    def test_valid_draft_returns_prepared_entry(self) -> None:
        draft = PullRequestDraft(
            title="Test PR",
            branch="test-branch",
            labels=["label1"],
            body="body",
        )
        entries = live_action_log_entries(draft)
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("dry_run", entry["mode"])
        self.assertEqual("github.open_pr", entry["action"])
        self.assertEqual("prepared", entry["status"])
        self.assertEqual("Test PR", entry["title"])
        self.assertEqual("test-branch", entry["branch"])
        self.assertEqual(["label1"], entry["labels"])
        self.assertFalse(entry["external_write"])


class PostmortemLessonTest(unittest.TestCase):
    """Test _postmortem_lesson helper for generating lessons."""

    def test_non_completed_terminal(self) -> None:
        terminal = TerminalState(
            status="blocked",
            reason="test reason",
            layer="run",
        )
        quality = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, quality, ci)
        self.assertIn("stopped before PR dry-run completion", lesson)

    def test_completed_but_quality_blocked(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="done",
            layer="run",
        )
        quality = QualityGateResult(status="block")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, quality, ci)
        self.assertIn("did not meet PR-readiness requirements", lesson)

    def test_completed_quality_passed_but_ci_failed(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="done",
            layer="run",
        )
        quality = QualityGateResult(status="pass")
        ci = CiStatus(status="failure")
        lesson = _postmortem_lesson(terminal, quality, ci)
        self.assertIn("CI evidence was not clean enough", lesson)

    def test_all_passed(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="done",
            layer="run",
        )
        quality = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, quality, ci)
        self.assertIn("PR-ready artifact set", lesson)


class RenderPostmortemTest(unittest.TestCase):
    """Test render_postmortem helper for generating postmortem reports."""

    def test_basic_postmortem(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="done",
            layer="run",
        )
        quality = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        draft = None
        result = render_postmortem(terminal, quality, ci, draft)
        self.assertIn("# Postmortem", result)
        self.assertIn("Status: completed", result)
        self.assertIn("Reason: done", result)
        self.assertIn("Layer: run", result)
        self.assertIn("Draft produced: False", result)
        self.assertIn("CI status: success", result)

    def test_postmortem_with_blockers(self) -> None:
        terminal = TerminalState(
            status="blocked",
            reason="test",
            layer="agent",
        )
        quality = QualityGateResult(
            status="block",
            blockers=["blocker1", "blocker2"],
            warnings=["warning1"],
        )
        ci = CiStatus(status="not_run")
        result = render_postmortem(terminal, quality, ci, None)
        self.assertIn("### Blockers", result)
        self.assertIn("- blocker1", result)
        self.assertIn("- blocker2", result)
        self.assertIn("### Warnings", result)
        self.assertIn("- warning1", result)

    def test_postmortem_with_draft(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="done",
            layer="run",
        )
        quality = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        draft = PullRequestDraft(
            title="Test",
            branch="test",
            labels=[],
            body="body",
        )
        result = render_postmortem(terminal, quality, ci, draft)
        self.assertIn("Draft produced: True", result)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
