from __future__ import annotations

import unittest

from contribarena.engine.lifecycle import build_ci_status, render_postmortem
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.models.lifecycle import (
    CiStatus,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    TerminalState,
)
from contribarena.models.tool_results import AciResult


class BuildCiStatusTest(unittest.TestCase):
    def test_returns_not_run_when_quality_gate_blocks(self) -> None:
        capture = ArtifactCapture()
        quality_gate = QualityGateResult(
            status="block",
            blockers=["agent_completed: agent status is failed"],
        )

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("not_run", result.status)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("dry_run_quality_gate", result.checks[0].name)
        self.assertEqual("skipped", result.checks[0].status)

    def test_returns_success_when_verify_result_succeeds(self) -> None:
        capture = ArtifactCapture()
        capture.record_aci_result(
            AciResult(tool="aci_verify", success=True, output="All passed")
        )
        quality_gate = QualityGateResult(status="pass")

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("success", result.status)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("local_verification", result.checks[0].name)
        self.assertEqual("success", result.checks[0].status)

    def test_returns_failure_when_verify_result_fails(self) -> None:
        capture = ArtifactCapture()
        capture.record_aci_result(
            AciResult(tool="aci_verify", success=False, output="1 failed")
        )
        quality_gate = QualityGateResult(status="pass")

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("failure", result.status)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("failure", result.checks[0].status)

    def test_returns_skipped_when_no_verification_command_recorded(self) -> None:
        capture = ArtifactCapture()
        quality_gate = QualityGateResult(status="pass")

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("success", result.status)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("skipped", result.checks[0].status)
        self.assertIn("No local verification", result.checks[0].details)

    def test_multiple_verify_results_aggregate_to_failure(self) -> None:
        capture = ArtifactCapture()
        capture.record_aci_result(
            AciResult(tool="aci_verify", success=True, output="ok")
        )
        capture.record_aci_result(
            AciResult(tool="aci_verify", success=False, output="fail")
        )
        quality_gate = QualityGateResult(status="pass")

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("failure", result.status)
        self.assertEqual(2, len(result.checks))

    def test_ignores_non_verify_aci_results(self) -> None:
        capture = ArtifactCapture()
        capture.record_aci_result(
            AciResult(tool="aci_view", success=True, output="file content")
        )
        capture.record_aci_result(
            AciResult(tool="aci_search", success=False, error="not found")
        )
        quality_gate = QualityGateResult(status="pass")

        result = build_ci_status(capture, quality_gate)

        self.assertEqual("success", result.status)
        self.assertEqual(1, len(result.checks))
        self.assertEqual("skipped", result.checks[0].status)


class RenderPostmortemTest(unittest.TestCase):
    def test_renders_completed_terminal_with_all_sections(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="agent finished",
            layer="agent",
        )
        quality_gate = QualityGateResult(
            status="pass",
            checks=[QualityGateCheck(name="agent_completed", status="pass", detail="ok")],
        )
        ci_status = CiStatus(status="success")
        draft = PullRequestDraft(
            title="Test PR", branch="contribarena/test", body="test body"
        )

        output = render_postmortem(terminal, quality_gate, ci_status, draft)

        self.assertIn("# Postmortem", output)
        self.assertIn("## Terminal State", output)
        self.assertIn("- Status: completed", output)
        self.assertIn("## Contribution Gate", output)
        self.assertIn("- Status: pass", output)
        self.assertIn("## PR Lifecycle", output)
        self.assertIn("- Draft produced: True", output)
        self.assertIn("## Lesson", output)

    def test_renders_blockers_when_present(self) -> None:
        terminal = TerminalState(
            status="blocked",
            reason="quality gate blocked",
            layer="contribution",
        )
        quality_gate = QualityGateResult(
            status="block",
            blockers=["minimality: 20 file(s), 800 changed line(s)"],
            warnings=["3 workspace command(s) returned non-zero"],
        )
        ci_status = CiStatus(status="not_run")

        output = render_postmortem(terminal, quality_gate, ci_status, None)

        self.assertIn("### Blockers", output)
        self.assertIn("- minimality:", output)
        self.assertIn("### Warnings", output)
        self.assertIn("- 3 workspace", output)
        self.assertIn("- Draft produced: False", output)

    def test_lesson_guides_for_blocked_run(self) -> None:
        terminal = TerminalState(
            status="blocked",
            reason="quality gate",
            layer="contribution",
        )
        quality_gate = QualityGateResult(status="block")
        ci_status = CiStatus(status="not_run")

        output = render_postmortem(terminal, quality_gate, ci_status, None)

        self.assertIn(
            "Run stopped before PR dry-run completion",
            output,
        )

    def test_lesson_guides_for_pr_ready_run(self) -> None:
        terminal = TerminalState(
            status="completed",
            reason="agent finished",
            layer="agent",
        )
        quality_gate = QualityGateResult(status="pass")
        ci_status = CiStatus(status="success")
        draft = PullRequestDraft(
            title="Test PR", branch="contribarena/test", body="test body"
        )

        output = render_postmortem(terminal, quality_gate, ci_status, draft)

        self.assertIn(
            "Run produced a PR-ready artifact set",
            output,
        )
