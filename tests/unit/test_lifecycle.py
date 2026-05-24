from __future__ import annotations

import unittest

from contribarena.engine.lifecycle import (
    apply_quality_gate_to_result,
    live_action_log_entries,
    render_pr_description,
    render_quality_gate_section,
    _branch_name,
    _changed_line_count,
    _has_patch_diff,
    _has_successful_verification_after_last_edit,
    _patch_paths,
    _postmortem_lesson,
    _pr_lifecycle_label,
    _pr_notice,
    _pr_title,
    _suspicious_patch_paths,
)
from contribarena.models import (
    AciResult,
    AgentFinalResult,
    CiStatus,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    RepoSummary,
    SelectedTask,
    TerminalState,
)


def _make_result(status="completed", task_title="Some Task") -> AgentFinalResult:
    return AgentFinalResult(
        status=status,
        repo=RepoSummary(owner="o", name="r", url="https://example.com"),
        repo_profile="",
        opportunities=[],
        selected_task=SelectedTask(title=task_title),
    )


class HasPatchDiffTest(unittest.TestCase):
    def test_returns_true_for_diff_at_start(self) -> None:
        self.assertTrue(_has_patch_diff("diff --git a/f.py b/f.py\n"))

    def test_returns_true_for_embedded_diff(self) -> None:
        self.assertTrue(_has_patch_diff("header\ndiff --git a/f.py b/f.py\n"))

    def test_returns_false_for_empty(self) -> None:
        self.assertFalse(_has_patch_diff(""))

    def test_returns_false_for_text_only(self) -> None:
        self.assertFalse(_has_patch_diff("some random text"))

    def test_returns_false_for_diff_in_middle_of_line(self) -> None:
        self.assertFalse(_has_patch_diff("this diff --git a/f.py is not valid"))

    def test_returns_false_for_whitespace_only(self) -> None:
        self.assertFalse(_has_patch_diff("   \n  "))


class PatchPathsTest(unittest.TestCase):
    def test_single_file(self) -> None:
        self.assertEqual(["src/app.py"], _patch_paths("diff --git a/src/app.py b/src/app.py\n"))

    def test_multiple_files_sorted(self) -> None:
        patch = "diff --git a/beta.py b/beta.py\ndiff --git a/alpha.py b/alpha.py\n"
        self.assertEqual(["alpha.py", "beta.py"], _patch_paths(patch))

    def test_removes_b_prefix(self) -> None:
        self.assertEqual(["x.py"], _patch_paths("diff --git a/x.py b/x.py\n"))

    def test_deduplicates(self) -> None:
        patch = "diff --git a/f.py b/f.py\ndiff --git a/f.py b/f.py\n"
        self.assertEqual(["f.py"], _patch_paths(patch))

    def test_empty_patch(self) -> None:
        self.assertEqual([], _patch_paths(""))

    def test_no_diff_lines(self) -> None:
        self.assertEqual([], _patch_paths("just some text\n"))


class SuspiciousPatchPathsTest(unittest.TestCase):
    def test_clean_paths(self) -> None:
        self.assertEqual([], _suspicious_patch_paths(["src/app.py"]))

    def test_pycache(self) -> None:
        self.assertEqual(
            ["src/__pycache__/mod.pyc"],
            _suspicious_patch_paths(["src/__pycache__/mod.pyc"]),
        )

    def test_pyc_extension(self) -> None:
        self.assertEqual(["mod.pyc"], _suspicious_patch_paths(["mod.pyc"]))

    def test_pyo_extension(self) -> None:
        self.assertEqual(["mod.pyo"], _suspicious_patch_paths(["mod.pyo"]))

    def test_ds_store(self) -> None:
        self.assertEqual([".DS_Store"], _suspicious_patch_paths([".DS_Store"]))

    def test_egg_info(self) -> None:
        self.assertEqual(
            ["pkg.egg-info/PKG-INFO"],
            _suspicious_patch_paths(["pkg.egg-info/PKG-INFO"]),
        )

    def test_pytest_cache(self) -> None:
        self.assertEqual(
            [".pytest_cache/v/cache"],
            _suspicious_patch_paths([".pytest_cache/v/cache"]),
        )

    def test_mixed_clean_and_suspicious(self) -> None:
        self.assertEqual(
            ["src/__pycache__/x.pyc"],
            _suspicious_patch_paths(["src/app.py", "src/__pycache__/x.pyc"]),
        )

    def test_empty_list(self) -> None:
        self.assertEqual([], _suspicious_patch_paths([]))


