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
    def test_required_fields(self) -> None:
        t = TerminalState(status="completed", reason="done", layer="run")
        self.assertEqual("completed", t.status)
        self.assertEqual("done", t.reason)
        self.assertEqual("run", t.layer)

    def test_defaults(self) -> None:
        t = TerminalState(status="blocked", reason="budget", layer="budget")
        self.assertEqual("", t.message)
        self.assertIsNone(t.agent_status)
        self.assertIsNone(t.harness_status)

    def test_all_status_literals(self) -> None:
        for status in ("completed", "blocked", "failed"):
            t = TerminalState(status=status, reason="r", layer="run")
            self.assertEqual(status, t.status)

    def test_all_layer_literals(self) -> None:
        layers = ("run", "workspace", "agent", "model_runtime", "budget",
                  "governance", "contribution", "pr", "unknown")
        for layer in layers:
            t = TerminalState(status="completed", reason="r", layer=layer)
            self.assertEqual(layer, t.layer)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="invalid", reason="r", layer="run")

    def test_invalid_layer_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="completed", reason="r", layer="invalid")

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            TerminalState(status="completed", reason="r")  # type: ignore[call-arg]


class QualityGateCheckTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        q = QualityGateCheck(name="ruff", status="pass")
        self.assertEqual("ruff", q.name)
        self.assertEqual("pass", q.status)

    def test_default_detail(self) -> None:
        q = QualityGateCheck(name="ruff", status="warn")
        self.assertEqual("", q.detail)

    def test_all_status_literals(self) -> None:
        for status in ("pass", "block", "warn"):
            q = QualityGateCheck(name="c", status=status)
            self.assertEqual(status, q.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateCheck(name="c", status="fail")


class QualityGateResultTest(unittest.TestCase):
    def test_required_status(self) -> None:
        r = QualityGateResult(status="pass")
        self.assertEqual("pass", r.status)

    def test_default_factory_independence(self) -> None:
        r1 = QualityGateResult(status="pass")
        r2 = QualityGateResult(status="block")
        r1.blockers.append("x")
        self.assertEqual([], r2.blockers)

    def test_all_status_literals(self) -> None:
        for status in ("pass", "block", "fail"):
            r = QualityGateResult(status=status)
            self.assertEqual(status, r.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QualityGateResult(status="warn")

    def test_full_construction(self) -> None:
        check = QualityGateCheck(name="ruff", status="pass", detail="ok")
        r = QualityGateResult(
            status="block",
            blockers=["missing tests"],
            warnings=["slow"],
            checks=[check],
        )
        self.assertEqual(["missing tests"], r.blockers)
        self.assertEqual(["slow"], r.warnings)
        self.assertEqual(1, len(r.checks))
        self.assertEqual("ruff", r.checks[0].name)


class PullRequestDraftTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        p = PullRequestDraft(title="fix: typo", branch="fix/typo", body="desc")
        self.assertEqual("fix: typo", p.title)
        self.assertEqual("fix/typo", p.branch)
        self.assertEqual("desc", p.body)

    def test_default_labels(self) -> None:
        p = PullRequestDraft(title="t", branch="b", body="d")
        self.assertEqual([], p.labels)

    def test_labels_factory_independence(self) -> None:
        p1 = PullRequestDraft(title="t", branch="b", body="d")
        p2 = PullRequestDraft(title="t", branch="b", body="d")
        p1.labels.append("bug")
        self.assertEqual([], p2.labels)

    def test_missing_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            PullRequestDraft(title="t", branch="b")  # type: ignore[call-arg]


class CiCheckTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        c = CiCheck(name="pytest", status="success")
        self.assertEqual("pytest", c.name)
        self.assertEqual("success", c.status)

    def test_default_details(self) -> None:
        c = CiCheck(name="pytest", status="skipped")
        self.assertEqual("", c.details)

    def test_all_status_literals(self) -> None:
        for status in ("success", "failure", "skipped"):
            c = CiCheck(name="c", status=status)
            self.assertEqual(status, c.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CiCheck(name="c", status="pending")


class CiStatusTest(unittest.TestCase):
    def test_required_status(self) -> None:
        s = CiStatus(status="success")
        self.assertEqual("success", s.status)

    def test_defaults(self) -> None:
        s = CiStatus(status="not_run")
        self.assertEqual("dry_run", s.source)
        self.assertEqual([], s.checks)

    def test_all_status_literals(self) -> None:
        for status in ("success", "failure", "not_run", "pending"):
            s = CiStatus(status=status)
            self.assertEqual(status, s.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CiStatus(status="skipped")

    def test_checks_factory_independence(self) -> None:
        s1 = CiStatus(status="success")
        s2 = CiStatus(status="failure")
        s1.checks.append(CiCheck(name="c", status="success"))
        self.assertEqual([], s2.checks)

    def test_full_construction(self) -> None:
        check = CiCheck(name="pytest", status="success", details="3 passed")
        s = CiStatus(status="success", source="ci", checks=[check])
        self.assertEqual("ci", s.source)
        self.assertEqual(1, len(s.checks))
        self.assertEqual("pytest", s.checks[0].name)


if __name__ == "__main__":
    unittest.main()
