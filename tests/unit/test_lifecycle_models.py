from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.lifecycle import (
    CiCheck,
    CiStatus,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    TerminalLayer,
    TerminalState,
    TerminalStatus,
)


class TerminalStateTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        state = TerminalState(status="completed", reason="done", layer="run")
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.reason, "done")
        self.assertEqual(state.layer, "run")
        self.assertEqual(state.message, "")
        self.assertIsNone(state.agent_status)
        self.assertIsNone(state.harness_status)

    def test_all_terminal_status_literals(self) -> None:
        for status in ("completed", "blocked", "failed"):
            ts: TerminalStatus = status
            state = TerminalState(status=ts, reason="x", layer="run")
            self.assertEqual(state.status, status)

    def test_all_terminal_layer_literals(self) -> None:
        valid_layers: list[TerminalLayer] = [
            "run",
            "workspace",
            "agent",
            "model_runtime",
            "budget",
            "governance",
            "contribution",
            "pr",
            "unknown",
        ]
        for layer in valid_layers:
            state = TerminalState(status="completed", reason="x", layer=layer)
            self.assertEqual(state.layer, layer)

    def test_invalid_terminal_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="invalid_status", reason="x", layer="run")

    def test_invalid_terminal_layer_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="completed", reason="x", layer="invalid_layer")

    def test_optional_fields(self) -> None:
        state = TerminalState(
            status="blocked",
            reason="timeout",
            layer="budget",
            message="Budget exceeded",
            agent_status="terminated",
            harness_status="cleanup_pending",
        )
        self.assertEqual(state.message, "Budget exceeded")
        self.assertEqual(state.agent_status, "terminated")
        self.assertEqual(state.harness_status, "cleanup_pending")

    def test_message_defaults_to_empty(self) -> None:
        state = TerminalState(status="failed", reason="crash", layer="agent")
        self.assertEqual(state.message, "")


class QualityGateCheckTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        check = QualityGateCheck(name="lint", status="pass")
        self.assertEqual(check.name, "lint")
        self.assertEqual(check.status, "pass")
        self.assertEqual(check.detail, "")

    def test_all_status_literals(self) -> None:
        for status in ("pass", "block", "warn"):
            check = QualityGateCheck(name="check", status=status)
            self.assertEqual(check.status, status)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateCheck(name="check", status="invalid")

    def test_detail_field(self) -> None:
        check = QualityGateCheck(name="test", status="warn", detail="flake8 W503")
        self.assertEqual(check.detail, "flake8 W503")


class QualityGateResultTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        result = QualityGateResult(status="pass")
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.blockers, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.checks, [])

    def test_factory_independence(self) -> None:
        r1 = QualityGateResult(status="pass")
        r2 = QualityGateResult(status="block", blockers=["lint"])
        r3 = QualityGateResult(status="fail")
        r1.blockers.append("mutated")
        self.assertNotIn("mutated", r2.blockers)
        self.assertNotIn("mutated", r3.blockers)

    def test_with_checks(self) -> None:
        check = QualityGateCheck(name="lint", status="pass")
        result = QualityGateResult(
            status="pass",
            warnings=["slow test"],
            checks=[check],
        )
        self.assertEqual(len(result.checks), 1)
        self.assertEqual(result.checks[0].name, "lint")
        self.assertEqual(result.warnings, ["slow test"])

    def test_blocked_with_blockers(self) -> None:
        result = QualityGateResult(
            status="block",
            blockers=["no tests", "lint failure"],
        )
        self.assertEqual(len(result.blockers), 2)
        self.assertEqual(result.status, "block")

    def test_all_result_status_literals(self) -> None:
        for status in ("pass", "block", "fail"):
            result = QualityGateResult(status=status)
            self.assertEqual(result.status, status)


class PullRequestDraftTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        draft = PullRequestDraft(title="Fix bug", branch="fix-bug", body="Details here")
        self.assertEqual(draft.title, "Fix bug")
        self.assertEqual(draft.branch, "fix-bug")
        self.assertEqual(draft.labels, [])
        self.assertEqual(draft.body, "Details here")

    def test_labels_factory_independence(self) -> None:
        d1 = PullRequestDraft(title="A", branch="a", body="")
        d2 = PullRequestDraft(title="B", branch="b", body="")
        d1.labels.append("bug")
        self.assertNotIn("bug", d2.labels)

    def test_with_labels(self) -> None:
        draft = PullRequestDraft(
            title="Feature",
            branch="feature-x",
            body="Adds X",
            labels=["enhancement", "needs-review"],
        )
        self.assertEqual(len(draft.labels), 2)
        self.assertIn("enhancement", draft.labels)


class CiCheckTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        check = CiCheck(name="pytest", status="success")
        self.assertEqual(check.name, "pytest")
        self.assertEqual(check.status, "success")
        self.assertEqual(check.details, "")

    def test_all_status_literals(self) -> None:
        for status in ("success", "failure", "skipped"):
            check = CiCheck(name="ci", status=status)
            self.assertEqual(check.status, status)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            CiCheck(name="ci", status="invalid")

    def test_details_field(self) -> None:
        check = CiCheck(name="pytest", status="failure", details="3 failed, 28 passed")
        self.assertEqual(check.details, "3 failed, 28 passed")


class CiStatusTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        status = CiStatus(status="not_run")
        self.assertEqual(status.status, "not_run")
        self.assertEqual(status.source, "dry_run")
        self.assertEqual(status.checks, [])

    def test_all_status_literals(self) -> None:
        for s in ("success", "failure", "not_run", "pending"):
            ci = CiStatus(status=s)
            self.assertEqual(ci.status, s)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            CiStatus(status="invalid")

    def test_source_field(self) -> None:
        ci = CiStatus(status="success", source="github_actions")
        self.assertEqual(ci.source, "github_actions")

    def test_checks_factory_independence(self) -> None:
        c1 = CiStatus(status="pending")
        c2 = CiStatus(status="pending")
        c1.checks.append(CiCheck(name="lint", status="success"))
        self.assertEqual(len(c2.checks), 0)

    def test_with_checks(self) -> None:
        ci = CiStatus(
            status="success",
            checks=[
                CiCheck(name="lint", status="success"),
                CiCheck(name="pytest", status="success"),
            ],
        )
        self.assertEqual(len(ci.checks), 2)


if __name__ == "__main__":
    unittest.main()
