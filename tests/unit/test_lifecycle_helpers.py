from __future__ import annotations

import unittest

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    GovernanceConfig,
    IssueConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RepoSearchFilters,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.lifecycle import (
    _add_check,
    _branch_name,
    _changed_line_count,
    _has_patch_diff,
    _patch_paths,
    _postmortem_lesson,
    _pr_lifecycle_label,
    _pr_notice,
    _pr_title,
    _suspicious_patch_paths,
    apply_quality_gate_to_result,
    build_ci_status,
    build_pr_draft,
    live_action_log_entries,
    render_postmortem,
    render_pr_description,
    render_quality_gate_section,
)
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.models import (
    AciResult,
    AgentFinalResult,
    CiStatus,
    OpportunitySummary,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    RepoSummary,
    SelectedTask,
    TerminalState,
)

_CANDIDATE = RepoCandidate(
    owner="qWaitCrypto",
    repo="ContribArena",
    url="https://github.com/qWaitCrypto/ContribArena",
)


def _minimal_config(*, mode: str = "shadow") -> RunConfig:
    governance = GovernanceConfig()
    if mode == "owned_live":
        governance = GovernanceConfig(
            owned_repositories=[
                OwnedRepositoryPolicy(
                    owner="qWaitCrypto",
                    repo="ContribArena",
                    pr_submission=PrSubmissionConfig(strategy="fork"),
                )
            ]
        )
    return RunConfig(
        run=RunSection(
            season_id="season_0",
            mode=mode,
            model="test-model",
        ),
        discovery=DiscoveryConfig(
            query="test",
            filters=RepoSearchFilters(language="Python"),
            candidates=[_CANDIDATE] if mode == "owned_live" else [],
        ),
        artifacts=ArtifactConfig(output_root="/tmp/artifacts"),
        workspace=WorkspaceConfig(backend="docker"),
        governance=governance,
    )


def _minimal_result(*, title: str = "fix: test", risk: str = "low") -> AgentFinalResult:
    return AgentFinalResult(
        status="completed",
        repo=RepoSummary(
            owner="qWaitCrypto",
            name="ContribArena",
            url="https://github.com/qWaitCrypto/ContribArena",
        ),
        repo_profile="test repo",
        opportunities=[
            OpportunitySummary(
                title="test opportunity",
                description="test",
                risk="low",
            )
        ],
        selected_task=SelectedTask(
            title=title,
            description="test",
            expected_change="change",
            risk=risk,
            rationale="reason",
        ),
        problem_statement_summary="test problem",
        reproduction_notes="test reproduction",
        verification_summary="test verification",
    )