class ChangedLineCountTest(unittest.TestCase):
    def test_counts_additions(self) -> None:
        patch = "+line1\n+line2\n"
        self.assertEqual(2, _changed_line_count(patch))

    def test_counts_deletions(self) -> None:
        patch = "-line1\n"
        self.assertEqual(1, _changed_line_count(patch))

    def test_skips_diff_headers(self) -> None:
        patch = "+++ b/f.py\n--- a/f.py\n+new\n-old\n"
        self.assertEqual(2, _changed_line_count(patch))

    def test_empty_patch(self) -> None:
        self.assertEqual(0, _changed_line_count(""))


class HasSuccessfulVerificationAfterLastEditTest(unittest.TestCase):
    def _make_capture(self, results):
        class FakeCapture:
            aci_results = results
        return FakeCapture()

    def test_returns_true_when_verify_after_edit(self) -> None:
        capture = self._make_capture([
            AciResult(tool="aci_apply_patch", success=True, output="", error=None),
            AciResult(tool="aci_verify", success=True, output="OK", error=None),
        ])
        self.assertTrue(_has_successful_verification_after_last_edit(capture))

    def test_returns_false_when_no_verify_after_edit(self) -> None:
        capture = self._make_capture([
            AciResult(tool="aci_apply_patch", success=True, output="", error=None),
        ])
        self.assertFalse(_has_successful_verification_after_last_edit(capture))

    def test_returns_true_when_no_edits_present(self) -> None:
        capture = self._make_capture([
            AciResult(tool="aci_verify", success=True, output="OK", error=None),
        ])
        self.assertTrue(_has_successful_verification_after_last_edit(capture))

    def test_returns_false_when_verify_fails(self) -> None:
        capture = self._make_capture([
            AciResult(tool="aci_apply_patch", success=True, output="", error=None),
            AciResult(tool="aci_verify", success=False, output="fail", error="fail"),
        ])
        self.assertFalse(_has_successful_verification_after_last_edit(capture))

    def test_returns_true_for_accepted_no_command_review(self) -> None:
        capture = self._make_capture([
            AciResult(tool="aci_apply_patch", success=True, output="", error=None),
            AciResult(tool="aci_submit_patch", success=True, output="", error=None, review_notes="no-command verification rationale accepted"),
        ])
        self.assertTrue(_has_successful_verification_after_last_edit(capture))


class PrTitleTest(unittest.TestCase):
    def _make_config(self, issue_title=None):
        class FakeIssue:
            title = issue_title
        class FakeConfig:
            issue = FakeIssue() if issue_title else None
        return FakeConfig()

    def test_uses_issue_title_when_present(self) -> None:
        cfg = self._make_config(issue_title="Fix bug #123")
        self.assertEqual("Fix bug #123", _pr_title(cfg, _make_result()))

    def test_falls_back_to_task_title(self) -> None:
        cfg = self._make_config()
        result = _make_result(task_title="Add tests")
        self.assertEqual("Add tests", _pr_title(cfg, result))


class PrLifecycleLabelTest(unittest.TestCase):
    def _make_config(self, mode: str):
        class FakeConfig:
            class Run:
                def __init__(self, m):
                    self.mode = m
            run = Run(mode)
        return FakeConfig()

    def test_owned_live(self) -> None:
        self.assertEqual("contribarena-live", _pr_lifecycle_label(self._make_config("owned_live")))

    def test_external_live(self) -> None:
        self.assertEqual("contribarena-external-live", _pr_lifecycle_label(self._make_config("external_live")))

    def test_dry_run(self) -> None:
        self.assertEqual("contribarena-dry-run", _pr_lifecycle_label(self._make_config("dry_run")))


class PrNoticeTest(unittest.TestCase):
    def _make_config(self, mode: str):
        class FakeConfig:
            class Run:
                def __init__(self, m):
                    self.mode = m
            run = Run(mode)
        return FakeConfig()

    def test_owned_live_notice(self) -> None:
        heading, body = _pr_notice(self._make_config("owned_live"))
        self.assertEqual("## Live PR Notice", heading)
        self.assertIn("ContribArena harness", body)

    def test_external_live_notice(self) -> None:
        heading, body = _pr_notice(self._make_config("external_live"))
        self.assertEqual("## External Live PR Notice", heading)
        self.assertIn("bot account", body)

    def test_dry_run_notice(self) -> None:
        heading, body = _pr_notice(self._make_config("dry_run"))
        self.assertEqual("## Dry-Run Notice", heading)
        self.assertIn("dry-run", body)


class BranchNameTest(unittest.TestCase):
    def test_basic_slug(self) -> None:
        self.assertEqual("contribarena/add-unit-tests", _branch_name(_make_result(task_title="Add unit tests")))

    def test_special_chars_become_hyphens(self) -> None:
        self.assertEqual("contribarena/fix-bug-123", _branch_name(_make_result(task_title="Fix bug #123!")))

    def test_collapses_multiple_hyphens(self) -> None:
        self.assertEqual("contribarena/some-task", _branch_name(_make_result(task_title="Some  Task!!!")))

    def test_empty_task_title(self) -> None:
        self.assertEqual("contribarena/dry-run", _branch_name(_make_result(task_title="")))

    def test_whitespace_only_title(self) -> None:
        self.assertEqual("contribarena/dry-run", _branch_name(_make_result(task_title="   ")))


