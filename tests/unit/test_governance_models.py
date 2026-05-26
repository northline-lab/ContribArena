from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.governance import (
    GovernanceDecision,
    GovernancePrRef,
    GovernanceAttempt,
    MaintainerSignal,
    PrLifecycleRecord,
    GovernanceState,
)


class GovernanceDecisionTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        decision = GovernanceDecision(
            status="pass",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertEqual("pass", decision.status)
        self.assertEqual("example/repo", decision.target_repository)
        self.assertEqual("pr_open", decision.action)

    def test_default_values(self) -> None:
        decision = GovernanceDecision(
            status="block",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertIsInstance(decision.id, str)
        self.assertTrue(len(decision.id) > 0)
        self.assertEqual([], decision.reasons)
        self.assertEqual("", decision.contribution_class)
        self.assertFalse(decision.external_write)
        self.assertEqual("", decision.actor)
        self.assertIsInstance(decision.created_at, str)
        self.assertTrue(len(decision.created_at) > 0)

    def test_passed_property_true(self) -> None:
        decision = GovernanceDecision(
            status="pass",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertTrue(decision.passed)

    def test_passed_property_false(self) -> None:
        decision = GovernanceDecision(
            status="block",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertFalse(decision.passed)

    def test_explicit_values(self) -> None:
        decision = GovernanceDecision(
            status="block",
            target_repository="example/repo",
            action="pr_open",
            reasons=["governance live_enabled is false", "risk too high"],
            contribution_class="low_risk_code",
            external_write=True,
            actor="season_0:glm-5.1",
        )
        self.assertEqual("block", decision.status)
        self.assertEqual(2, len(decision.reasons))
        self.assertEqual("low_risk_code", decision.contribution_class)
        self.assertTrue(decision.external_write)
        self.assertEqual("season_0:glm-5.1", decision.actor)

    def test_accepted_status_pass(self) -> None:
        decision = GovernanceDecision(
            status="pass",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertEqual("pass", decision.status)

    def test_accepted_status_block(self) -> None:
        decision = GovernanceDecision(
            status="block",
            target_repository="example/repo",
            action="pr_open",
        )
        self.assertEqual("block", decision.status)

    def test_rejected_status_value(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(
                status="unknown",
                target_repository="example/repo",
                action="pr_open",
            )

    def test_missing_required_status(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(
                target_repository="example/repo",
                action="pr_open",
            )

    def test_missing_required_target_repository(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(
                status="pass",
                action="pr_open",
            )

    def test_missing_required_action(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceDecision(
                status="pass",
                target_repository="example/repo",
            )


class GovernancePrRefTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42)
        self.assertEqual("example/repo", ref.repository)
        self.assertEqual(42, ref.number)

    def test_default_values(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42)
        self.assertEqual("", ref.season_id)
        self.assertEqual("", ref.participant_id)
        self.assertEqual("", ref.url)
        self.assertEqual("", ref.branch)
        self.assertEqual("open", ref.state)
        self.assertIsInstance(ref.created_at, str)

    def test_explicit_values(self) -> None:
        ref = GovernancePrRef(
            season_id="season_0",
            participant_id="season_0:glm-5.1",
            repository="example/repo",
            number=42,
            url="https://github.com/example/repo/pull/42",
            branch="contribarena/test",
            state="merged",
        )
        self.assertEqual("season_0", ref.season_id)
        self.assertEqual("season_0:glm-5.1", ref.participant_id)
        self.assertEqual("https://github.com/example/repo/pull/42", ref.url)
        self.assertEqual("contribarena/test", ref.branch)
        self.assertEqual("merged", ref.state)

    def test_accepted_state_open(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42, state="open")
        self.assertEqual("open", ref.state)

    def test_accepted_state_closed(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42, state="closed")
        self.assertEqual("closed", ref.state)

    def test_accepted_state_merged(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42, state="merged")
        self.assertEqual("merged", ref.state)

    def test_rejected_state_value(self) -> None:
        with self.assertRaises(ValidationError):
            GovernancePrRef(repository="example/repo", number=42, state="draft")

    def test_missing_required_repository(self) -> None:
        with self.assertRaises(ValidationError):
            GovernancePrRef(number=42)

    def test_missing_required_number(self) -> None:
        with self.assertRaises(ValidationError):
            GovernancePrRef(repository="example/repo")


class GovernanceAttemptTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        attempt = GovernanceAttempt(
            repository="example/repo",
            action="pr_open",
            status="opened",
        )
        self.assertEqual("example/repo", attempt.repository)
        self.assertEqual("pr_open", attempt.action)
        self.assertEqual("opened", attempt.status)

    def test_default_values(self) -> None:
        attempt = GovernanceAttempt(
            repository="example/repo",
            action="pr_open",
            status="prepared",
        )
        self.assertEqual("", attempt.decision_id)
        self.assertIsInstance(attempt.created_at, str)

    def test_explicit_values(self) -> None:
        attempt = GovernanceAttempt(
            repository="example/repo",
            action="pr_open",
            status="blocked",
            decision_id="abc123def456",
        )
        self.assertEqual("abc123def456", attempt.decision_id)

    def test_accepted_status_values(self) -> None:
        for value in ("prepared", "opened", "blocked", "failed", "skipped"):
            attempt = GovernanceAttempt(
                repository="example/repo",
                action="pr_open",
                status=value,
            )
            self.assertEqual(value, attempt.status)

    def test_rejected_status_value(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(
                repository="example/repo",
                action="pr_open",
                status="unknown",
            )

    def test_missing_required_repository(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(action="pr_open", status="prepared")

    def test_missing_required_action(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(repository="example/repo", status="prepared")

    def test_missing_required_status(self) -> None:
        with self.assertRaises(ValidationError):
            GovernanceAttempt(repository="example/repo", action="pr_open")


class MaintainerSignalTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="rejection")
        self.assertEqual("example/repo", signal.repository)
        self.assertEqual("rejection", signal.kind)

    def test_default_values(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="positive")
        self.assertEqual("", signal.organization)
        self.assertEqual("medium", signal.severity)
        self.assertEqual("", signal.message)
        self.assertEqual("", signal.source)
        self.assertIsInstance(signal.created_at, str)

    def test_explicit_values(self) -> None:
        signal = MaintainerSignal(
            repository="example/repo",
            organization="example",
            kind="anti_ai_or_bot",
            severity="high",
            message="No bot contributions allowed",
            source="README.md",
        )
        self.assertEqual("example", signal.organization)
        self.assertEqual("anti_ai_or_bot", signal.kind)
        self.assertEqual("high", signal.severity)
        self.assertEqual("No bot contributions allowed", signal.message)
        self.assertEqual("README.md", signal.source)

    def test_accepted_kind_values(self) -> None:
        for value in ("rejection", "opt_out", "anti_ai_or_bot", "positive", "process_feedback", "other"):
            signal = MaintainerSignal(repository="example/repo", kind=value)
            self.assertEqual(value, signal.kind)

    def test_rejected_kind_value(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(repository="example/repo", kind="unknown")

    def test_accepted_severity_values(self) -> None:
        for value in ("low", "medium", "high"):
            signal = MaintainerSignal(repository="example/repo", kind="rejection", severity=value)
            self.assertEqual(value, signal.severity)

    def test_rejected_severity_value(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(repository="example/repo", kind="rejection", severity="critical")

    def test_missing_required_repository(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(kind="rejection")

    def test_missing_required_kind(self) -> None:
        with self.assertRaises(ValidationError):
            MaintainerSignal(repository="example/repo")


class PrLifecycleRecordTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=42)
        self.assertEqual("example/repo", record.repository)
        self.assertEqual(42, record.number)

    def test_default_values(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=42)
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
        self.assertIsInstance(record.created_at, str)

    def test_explicit_values(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="rejection", severity="high")
        record = PrLifecycleRecord(
            season_id="season_0",
            participant_id="season_0:glm-5.1",
            repository="example/repo",
            number=42,
            url="https://github.com/example/repo/pull/42",
            originating_run_dir="/workspace/runs/run-1",
            branch="contribarena/test",
            head="contribarena-bot:contribarena/test",
            base="main",
            head_sha="abc123",
            state="closed",
            lifecycle_status="merged",
            last_observed_at="2026-05-26T12:00:00Z",
            ci_status="passed",
            summary="PR merged successfully",
            maintainer_signals=[signal],
        )
        self.assertEqual("season_0", record.season_id)
        self.assertEqual("merged", record.lifecycle_status)
        self.assertEqual("passed", record.ci_status)
        self.assertEqual("PR merged successfully", record.summary)
        self.assertEqual(1, len(record.maintainer_signals))

    def test_accepted_state_values(self) -> None:
        for value in ("open", "closed", "merged"):
            record = PrLifecycleRecord(repository="example/repo", number=42, state=value)
            self.assertEqual(value, record.state)

    def test_accepted_lifecycle_status_values(self) -> None:
        for value in ("tracking", "merged", "closed", "rejected", "stale", "needs_response", "blocked", "failed"):
            record = PrLifecycleRecord(repository="example/repo", number=42, lifecycle_status=value)
            self.assertEqual(value, record.lifecycle_status)

    def test_rejected_state_value(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(repository="example/repo", number=42, state="draft")

    def test_rejected_lifecycle_status_value(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(repository="example/repo", number=42, lifecycle_status="unknown")

    def test_missing_required_repository(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(number=42)

    def test_missing_required_number(self) -> None:
        with self.assertRaises(ValidationError):
            PrLifecycleRecord(repository="example/repo")


class GovernanceStateTest(unittest.TestCase):
    def test_default_values(self) -> None:
        state = GovernanceState()
        self.assertEqual([], state.attempts)
        self.assertEqual([], state.pull_requests)
        self.assertEqual([], state.lifecycle_records)
        self.assertEqual([], state.maintainer_signals)

    def test_default_factory_independence(self) -> None:
        state1 = GovernanceState()
        state2 = GovernanceState()
        state1.attempts.append(GovernanceAttempt(repository="a/repo", action="pr_open", status="prepared"))
        self.assertEqual(0, len(state2.attempts))

    def test_with_attempts(self) -> None:
        attempt = GovernanceAttempt(repository="example/repo", action="pr_open", status="opened")
        state = GovernanceState(attempts=[attempt])
        self.assertEqual(1, len(state.attempts))
        self.assertEqual("opened", state.attempts[0].status)

    def test_with_pull_requests(self) -> None:
        ref = GovernancePrRef(repository="example/repo", number=42, url="https://github.com/example/repo/pull/42")
        state = GovernanceState(pull_requests=[ref])
        self.assertEqual(1, len(state.pull_requests))
        self.assertEqual(42, state.pull_requests[0].number)

    def test_with_lifecycle_records(self) -> None:
        record = PrLifecycleRecord(repository="example/repo", number=42)
        state = GovernanceState(lifecycle_records=[record])
        self.assertEqual(1, len(state.lifecycle_records))
        self.assertEqual("tracking", state.lifecycle_records[0].lifecycle_status)

    def test_with_maintainer_signals(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="rejection")
        state = GovernanceState(maintainer_signals=[signal])
        self.assertEqual(1, len(state.maintainer_signals))
        self.assertEqual("rejection", state.maintainer_signals[0].kind)

    def test_full_state(self) -> None:
        attempt = GovernanceAttempt(repository="example/repo", action="pr_open", status="opened")
        ref = GovernancePrRef(repository="example/repo", number=42, url="https://github.com/example/repo/pull/42")
        record = PrLifecycleRecord(repository="example/repo", number=42, lifecycle_status="merged")
        signal = MaintainerSignal(repository="example/repo", kind="positive", severity="low")
        state = GovernanceState(
            attempts=[attempt],
            pull_requests=[ref],
            lifecycle_records=[record],
            maintainer_signals=[signal],
        )
        self.assertEqual(1, len(state.attempts))
        self.assertEqual(1, len(state.pull_requests))
        self.assertEqual(1, len(state.lifecycle_records))
        self.assertEqual(1, len(state.maintainer_signals))

    def test_multiple_lifecycle_records(self) -> None:
        records = [
            PrLifecycleRecord(repository="a/repo", number=1),
            PrLifecycleRecord(repository="b/repo", number=2),
        ]
        state = GovernanceState(lifecycle_records=records)
        self.assertEqual(2, len(state.lifecycle_records))


if __name__ == "__main__":
    unittest.main()
