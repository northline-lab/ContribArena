from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.lifecycle import (
    CiCheck,
    CiStatus,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    TerminalState,
)


class TerminalStateTest(unittest.TestCase):
    """TerminalState records the final status of a run."""

    def test_required_fields(self) -> None:
        ts = TerminalState(status="completed", reason="done", layer="run")
        self.assertEqual("completed", ts.status)
        self.assertEqual("done", ts.reason)
        self.assertEqual("run", ts.layer)

    def test_defaults(self) -> None:
        ts = TerminalState(status="failed", reason="error", layer="agent")
        self.assertEqual("", ts.message)
        self.assertIsNone(ts.agent_status)
        self.assertIsNone(ts.harness_status)

    def test_explicit_optional_values(self) -> None:
        ts = TerminalState(
            status="blocked",
            reason="governance",
            layer="governance",
            message="blocked by quality gate",
            agent_status="stopped",
            harness_status="terminated",
        )
        self.assertEqual("blocked by quality gate", ts.message)
        self.assertEqual("stopped", ts.agent_status)
        self.assertEqual("terminated", ts.harness_status)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("completed", "blocked", "failed"):
            ts = TerminalState(status=status, reason="x", layer="run")
            self.assertEqual(status, ts.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="running", reason="x", layer="run")

    def test_all_layer_literals_accepted(self) -> None:
        for layer in (
            "run",
            "workspace",
            "agent",
            "model_runtime",
            "budget",
            "governance",
            "contribution",
            "pr",
            "unknown",
        ):
            ts = TerminalState(status="failed", reason="x", layer=layer)
            self.assertEqual(layer, ts.layer)

    def test_invalid_layer_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="failed", reason="x", layer="infrastructure")

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="completed", reason="done")  # type: ignore[call-arg]