class PostmortemLessonTest(unittest.TestCase):
    def _terminal(self, status="completed") -> TerminalState:
        return TerminalState(status=status, reason="done", layer="run")

    def _qg(self, status="pass") -> QualityGateResult:
        return QualityGateResult(status=status)

    def _ci(self, status="success") -> CiStatus:
        return CiStatus(status=status)

    def test_not_completed(self) -> None:
        lesson = _postmortem_lesson(self._terminal("blocked"), self._qg(), self._ci())
        self.assertIn("stopped before PR", lesson)

    def test_quality_gate_failed(self) -> None:
        lesson = _postmortem_lesson(self._terminal(), self._qg("block"), self._ci())
        self.assertIn("did not meet PR-readiness", lesson)

    def test_ci_failed(self) -> None:
        lesson = _postmortem_lesson(self._terminal(), self._qg(), self._ci("failure"))
        self.assertIn("CI evidence was not clean", lesson)

    def test_all_pass(self) -> None:
        lesson = _postmortem_lesson(self._terminal(), self._qg(), self._ci())
        self.assertIn("PR-ready artifact", lesson)


class ApplyQualityGateToResultTest(unittest.TestCase):
    def test_passes_did_not_modify_completed(self) -> None:
        result = _make_result()
        qg = QualityGateResult(status="pass")
        apply_quality_gate_to_result(result, qg)
        self.assertEqual("completed", result.status)

    def test_blocks_changes_status(self) -> None:
        result = _make_result()
        qg = QualityGateResult(status="block", blockers=["no verification"])
        apply_quality_gate_to_result(result, qg)
        self.assertEqual("blocked", result.status)

    def test_non_completed_unchanged(self) -> None:
        result = _make_result(status="failed")
        qg = QualityGateResult(status="block", blockers=["x"])
        apply_quality_gate_to_result(result, qg)
        self.assertEqual("failed", result.status)


class RenderPrDescriptionTest(unittest.TestCase):
    def test_basic_rendering(self) -> None:
        draft = PullRequestDraft(
            title="Fix bug",
            branch="contribarena/fix-bug",
            labels=["risk-low"],
            body="## Summary\nFixed a bug.",
        )
        output = render_pr_description(draft)
        self.assertIn("# Fix bug", output)
        self.assertIn("`contribarena/fix-bug`", output)
        self.assertIn("risk-low", output)

    def test_empty_labels(self) -> None:
        draft = PullRequestDraft(title="T", branch="b", labels=[], body="body")
        output = render_pr_description(draft)
        self.assertIn("n/a", output)


class RenderQualityGateSectionTest(unittest.TestCase):
    def test_pass_with_no_blockers(self) -> None:
        qg = QualityGateResult(
            status="pass",
            checks=[QualityGateCheck(name="x", status="pass", detail="ok")],
        )
        sections = render_quality_gate_section(qg)
        self.assertIn("- Status: pass", sections)

    def test_includes_blockers(self) -> None:
        qg = QualityGateResult(status="block", blockers=["b1"], checks=[])
        sections = render_quality_gate_section(qg)
        self.assertIn("### Blockers", sections)
        self.assertIn("- b1", sections)

    def test_includes_warnings(self) -> None:
        qg = QualityGateResult(status="pass", warnings=["w1"], checks=[])
        sections = render_quality_gate_section(qg)
        self.assertIn("### Warnings", sections)

    def test_lists_checks(self) -> None:
        qg = QualityGateResult(
            status="pass",
            checks=[QualityGateCheck(name="chk", status="pass", detail="d")],
        )
        sections = render_quality_gate_section(qg)
        self.assertIn("- chk: pass (d)", sections)


class LiveActionLogEntriesTest(unittest.TestCase):
    def test_no_draft_returns_skip(self) -> None:
        entries = live_action_log_entries(None)
        self.assertEqual(1, len(entries))
        self.assertEqual("skipped", entries[0]["status"])
        self.assertFalse(entries[0]["external_write"])

    def test_draft_returns_prepared(self) -> None:
        draft = PullRequestDraft(title="T", branch="b", labels=["l"], body="body")
        entries = live_action_log_entries(draft)
        self.assertEqual(1, len(entries))
        self.assertEqual("prepared", entries[0]["status"])
        self.assertEqual("T", entries[0]["title"])
        self.assertFalse(entries[0]["external_write"])


if __name__ == "__main__":
    unittest.main()
