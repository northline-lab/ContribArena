from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from contribarena.engine.external_lifecycle import (
    _classify_status,
    _github_state,
    _has_rejection_signal,
    _has_requested_changes,
    _iso,
    _parse,
    _summary_for_status,
    lifecycle_record_due,
    lifecycle_record_for_opened_pr,
    mark_lifecycle_observation_failed,
)
from contribarena.models import CiStatus, MaintainerSignal, PrLifecycleRecord
from contribarena.tools.github_pr import PullRequestStatusResult


class GithubStateTest(unittest.TestCase):
    def test_none_pr_status_returns_open(self) -> None:
        self.assertEqual("open", _github_state(None))

    def test_failed_pr_status_returns_open(self) -> None:
        failed = PullRequestStatusResult(ok=False, number=42, error="timeout")
        self.assertEqual("open", _github_state(failed))

    def test_merged_pr_returns_merged(self) -> None:
        merged = PullRequestStatusResult(ok=True, number=42, merged=True)
        self.assertEqual("merged", _github_state(merged))

    def test_closed_pr_returns_closed(self) -> None:
        closed = PullRequestStatusResult(ok=True, number=42, state="closed", merged=False)
        self.assertEqual("closed", _github_state(closed))

    def test_open_pr_returns_open(self) -> None:
        pr_open = PullRequestStatusResult(ok=True, number=42, state="open", merged=False)
        self.assertEqual("open", _github_state(pr_open))


class HasRequestedChangesTest(unittest.TestCase):
    def test_empty_reviews_returns_false(self) -> None:
        self.assertFalse(_has_requested_changes([]))

    def test_approved_review_returns_false(self) -> None:
        review = MagicMock(state="APPROVED")
        self.assertFalse(_has_requested_changes([review]))

    def test_changes_requested_review_returns_true(self) -> None:
        review = MagicMock(state="CHANGES_REQUESTED")
        self.assertTrue(_has_requested_changes([review]))

    def test_mixed_reviews_with_one_changes_requested(self) -> None:
        approved = MagicMock(state="APPROVED")
        changes = MagicMock(state="CHANGES_REQUESTED")
        commented = MagicMock(state="COMMENTED")
        self.assertTrue(_has_requested_changes([approved, changes, commented]))

    def test_object_without_state_returns_false(self) -> None:
        review = MagicMock(spec=[])
        self.assertFalse(_has_requested_changes([review]))


class HasRejectionSignalTest(unittest.TestCase):
    def test_empty_signals_returns_false(self) -> None:
        self.assertFalse(_has_rejection_signal([]))

    def test_process_feedback_signal_returns_false(self) -> None:
        signal = MaintainerSignal(
            repository="owner/repo",
            kind="process_feedback",
            severity="medium",
            source="pr#1",
        )
        self.assertFalse(_has_rejection_signal([signal]))

    def test_rejection_signal_returns_true(self) -> None:
        signal = MaintainerSignal(
            repository="owner/repo",
            kind="rejection",
            severity="high",
            source="pr#1",
        )
        self.assertTrue(_has_rejection_signal([signal]))

    def test_opt_out_signal_returns_true(self) -> None:
        signal = MaintainerSignal(
            repository="owner/repo",
            kind="opt_out",
            severity="high",
            source="pr#1",
        )
        self.assertTrue(_has_rejection_signal([signal]))

    def test_anti_ai_or_bot_signal_returns_true(self) -> None:
        signal = MaintainerSignal(
            repository="owner/repo",
            kind="anti_ai_or_bot",
            severity="high",
            source="pr#1",
        )
        self.assertTrue(_has_rejection_signal([signal]))


class SummaryForStatusTest(unittest.TestCase):
    def test_merged(self) -> None:
        self.assertEqual("external PR was merged", _summary_for_status("merged"))

    def test_closed(self) -> None:
        self.assertEqual("external PR was closed", _summary_for_status("closed"))

    def test_rejected(self) -> None:
        self.assertEqual(
            "external PR has maintainer rejection signal",
            _summary_for_status("rejected"),
        )

    def test_needs_response(self) -> None:
        self.assertEqual(
            "external PR needs agent follow-up",
            _summary_for_status("needs_response"),
        )

    def test_failed(self) -> None:
        self.assertEqual(
            "external PR lifecycle observation failed",
            _summary_for_status("failed"),
        )

    def test_unknown_status_defaults_to_tracking(self) -> None:
        self.assertEqual(
            "external PR remains open and tracked",
            _summary_for_status("unknown"),
        )