class AddCheckTest(unittest.TestCase):
    def test_passed_check_adds_pass_status(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_check(checks, blockers, "minimality", True, "detail text")
        self.assertEqual(1, len(checks))
        self.assertEqual("pass", checks[0].status)
        self.assertEqual("minimality", checks[0].name)
        self.assertEqual("detail text", checks[0].detail)
        self.assertEqual([], blockers)

    def test_failed_check_adds_block_status_and_blocker(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_check(checks, blockers, "agent_completed", False, "agent did not complete")
        self.assertEqual(1, len(checks))
        self.assertEqual("block", checks[0].status)
        self.assertEqual("agent_completed", checks[0].name)
        self.assertEqual(1, len(blockers))
        self.assertEqual("agent_completed: agent did not complete", blockers[0])

    def test_multiple_checks_appended_in_order(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_check(checks, blockers, "first", True, "ok")
        _add_check(checks, blockers, "second", False, "bad")
        _add_check(checks, blockers, "third", True, "fine")
        self.assertEqual(3, len(checks))
        self.assertEqual(["first", "second", "third"], [c.name for c in checks])
        self.assertEqual(["second: bad"], blockers)

    def test_all_passed_no_blockers(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_check(checks, blockers, "a", True, "ok a")
        _add_check(checks, blockers, "b", True, "ok b")
        self.assertEqual([], blockers)

    def test_empty_detail_preserved(self) -> None:
        checks: list[QualityGateCheck] = []
        blockers: list[str] = []
        _add_check(checks, blockers, "test", True, "")
        self.assertEqual("", checks[0].detail)


class HasPatchDiffTest(unittest.TestCase):
    def test_starts_with_diff_git(self) -> None:
        self.assertTrue(_has_patch_diff("diff --git a/file.py b/file.py\ncontent"))

    def test_contains_diff_git_in_middle(self) -> None:
        self.assertTrue(_has_patch_diff("some prefix\ndiff --git a/x.py b/x.py\nmore"))

    def test_no_diff(self) -> None:
        self.assertFalse(_has_patch_diff("just some text"))

    def test_empty_string(self) -> None:
        self.assertFalse(_has_patch_diff(""))

    def test_whitespace_only(self) -> None:
        self.assertFalse(_has_patch_diff("   \n   "))

    def test_partial_substring_not_matched(self) -> None:
        self.assertFalse(_has_patch_diff("diff --gi something"))


class PatchPathsTest(unittest.TestCase):
    def test_single_path_extracted(self) -> None:
        patch = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py"
        self.assertEqual(["src/app.py"], _patch_paths(patch))

    def test_multiple_paths_sorted_unique(self) -> None:
        patch = (
            "diff --git a/src/b.py b/src/b.py\n"
            "diff --git a/src/a.py b/src/a.py\n"
            "diff --git a/src/b.py b/src/b.py\n"
        )
        self.assertEqual(["src/a.py", "src/b.py"], _patch_paths(patch))

    def test_ignores_non_diff_lines(self) -> None:
        patch = (
            "some header\n"
            "diff --git a/src/x.py b/src/x.py\n"
            "--- a/src/x.py\n"
            "+++ b/src/x.py\n"
            "@@ -1 +1 @@\n"
        )
        self.assertEqual(["src/x.py"], _patch_paths(patch))

    def test_empty_patch(self) -> None:
        self.assertEqual([], _patch_paths(""))

    def test_b_prefix_stripped(self) -> None:
        patch = "diff --git a/foo/bar.py b/foo/bar.py"
        self.assertEqual(["foo/bar.py"], _patch_paths(patch))

    def test_deep_paths(self) -> None:
        patch = "diff --git a/a/b/c/d/file.txt b/a/b/c/d/file.txt"
        self.assertEqual(["a/b/c/d/file.txt"], _patch_paths(patch))


class SuspiciousPatchPathsTest(unittest.TestCase):
    def test_normal_paths_clean(self) -> None:
        self.assertEqual([], _suspicious_patch_paths(["src/app.py", "tests/test.py"]))

    def test_pycache_detected(self) -> None:
        result = _suspicious_patch_paths(
            ["src/app.py", "src/__pycache__/module.cpython-311.pyc"]
        )
        self.assertIn("src/__pycache__/module.cpython-311.pyc", result)

    def test_pyc_file_detected(self) -> None:
        self.assertIn("foo.pyc", _suspicious_patch_paths(["foo.pyc"]))

    def test_pyo_file_detected(self) -> None:
        self.assertIn("foo.pyo", _suspicious_patch_paths(["foo.pyo"]))

    def test_ds_store_detected(self) -> None:
        self.assertIn(".DS_Store", _suspicious_patch_paths([".DS_Store"]))

    def test_egg_info_detected(self) -> None:
        result = _suspicious_patch_paths(["pkg.egg-info/PKG-INFO"])
        self.assertIn("pkg.egg-info/PKG-INFO", result)

    def test_pytest_cache_detected(self) -> None:
        result = _suspicious_patch_paths([".pytest_cache/v/cache/lastfailed"])
        self.assertIn(".pytest_cache/v/cache/lastfailed", result)

    def test_embedded_marker_in_deep_path(self) -> None:
        result = _suspicious_patch_paths(["deep/nested/__pycache__/mod.pyc"])
        self.assertIn("deep/nested/__pycache__/mod.pyc", result)

    def test_empty_list(self) -> None:
        self.assertEqual([], _suspicious_patch_paths([]))


class ChangedLineCountTest(unittest.TestCase):
    def test_counts_added_and_removed_lines(self) -> None:
        patch = "+added line\n-removed line\n+another added"
        self.assertEqual(3, _changed_line_count(patch))

    def test_excludes_header_lines(self) -> None:
        patch = (
            "+++ b/file.py\n"
            "--- a/file.py\n"
            "+real change\n"
            "-real deletion\n"
        )
        self.assertEqual(2, _changed_line_count(patch))

    def test_empty_patch_zero(self) -> None:
        self.assertEqual(0, _changed_line_count(""))

    def test_only_headers_zero(self) -> None:
        patch = "+++ b/file.py\n--- a/file.py"
        self.assertEqual(0, _changed_line_count(patch))

    def test_mixed_content(self) -> None:
        patch = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,3 +1,3 @@\n"
            " context\n"
            "-old line\n"
            "+new line\n"
            " context\n"
        )
        self.assertEqual(2, _changed_line_count(patch))


class PrLifecycleLabelTest(unittest.TestCase):
    def test_shadow_mode_label(self) -> None:
        config = _minimal_config(mode="shadow")
        self.assertEqual("contribarena-dry-run", _pr_lifecycle_label(config))

    def test_owned_live_mode_label(self) -> None:
        config = _minimal_config(mode="owned_live")
        self.assertEqual("contribarena-live", _pr_lifecycle_label(config))

    def test_external_live_mode_label(self) -> None:
        config = _minimal_config(mode="external_live")
        self.assertEqual("contribarena-external-live", _pr_lifecycle_label(config))


class PrNoticeTest(unittest.TestCase):
    def test_shadow_dry_run_notice(self) -> None:
        config = _minimal_config(mode="shadow")
        heading, body = _pr_notice(config)
        self.assertEqual("## Dry-Run Notice", heading)
        self.assertIn("dry-run", body.lower())
        self.assertIn("no external", body.lower())

    def test_owned_live_notice(self) -> None:
        config = _minimal_config(mode="owned_live")
        heading, body = _pr_notice(config)
        self.assertEqual("## Live PR Notice", heading)
        self.assertIn("ContribArena harness", body)
        self.assertIn("quality", body.lower())

    def test_external_live_notice(self) -> None:
        config = _minimal_config(mode="external_live")
        heading, body = _pr_notice(config)
        self.assertEqual("## External Live PR Notice", heading)
        self.assertIn("bot account", body.lower())
        self.assertIn("AI-assisted", body)


class PrTitleTest(unittest.TestCase):
    def test_uses_issue_title_when_configured(self) -> None:
        config = _minimal_config()
        config.issue = IssueConfig(
            problem_statement="crash on startup",
            title="Bug: fix crash on startup",
        )
        result = _minimal_result(title="different result title")
        self.assertEqual("Bug: fix crash on startup", _pr_title(config, result))

    def test_uses_result_title_when_no_issue(self) -> None:
        config = _minimal_config()
        result = _minimal_result(title="result title wins")
        self.assertEqual("result title wins", _pr_title(config, result))

    def test_uses_result_title_when_issue_title_none(self) -> None:
        config = _minimal_config()
        config.issue = IssueConfig(
            problem_statement="crash on startup",
            title=None,
        )
        result = _minimal_result(title="result title")
        self.assertEqual("result title", _pr_title(config, result))


class PostmortemLessonTest(unittest.TestCase):
    def test_non_completed_terminal_lesson(self) -> None:
        terminal = TerminalState(status="blocked", reason="test", layer="agent")
        gate = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, gate, ci)
        self.assertIn("Run stopped", lesson)

    def test_blocked_quality_gate_lesson(self) -> None:
        terminal = TerminalState(status="completed", reason="done", layer="run")
        gate = QualityGateResult(status="block")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, gate, ci)
        self.assertIn("did not meet", lesson)

    def test_ci_failure_lesson(self) -> None:
        terminal = TerminalState(status="completed", reason="done", layer="run")
        gate = QualityGateResult(status="pass")
        ci = CiStatus(status="failure")
        lesson = _postmortem_lesson(terminal, gate, ci)
        self.assertIn("CI evidence", lesson)

    def test_all_green_lesson(self) -> None:
        terminal = TerminalState(status="completed", reason="done", layer="run")
        gate = QualityGateResult(status="pass")
        ci = CiStatus(status="success")
        lesson = _postmortem_lesson(terminal, gate, ci)
        self.assertIn("PR-ready", lesson)


class RenderQualityGateSectionTest(unittest.TestCase):
    def test_pass_with_no_blockers(self) -> None:
        gate = QualityGateResult(
            status="pass",
            checks=[QualityGateCheck(name="a", status="pass", detail="ok")],
        )
        sections = render_quality_gate_section(gate)
        text = "\n".join(sections)
        self.assertIn("## Contribution Quality Gate", text)
        self.assertIn("- Status: pass", text)
        self.assertIn("- a: pass (ok)", text)

    def test_block_with_blockers(self) -> None:
        gate = QualityGateResult(
            status="block",
            blockers=["patch_submitted: no diff"],
            checks=[QualityGateCheck(name="x", status="block", detail="bad")],
        )
        sections = render_quality_gate_section(gate)
        text = "\n".join(sections)
        self.assertIn("### Blockers", text)
        self.assertIn("- patch_submitted: no diff", text)

    def test_warnings_rendered(self) -> None:
        gate = QualityGateResult(
            status="pass",
            warnings=["3 failed commands"],
            checks=[
                QualityGateCheck(name="command_health", status="warn", detail="3 failed")
            ],
        )
        sections = render_quality_gate_section(gate)
        text = "\n".join(sections)
        self.assertIn("### Warnings", text)
        self.assertIn("- 3 failed commands", text)


class RenderPrDescriptionTest(unittest.TestCase):
    def test_basic_description(self) -> None:
        draft = PullRequestDraft(
            title="fix: test",
            branch="contribarena/abc-test",
            labels=["contribarena-live"],
            body="PR body text",
        )
        desc = render_pr_description(draft)
        self.assertIn("# fix: test", desc)
        self.assertIn("- Branch: `contribarena/abc-test`", desc)
        self.assertIn("contribarena-live", desc)
        self.assertIn("PR body text", desc)

    def test_no_labels_shows_na(self) -> None:
        draft = PullRequestDraft(
            title="fix: test",
            branch="contribarena/abc-test",
            body="text",
        )
        desc = render_pr_description(draft)
        self.assertIn("- Labels: n/a", desc)


class ApplyQualityGateToResultTest(unittest.TestCase):
    def test_pass_does_not_modify_result(self) -> None:
        result = _minimal_result()
        gate = QualityGateResult(status="pass")
        apply_quality_gate_to_result(result, gate)
        self.assertEqual("completed", result.status)

    def test_block_changes_status_to_blocked(self) -> None:
        result = _minimal_result()
        gate = QualityGateResult(
            status="block",
            blockers=["agent_completed: agent status is failed"],
        )
        apply_quality_gate_to_result(result, gate)
        self.assertEqual("blocked", result.status)
        self.assertTrue(
            any("agent_completed" in b for b in result.blockers)
        )

    def test_block_adds_verification_summary_if_empty(self) -> None:
        result = _minimal_result()
        result.verification_summary = ""
        gate = QualityGateResult(
            status="block",
            blockers=["minimality: too large"],
        )
        apply_quality_gate_to_result(result, gate)
        self.assertIn("minimality", result.verification_summary)

    def test_non_completed_status_unchanged(self) -> None:
        result = _minimal_result()
        result.status = "failed"
        gate = QualityGateResult(status="block", blockers=["test: fail"])
        apply_quality_gate_to_result(result, gate)
        self.assertEqual("failed", result.status)


class BuildCiStatusTest(unittest.TestCase):
    def test_non_pass_gate_returns_not_run(self) -> None:
        capture = ArtifactCapture()
        gate = QualityGateResult(status="block")
        ci = build_ci_status(capture, gate)
        self.assertEqual("not_run", ci.status)
        self.assertEqual(1, len(ci.checks))
        self.assertEqual("skipped", ci.checks[0].status)

    def test_pass_gate_no_aci_verify_returns_skipped(self) -> None:
        capture = ArtifactCapture()
        gate = QualityGateResult(status="pass")
        ci = build_ci_status(capture, gate)
        self.assertEqual("success", ci.status)
        self.assertEqual("local_verification", ci.checks[0].name)
        self.assertEqual("skipped", ci.checks[0].status)

    def test_successful_aci_verify(self) -> None:
        capture = ArtifactCapture()
        capture.aci_results.append(
            AciResult(tool="aci_verify", success=True, output="All tests passed")
        )
        gate = QualityGateResult(status="pass")
        ci = build_ci_status(capture, gate)
        self.assertEqual("success", ci.status)
        self.assertEqual("success", ci.checks[0].status)

    def test_failed_aci_verify(self) -> None:
        capture = ArtifactCapture()
        capture.aci_results.append(
            AciResult(tool="aci_verify", success=False, output="2 failures")
        )
        gate = QualityGateResult(status="pass")
        ci = build_ci_status(capture, gate)
        self.assertEqual("failure", ci.status)
        self.assertEqual("failure", ci.checks[0].status)

    def test_non_aci_verify_tools_ignored(self) -> None:
        capture = ArtifactCapture()
        capture.aci_results.append(
            AciResult(tool="aci_apply_patch", success=True, output="applied")
        )
        capture.aci_results.append(
            AciResult(tool="aci_verify", success=True, output="passed")
        )
        gate = QualityGateResult(status="pass")
        ci = build_ci_status(capture, gate)
        self.assertEqual(1, len(ci.checks))
        self.assertEqual("success", ci.checks[0].status)


class LiveActionLogEntriesTest(unittest.TestCase):
    def test_none_draft_returns_skipped_entry(self) -> None:
        entries = live_action_log_entries(None)
        self.assertEqual(1, len(entries))
        self.assertEqual("skipped", entries[0]["status"])
        self.assertFalse(entries[0]["external_write"])

    def test_draft_returns_prepared_entry(self) -> None:
        draft = PullRequestDraft(
            title="fix: test",
            branch="contribarena/abc-test",
            labels=["contribarena-live"],
            body="body",
        )
        entries = live_action_log_entries(draft)
        self.assertEqual(1, len(entries))
        self.assertEqual("prepared", entries[0]["status"])
        self.assertEqual("fix: test", entries[0]["title"])
        self.assertEqual("contribarena/abc-test", entries[0]["branch"])


class RenderPostmortemTest(unittest.TestCase):
    def test_full_postmortem(self) -> None:
        terminal = TerminalState(status="completed", reason="done", layer="run")
        gate = QualityGateResult(
            status="pass",
            blockers=[],
            warnings=["3 failed commands"],
            checks=[QualityGateCheck(name="a", status="pass", detail="ok")],
        )
        ci = CiStatus(status="success")
        draft = PullRequestDraft(
            title="fix: test",
            branch="contribarena/abc-test",
            body="body",
        )
        output = render_postmortem(terminal, gate, ci, draft)
        self.assertIn("# Postmortem", output)
        self.assertIn("## Terminal State", output)
        self.assertIn("- Status: completed", output)
        self.assertIn("## Contribution Gate", output)
        self.assertIn("- Status: pass", output)
        self.assertIn("### Warnings", output)
        self.assertIn("## PR Lifecycle", output)
        self.assertIn("- Draft produced: True", output)
        self.assertIn("## Lesson", output)

    def test_postmortem_with_blockers(self) -> None:
        terminal = TerminalState(
            status="blocked", reason="quality", layer="contribution"
        )
        gate = QualityGateResult(
            status="block",
            blockers=["agent_completed: failed"],
            checks=[QualityGateCheck(name="a", status="block", detail="bad")],
        )
        ci = CiStatus(status="not_run")
        output = render_postmortem(terminal, gate, ci, None)
        self.assertIn("### Blockers", output)
        self.assertIn("- agent_completed: failed", output)
        self.assertIn("- Draft produced: False", output)


class BranchNameTest(unittest.TestCase):
    def test_basic_branch_name(self) -> None:
        result = _minimal_result(title="fix: add unit test")
        name = _branch_name(result, run_id="abc123")
        self.assertTrue(name.startswith("contribarena/abc123-"))

    def test_no_run_id(self) -> None:
        result = _minimal_result(title="fix: add unit test")
        name = _branch_name(result, run_id="")
        self.assertTrue(name.startswith("contribarena/"))
        self.assertFalse(name.startswith("contribarena/-"))

    def test_empty_run_id(self) -> None:
        result = _minimal_result(title="test")
        name = _branch_name(result, run_id="")
        self.assertEqual("contribarena/test", name)

    def test_special_chars_replaced_with_dash(self) -> None:
        result = _minimal_result(title="fix: add unit test!!!")
        name = _branch_name(result, run_id="abc")
        self.assertNotIn(":", name)
        self.assertNotIn("!", name)

    def test_consecutive_dashes_collapsed(self) -> None:
        result = _minimal_result(title="fix:  add  test")
        name = _branch_name(result, run_id="abc")
        self.assertNotIn("--", name)


class BuildPrDraftTest(unittest.TestCase):
    def test_shadow_draft(self) -> None:
        config = _minimal_config(mode="shadow")
        result = _minimal_result(title="fix: test")
        draft = build_pr_draft(
            config, result, "diff --git a/x.py b/x.py", run_id="id123"
        )
        self.assertEqual("fix: test", draft.title)
        self.assertTrue(draft.branch.startswith("contribarena/"))
        self.assertIn("contribarena-dry-run", draft.labels)
        self.assertIn("## Summary", draft.body)
        self.assertIn("## Verification", draft.body)
        self.assertIn("## Risk", draft.body)
        self.assertIn("## Dry-Run Notice", draft.body)

    def test_owned_live_draft(self) -> None:
        config = _minimal_config(mode="owned_live")
        result = _minimal_result(title="test", risk="low")
        draft = build_pr_draft(config, result, "", run_id="id123")
        self.assertIn("contribarena-live", draft.labels)
        self.assertIn("risk-low", draft.labels)
        self.assertIn("## Live PR Notice", draft.body)

    def test_issue_config_adds_issue_solving_label(self) -> None:
        config = _minimal_config()
        config.issue = IssueConfig(
            problem_statement="crash on startup",
            title="Bug: fix crash",
        )
        result = _minimal_result()
        draft = build_pr_draft(config, result, "")
        self.assertIn("issue-solving", draft.labels)

    def test_high_risk_label(self) -> None:
        config = _minimal_config()
        result = _minimal_result(title="big change", risk="high")
        draft = build_pr_draft(config, result, "")
        self.assertIn("risk-high", draft.labels)
