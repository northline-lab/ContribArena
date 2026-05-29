from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.models import (
    GovernanceAttempt,
    GovernanceDecision,
    GovernancePrRef,
    GovernanceState,
    MaintainerSignal,
    PrLifecycleRecord,
)
from contribarena.models.governance import GovernanceDecisionStatus


class GovernanceDecisionTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        decision = GovernanceDecision(
            status="pass",
            target_repository="example/repo",
            action="open_pr",
        )
        self.assertEqual("pass", decision.status)
        self.assertEqual("example/repo", decision.target_repository)
        self.assertEqual("open_pr", decision.action)
        self.assertEqual([], decision.reasons)
        self.assertEqual("", decision.contribution_class)
        self.assertFalse(decision.external_write)
        self.assertEqual("", decision.actor)
        self.assertTrue(decision.id)
        self.assertTrue(decision.created_at)

    def test_id_is_unique_per_instance(self) -> None:
        first = GovernanceDecision(
            status="pass", target_repository="a/b", action="open_pr"
        )
        second = GovernanceDecision(
            status="pass", target_repository="a/b", action="open_pr"
        )
        self.assertNotEqual(first.id, second.id)

    def test_passed_property(self) -> None:
        passing = GovernanceDecision(
            status="pass", target_repository="a/b", action="open_pr"
        )
        blocking = GovernanceDecision(
            status="block", target_repository="a/b", action="open_pr"
        )
        self.assertTrue(passing.passed)
        self.assertFalse(blocking.passed)

    def test_all_status_literals_accepted(self) -> None:
        for value in get_args(GovernanceDecisionStatus):
            GovernanceDecision(
                status=value, target_repository="a/b", action="open_pr"
            )

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(
                status="maybe",  # type: ignore[arg-type]
                target_repository="a/b",
                action="open_pr",
            )

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(status="pass", action="open_pr")  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            GovernanceDecision(status="pass", target_repository="a/b")  # type: ignore[call-arg]

    def test_reasons_factory_independence(self) -> None:
        first = GovernanceDecision(
            status="block", target_repository="a/b", action="open_pr"
        )
        first.reasons.append("r1")
        second = GovernanceDecision(
            status="block", target_repository="a/b", action="open_pr"
        )
        self.assertEqual([], second.reasons)

    def test_explicit_values(self) -> None:
        decision = GovernanceDecision(
            id="fixed-id",
            status="block",
            reasons=["r1", "r2"],
            target_repository="example/repo",
            action="open_pr",
            contribution_class="low_risk_code",
            external_write=True,
            actor="contribarena-bot",
            created_at="2026-05-29T00:00:00+00:00",
        )
        self.assertEqual("fixed-id", decision.id)
        self.assertEqual(["r1", "r2"], decision.reasons)
        self.assertEqual("low_risk_code", decision.contribution_class)
        self.assertTrue(decision.external_write)
        self.assertEqual("contribarena-bot", decision.actor)
        self.assertEqual("2026-05-29T00:00:00+00:00", decision.created_at)


class GovernancePrRefTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=1)
        self.assertEqual("example/repo", ref.repository)
        self.assertEqual(1, ref.number)
        self.assertEqual("", ref.season_id)
        self.assertEqual("", ref.participant_id)
        self.assertEqual("", ref.url)
        self.assertEqual("", ref.branch)
        self.assertEqual("open", ref.state)
        self.assertTrue(ref.created_at)

    def test_all_state_literals_accepted(self) -> None:
        for value in ("open", "closed", "merged"):
            GovernancePrRef(repository="a/b", number=1, state=value)

    def test_invalid_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GovernancePrRef(
                repository="a/b", number=1, state="draft"  # type: ignore[arg-type]
            )

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GovernancePrRef(number=1)  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            GovernancePrRef(repository="a/b")  # type: ignore[call-arg]


class GovernanceAttemptTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        attempt = GovernanceAttempt(
            repository="example/repo", action="open_pr", status="opened"
        )
        self.assertEqual("example/repo", attempt.repository)
        self.assertEqual("open_pr", attempt.action)
        self.assertEqual("opened", attempt.status)
        self.assertEqual("", attempt.decision_id)
        self.assertTrue(attempt.created_at)

    def test_all_status_literals_accepted(self) -> None:
        for value in ("prepared", "opened", "blocked", "failed", "skipped"):
            GovernanceAttempt(repository="a/b", action="open_pr", status=value)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(
                repository="a/b",
                action="open_pr",
                status="unknown",  # type: ignore[arg-type]
            )

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(action="open_pr", status="opened")  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            GovernanceAttempt(repository="a/b", status="opened")  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            GovernanceAttempt(repository="a/b", action="open_pr")  # type: ignore[call-arg]


class MaintainerSignalTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="rejection")
        self.assertEqual("example/repo", signal.repository)
        self.assertEqual("rejection", signal.kind)
        self.assertEqual("", signal.organization)
        self.assertEqual("medium", signal.severity)
        self.assertEqual("", signal.message)
        self.assertEqual("", signal.source)
        self.assertTrue(signal.created_at)

    def test_all_kind_literals_accepted(self) -> None:
        for value in (
            "rejection",
            "opt_out",
            "anti_ai_or_bot",
            "positive",
            "process_feedback",
            "other",
        ):
            MaintainerSignal(repository="a/b", kind=value)

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(repository="a/b", kind="praise")  # type: ignore[arg-type]

    def test_all_severity_literals_accepted(self) -> None:
        for value in ("low", "medium", "high"):
            MaintainerSignal(repository="a/b", kind="rejection", severity=value)

    def test_invalid_severity_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(
                repository="a/b",
                kind="rejection",
                severity="critical",  # type: ignore[arg-type]
            )

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(kind="rejection")  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            MaintainerSignal(repository="a/b")  # type: ignore[call-arg]


class PrLifecycleRecordTest(unittest.TestCase):
    def test_minimal_required_fields(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=1)
        self.assertEqual("example/repo", record.repository)
        self.assertEqual(1, record.number)
        self.assertEqual("", record.season_id)
        self.assertEqual("", record.participant_id)
        self.assertEqual("", record.url)
        self.assertEqual("", record.originating_run_dir)
        self.assertEqual("", record.branch)
        self.assertEqual("", record.head)
        self.assertEqual("main", record.base)
        self.assertEqual("", record.head_sha)
        self.assertEqual("open", record.state)
        self.assertEqual("tracking", record.lifecycle_status)
        self.assertEqual("", record.last_observed_at)
        self.assertEqual("", record.last_poll_at)
        self.assertEqual("", record.next_poll_at)
        self.assertEqual(0, record.lifecycle_retry_count)
        self.assertEqual("not_run", record.ci_status)
        self.assertEqual("", record.review_cursor)
        self.assertEqual("", record.comment_cursor)
        self.assertEqual("", record.summary)
        self.assertEqual([], record.maintainer_signals)
        self.assertTrue(record.created_at)

    def test_all_state_literals_accepted(self) -> None:
        for value in ("open", "closed", "merged"):
            PrLifecycleRecord(repository="a/b", number=1, state=value)

    def test_invalid_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(
                repository="a/b", number=1, state="draft"  # type: ignore[arg-type]
            )

    def test_all_lifecycle_status_literals_accepted(self) -> None:
        for value in (
            "tracking",
            "merged",
            "closed",
            "rejected",
            "stale",
            "needs_response",
            "blocked",
            "failed",
        ):
            PrLifecycleRecord(
                repository="a/b", number=1, lifecycle_status=value
            )

    def test_invalid_lifecycle_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(
                repository="a/b",
                number=1,
                lifecycle_status="pending",  # type: ignore[arg-type]
            )

    def test_missing_required_field_raises(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(number=1)  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(repository="a/b")  # type: ignore[call-arg]

    def test_maintainer_signals_factory_independence(self) -> None:
        first = PrLifecycleRecord(repository="a/b", number=1)
        first.maintainer_signals.append(
            MaintainerSignal(repository="a/b", kind="rejection")
        )
        second = PrLifecycleRecord(repository="a/b", number=2)
        self.assertEqual([], second.maintainer_signals)

    def test_nested_maintainer_signal_composition(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo", kind="positive", severity="high"
        )
        record = PrLifecycleRecord(
            repository="example/repo",
            number=42,
            maintainer_signals=[signal],
        )
        self.assertEqual(1, len(record.maintainer_signals))
        self.assertEqual("positive", record.maintainer_signals[0].kind)
        self.assertEqual("high", record.maintainer_signals[0].severity)


class GovernanceStateTest(unittest.TestCase):
    def test_defaults_are_empty(self) -> None:
        state = GovernanceState()
        self.assertEqual([], state.attempts)
        self.assertEqual([], state.pull_requests)
        self.assertEqual([], state.lifecycle_records)
        self.assertEqual([], state.maintainer_signals)

    def test_factory_independence_across_instances(self) -> None:
        first = GovernanceState()
        first.attempts.append(
            GovernanceAttempt(
                repository="a/b", action="open_pr", status="opened"
            )
        )
        first.pull_requests.append(GovernancePrRef(repository="a/b", number=1))
        first.lifecycle_records.append(
            PrLifecycleRecord(repository="a/b", number=1)
        )
        first.maintainer_signals.append(
            MaintainerSignal(repository="a/b", kind="rejection")
        )
        second = GovernanceState()
        self.assertEqual([], second.attempts)
        self.assertEqual([], second.pull_requests)
        self.assertEqual([], second.lifecycle_records)
        self.assertEqual([], second.maintainer_signals)

    def test_explicit_composition(self) -> None:
        state = GovernanceState(
            attempts=[
                GovernanceAttempt(
                    repository="a/b", action="open_pr", status="prepared"
                )
            ],
            pull_requests=[GovernancePrRef(repository="a/b", number=5)],
            lifecycle_records=[PrLifecycleRecord(repository="a/b", number=5)],
            maintainer_signals=[
                MaintainerSignal(repository="a/b", kind="process_feedback")
            ],
        )
        self.assertEqual(1, len(state.attempts))
        self.assertEqual("prepared", state.attempts[0].status)
        self.assertEqual(5, state.pull_requests[0].number)
        self.assertEqual(5, state.lifecycle_records[0].number)
        self.assertEqual(
            "process_feedback", state.maintainer_signals[0].kind
        )


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