class ParseTest(unittest.TestCase):
    def test_zulu_timestamp(self) -> None:
        result = _parse("2026-05-23T12:00:00Z")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(2026, result.year)

    def test_offset_timestamp(self) -> None:
        result = _parse("2026-05-23T12:00:00+05:00")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(7, result.hour)

    def test_naive_timestamp_gets_utc(self) -> None:
        result = _parse("2026-05-23T12:00:00")
        self.assertEqual(UTC, result.tzinfo)


class IsoTest(unittest.TestCase):
    def test_utc_datetime(self) -> None:
        dt = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
        result = _iso(dt)
        self.assertEqual("2026-05-23T12:00:00+00:00", result)

    def test_naive_datetime_gets_utc(self) -> None:
        dt = datetime(2026, 5, 23, 12, 0, 0)
        result = _iso(dt)
        self.assertEqual("2026-05-23T12:00:00+00:00", result)

    def test_non_utc_datetime_converts(self) -> None:
        import zoneinfo

        eastern = datetime(2026, 5, 23, 8, 0, 0, tzinfo=zoneinfo.ZoneInfo("US/Eastern"))
        self.assertEqual("2026-05-23T12:00:00+00:00", _iso(eastern))


class LifecycleRecordDueTest(unittest.TestCase):
    def _record(self, *, lifecycle_status: str = "tracking", next_poll_at: str = "") -> PrLifecycleRecord:
        return PrLifecycleRecord(
            repository="owner/repo",
            number=1,
            lifecycle_status=lifecycle_status,
            next_poll_at=next_poll_at,
        )

    def test_merged_record_not_due(self) -> None:
        self.assertFalse(lifecycle_record_due(self._record(lifecycle_status="merged")))

    def test_closed_record_not_due(self) -> None:
        self.assertFalse(lifecycle_record_due(self._record(lifecycle_status="closed")))

    def test_rejected_record_not_due(self) -> None:
        self.assertFalse(lifecycle_record_due(self._record(lifecycle_status="rejected")))

    def test_failed_record_not_due(self) -> None:
        self.assertFalse(lifecycle_record_due(self._record(lifecycle_status="failed")))

    def test_empty_next_poll_at_is_due(self) -> None:
        self.assertTrue(lifecycle_record_due(self._record(next_poll_at="")))

    def test_past_next_poll_at_is_due(self) -> None:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.assertTrue(lifecycle_record_due(self._record(next_poll_at=past)))

    def test_future_next_poll_at_not_due(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        self.assertFalse(lifecycle_record_due(self._record(next_poll_at=future)))


class MarkLifecycleObservationFailedTest(unittest.TestCase):
    def _record(self, **kwargs: object) -> PrLifecycleRecord:
        defaults = {
            "repository": "owner/repo",
            "number": 1,
            "lifecycle_retry_count": 0,
            "summary": "external PR remains open and tracked",
        }
        defaults.update(kwargs)
        return PrLifecycleRecord(**defaults)  # type: ignore[arg-type]

    def test_first_failure_increments_retry_count(self) -> None:
        record = self._record()
        result = mark_lifecycle_observation_failed(
            record=record, error="connection reset", poll_interval_seconds=60
        )
        self.assertEqual(1, result.lifecycle_retry_count)
        self.assertIn("connection reset", result.summary)

    def test_second_failure_backs_off(self) -> None:
        record = self._record(lifecycle_retry_count=1)
        result = mark_lifecycle_observation_failed(
            record=record, error="timeout", poll_interval_seconds=60
        )
        self.assertEqual(2, result.lifecycle_retry_count)

    def test_future_next_poll_at_set(self) -> None:
        record = self._record()
        result = mark_lifecycle_observation_failed(
            record=record, error="503", poll_interval_seconds=60
        )
        next_poll = _parse(result.next_poll_at)
        self.assertGreater(next_poll, datetime.now(UTC))

    def test_summary_truncates_long_error(self) -> None:
        record = self._record()
        long_error = "x" * 300
        result = mark_lifecycle_observation_failed(
            record=record, error=long_error, poll_interval_seconds=60
        )
        self.assertIn("xxx", result.summary)
        # summary = "external PR lifecycle observation failed transiently: " + error[:160]
        # prefix is ~50 chars, so total is ~210
        self.assertLessEqual(len(result.summary), 220)


class ClassifyStatusTest(unittest.TestCase):
    def _record(self, **kwargs: object) -> PrLifecycleRecord:
        defaults: dict[str, object] = {
            "repository": "owner/repo",
            "number": 1,
        }
        defaults.update(kwargs)
        return PrLifecycleRecord(**defaults)  # type: ignore[arg-type]

    def test_pr_not_ok_returns_failed(self) -> None:
        pr = PullRequestStatusResult(ok=False, number=1, error="timeout")
        self.assertEqual("failed", _classify_status(self._record(), pr, None, []))

    def test_merged_pr_returns_merged(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, merged=True)
        self.assertEqual("merged", _classify_status(self._record(), pr, None, []))

    def test_closed_pr_returns_closed(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, state="closed", merged=False)
        self.assertEqual("closed", _classify_status(self._record(), pr, None, []))

    def test_rejection_signal_returns_rejected(self) -> None:
        signal = MaintainerSignal(
            repository="owner/repo", kind="rejection", severity="high", source="pr#1"
        )
        record = self._record(maintainer_signals=[signal])
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("rejected", _classify_status(record, pr, None, []))

    def test_changes_requested_returns_needs_response(self) -> None:
        review = MagicMock(state="CHANGES_REQUESTED")
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual(
            "needs_response", _classify_status(self._record(), pr, None, [review])
        )

    def test_ci_failure_returns_needs_response(self) -> None:
        ci = CiStatus(status="failure", source="dry_run")
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("needs_response", _classify_status(self._record(), pr, ci, []))

    def test_open_pr_no_issues_returns_tracking(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("tracking", _classify_status(self._record(), pr, None, []))


class LifecycleRecordForOpenedPrTest(unittest.TestCase):
    def test_basic_record_creation(self) -> None:
        record = lifecycle_record_for_opened_pr(
            repository="owner/repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
            branch="feature/x",
            head="northline-lab:feature/x",
            base="main",
            head_sha="abc123",
            ci_status=None,
            poll_interval_seconds=21600,
        )
        self.assertEqual("owner/repo", record.repository)
        self.assertEqual(1, record.number)
        self.assertEqual("tracking", record.lifecycle_status)
        self.assertEqual("open", record.state)
        self.assertEqual("not_run", record.ci_status)

    def test_record_with_ci_status(self) -> None:
        ci = CiStatus(status="pending", source="dry_run")
        record = lifecycle_record_for_opened_pr(
            repository="owner/repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
            branch="feature/x",
            head="northline-lab:feature/x",
            base="main",
            head_sha="abc123",
            ci_status=ci,
            poll_interval_seconds=21600,
        )
        self.assertEqual("pending", record.ci_status)

    def test_initial_poll_delay_used(self) -> None:
        now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
        record = lifecycle_record_for_opened_pr(
            repository="owner/repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
            branch="feature/x",
            head="northline-lab:feature/x",
            base="main",
            head_sha="abc123",
            ci_status=None,
            poll_interval_seconds=21600,
            initial_poll_delay_seconds=300,
            now=now,
        )
        next_poll = _parse(record.next_poll_at)
        expected = now + timedelta(seconds=300)
        self.assertEqual(expected, next_poll)

    def test_season_participant_fields(self) -> None:
        record = lifecycle_record_for_opened_pr(
            repository="owner/repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
            branch="feature/x",
            head="northline-lab:feature/x",
            base="main",
            head_sha="abc123",
            ci_status=None,
            poll_interval_seconds=21600,
            season_id="season_0",
            participant_id="season_0:deepseek",
            originating_run_dir="/tmp/runs/abc123",
        )
        self.assertEqual("season_0", record.season_id)
        self.assertEqual("season_0:deepseek", record.participant_id)
        self.assertEqual("/tmp/runs/abc123", record.originating_run_dir)