class QualityGateCheckTest(unittest.TestCase):
    """QualityGateCheck represents a single gate check result."""

    def test_required_fields(self) -> None:
        qgc = QualityGateCheck(name="no_secrets", status="pass")
        self.assertEqual("no_secrets", qgc.name)
        self.assertEqual("pass", qgc.status)

    def test_defaults(self) -> None:
        qgc = QualityGateCheck(name="lint", status="warn")
        self.assertEqual("", qgc.detail)

    def test_explicit_values(self) -> None:
        qgc = QualityGateCheck(name="tests", status="block", detail="3 tests failed")
        self.assertEqual("3 tests failed", qgc.detail)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("pass", "block", "warn"):
            qgc = QualityGateCheck(name="x", status=status)
            self.assertEqual(status, qgc.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateCheck(name="x", status="fail")

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateCheck(name="x")  # type: ignore[call-arg]


class QualityGateResultTest(unittest.TestCase):
    """QualityGateResult aggregates multiple gate checks."""

    def test_required_fields(self) -> None:
        qgr = QualityGateResult(status="pass")
        self.assertEqual("pass", qgr.status)

    def test_defaults(self) -> None:
        qgr = QualityGateResult(status="pass")
        self.assertEqual([], qgr.blockers)
        self.assertEqual([], qgr.warnings)
        self.assertEqual([], qgr.checks)

    def test_explicit_values(self) -> None:
        check = QualityGateCheck(name="secrets", status="pass")
        qgr = QualityGateResult(
            status="block",
            blockers=["no_secrets"],
            warnings=["slow_tests"],
            checks=[check],
        )
        self.assertEqual("block", qgr.status)
        self.assertEqual(["no_secrets"], qgr.blockers)
        self.assertEqual(["slow_tests"], qgr.warnings)
        self.assertEqual([check], qgr.checks)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("pass", "block", "fail"):
            qgr = QualityGateResult(status=status)
            self.assertEqual(status, qgr.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateResult(status="warn")

    def test_list_factory_independence(self) -> None:
        q1 = QualityGateResult(status="pass")
        q2 = QualityGateResult(status="pass")
        q1.blockers.append("x")
        q1.warnings.append("y")
        q1.checks.append(QualityGateCheck(name="z", status="pass"))
        self.assertEqual([], q2.blockers)
        self.assertEqual([], q2.warnings)
        self.assertEqual([], q2.checks)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateResult()  # type: ignore[call-arg]


class PullRequestDraftTest(unittest.TestCase):
    """PullRequestDraft holds the proposed PR metadata."""

    def test_required_fields(self) -> None:
        prd = PullRequestDraft(title="Fix bug", branch="fix-1", body="Description")
        self.assertEqual("Fix bug", prd.title)
        self.assertEqual("fix-1", prd.branch)
        self.assertEqual("Description", prd.body)

    def test_defaults(self) -> None:
        prd = PullRequestDraft(title="Fix bug", branch="fix-1", body="Desc")
        self.assertEqual([], prd.labels)

    def test_explicit_values(self) -> None:
        prd = PullRequestDraft(
            title="Fix bug",
            branch="fix-1",
            body="Desc",
            labels=["bugfix", "low_risk"],
        )
        self.assertEqual(["bugfix", "low_risk"], prd.labels)

    def test_labels_factory_independence(self) -> None:
        p1 = PullRequestDraft(title="a", branch="b", body="c")
        p2 = PullRequestDraft(title="a", branch="b", body="c")
        p1.labels.append("x")
        self.assertEqual([], p2.labels)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            PullRequestDraft(title="a", branch="b")  # type: ignore[call-arg]


class CiCheckTest(unittest.TestCase):
    """CiCheck records a single CI check outcome."""

    def test_required_fields(self) -> None:
        cc = CiCheck(name="pytest", status="success")
        self.assertEqual("pytest", cc.name)
        self.assertEqual("success", cc.status)

    def test_defaults(self) -> None:
        cc = CiCheck(name="pytest", status="failure")
        self.assertEqual("", cc.details)

    def test_explicit_values(self) -> None:
        cc = CiCheck(name="lint", status="skipped", details="not applicable")
        self.assertEqual("not applicable", cc.details)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("success", "failure", "skipped"):
            cc = CiCheck(name="x", status=status)
            self.assertEqual(status, cc.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CiCheck(name="x", status="pending")

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            CiCheck(name="x")  # type: ignore[call-arg]


class CiStatusTest(unittest.TestCase):
    """CiStatus aggregates CI check results for a PR."""

    def test_required_fields(self) -> None:
        cs = CiStatus(status="success")
        self.assertEqual("success", cs.status)

    def test_defaults(self) -> None:
        cs = CiStatus(status="success")
        self.assertEqual("dry_run", cs.source)
        self.assertEqual([], cs.checks)

    def test_explicit_values(self) -> None:
        check = CiCheck(name="pytest", status="success")
        cs = CiStatus(
            status="failure",
            source="github_actions",
            checks=[check],
        )
        self.assertEqual("failure", cs.status)
        self.assertEqual("github_actions", cs.source)
        self.assertEqual([check], cs.checks)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("success", "failure", "not_run", "pending"):
            cs = CiStatus(status=status)
            self.assertEqual(status, cs.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CiStatus(status="running")

    def test_checks_factory_independence(self) -> None:
        c1 = CiStatus(status="success")
        c2 = CiStatus(status="success")
        c1.checks.append(CiCheck(name="x", status="success"))
        self.assertEqual([], c2.checks)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            CiStatus()  # type: ignore[call-arg]


class LifecycleImportTest(unittest.TestCase):
    """All lifecycle models are importable from the models package."""

    def test_terminal_state_importable_from_models(self) -> None:
        from contribarena.models import TerminalState

        self.assertIs(TerminalState, TerminalState)

    def test_quality_gate_result_importable_from_models(self) -> None:
        from contribarena.models import QualityGateResult

        self.assertIs(QualityGateResult, QualityGateResult)

    def test_pull_request_draft_importable_from_models(self) -> None:
        from contribarena.models import PullRequestDraft

        self.assertIs(PullRequestDraft, PullRequestDraft)

    def test_ci_status_importable_from_models(self) -> None:
        from contribarena.models import CiStatus

        self.assertIs(CiStatus, CiStatus)

    def test_ci_check_importable_from_models(self) -> None:
        from contribarena.models import CiCheck

        self.assertIs(CiCheck, CiCheck)

    def test_quality_gate_check_importable_from_models(self) -> None:
        from contribarena.models import QualityGateCheck

        self.assertIs(QualityGateCheck, QualityGateCheck)
