from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from contribarena.engine.external_lifecycle import (
    _classify_status,
    _github_state,
    _has_rejection_signal,
    _has_requested_changes,
    _iso,
    _parse,
    _signals_from_status,
    _summary_for_status,
)
from contribarena.models import MaintainerSignal, PrLifecycleRecord
from contribarena.tools.github_pr import PullRequestStatusResult


class ParseTest(unittest.TestCase):
    def test_utc_z_suffix(self) -> None:
        result = _parse("2025-06-15T12:00:00Z")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(datetime(2025, 6, 15, 12, 0, tzinfo=UTC), result)

    def test_utc_offset(self) -> None:
        result = _parse("2025-06-15T12:00:00+00:00")
        self.assertEqual(UTC, result.tzinfo)

    def test_positive_offset(self) -> None:
        result = _parse("2025-06-15T12:00:00+05:30")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(6, result.hour)
        self.assertEqual(30, result.minute)

    def test_negative_offset(self) -> None:
        result = _parse("2025-06-15T12:00:00-04:00")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(16, result.hour)

    def test_naive_assumes_utc(self) -> None:
        result = _parse("2025-06-15T12:00:00")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(12, result.hour)

    def test_microseconds(self) -> None:
        result = _parse("2025-06-15T12:00:00.123456Z")
        self.assertEqual(UTC, result.tzinfo)
        self.assertEqual(123456, result.microsecond)


class IsoTest(unittest.TestCase):
    def test_utc_aware(self) -> None:
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        result = _iso(dt)
        self.assertIn("2025-06-15T12:00:00", result)

    def test_naive_assumes_utc(self) -> None:
        dt = datetime(2025, 6, 15, 12, 0)
        result = _iso(dt)
        self.assertIn("2025-06-15T12:00:00", result)

    def test_positive_offset_converted(self) -> None:
        tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=tz)
        result = _iso(dt)
        self.assertIn("2025-06-15T06:30:00", result)

    def test_negative_offset_converted(self) -> None:
        tz = timezone(timedelta(hours=-4))
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=tz)
        result = _iso(dt)
        self.assertIn("2025-06-15T16:00:00", result)


class GithubStateTest(unittest.TestCase):
    def test_none_is_open(self) -> None:
        self.assertEqual("open", _github_state(None))

    def test_not_ok_is_open(self) -> None:
        pr = PullRequestStatusResult(ok=False, number=1, state="closed")
        self.assertEqual("open", _github_state(pr))

    def test_merged(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, state="closed", merged=True)
        self.assertEqual("merged", _github_state(pr))

    def test_closed_not_merged(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, state="closed", merged=False)
        self.assertEqual("closed", _github_state(pr))

    def test_open(self) -> None:
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("open", _github_state(pr))


class HasRequestedChangesTest(unittest.TestCase):
    def test_changes_requested(self) -> None:
        review = _ReviewStub(state="CHANGES_REQUESTED")
        self.assertTrue(_has_requested_changes([review]))

    def test_changes_requested_lowercase(self) -> None:
        review = _ReviewStub(state="changes_requested")
        self.assertTrue(_has_requested_changes([review]))

    def test_approved(self) -> None:
        review = _ReviewStub(state="APPROVED")
        self.assertFalse(_has_requested_changes([review]))

    def test_empty_list(self) -> None:
        self.assertFalse(_has_requested_changes([]))

    def test_multiple_reviews(self) -> None:
        reviews = [_ReviewStub(state="APPROVED"), _ReviewStub(state="CHANGES_REQUESTED")]
        self.assertTrue(_has_requested_changes(reviews))

    def test_no_state_attribute(self) -> None:
        self.assertFalse(_has_requested_changes([object()]))


class HasRejectionSignalTest(unittest.TestCase):
    def test_rejection(self) -> None:
        signal = MaintainerSignal(repository="a/b", kind="rejection")
        self.assertTrue(_has_rejection_signal([signal]))

    def test_opt_out(self) -> None:
        signal = MaintainerSignal(repository="a/b", kind="opt_out")
        self.assertTrue(_has_rejection_signal([signal]))

    def test_anti_ai_or_bot(self) -> None:
        signal = MaintainerSignal(repository="a/b", kind="anti_ai_or_bot")
        self.assertTrue(_has_rejection_signal([signal]))

    def test_positive_not_rejection(self) -> None:
        signal = MaintainerSignal(repository="a/b", kind="positive")
        self.assertFalse(_has_rejection_signal([signal]))

    def test_empty_list(self) -> None:
        self.assertFalse(_has_rejection_signal([]))

    def test_multiple_signals(self) -> None:
        signals = [
            MaintainerSignal(repository="a/b", kind="positive"),
            MaintainerSignal(repository="a/b", kind="rejection"),
        ]
        self.assertTrue(_has_rejection_signal(signals))


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

    def test_tracking(self) -> None:
        self.assertEqual(
            "external PR remains open and tracked",
            _summary_for_status("tracking"),
        )

    def test_unknown(self) -> None:
        self.assertEqual(
            "external PR remains open and tracked",
            _summary_for_status("unknown"),
        )


class ClassifyStatusTest(unittest.TestCase):
    def _record(self, **kwargs: object) -> PrLifecycleRecord:
        defaults: dict[str, object] = {
            "repository": "example/repo",
            "number": 1,
        }
        defaults.update(kwargs)
        return PrLifecycleRecord(**defaults)  # type: ignore[arg-type]

    def test_pr_not_ok(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=False, number=1)
        self.assertEqual("failed", _classify_status(record, pr, None, []))

    def test_merged(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, merged=True)
        self.assertEqual("merged", _classify_status(record, pr, None, []))

    def test_rejection_signal(self) -> None:
        signal = MaintainerSignal(repository="example/repo", kind="rejection")
        record = self._record(maintainer_signals=[signal])
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("rejected", _classify_status(record, pr, None, []))

    def test_closed_not_merged(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, state="closed", merged=False)
        self.assertEqual("closed", _classify_status(record, pr, None, []))

    def test_requested_changes(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        reviews = [_ReviewStub(state="CHANGES_REQUESTED")]
        self.assertEqual("needs_response", _classify_status(record, pr, None, reviews))

    def test_ci_failure(self) -> None:
        from contribarena.models import CiStatus

        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        ci = CiStatus(status="failure")
        self.assertEqual("needs_response", _classify_status(record, pr, ci, []))

    def test_tracking_when_all_ok(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, state="open", merged=False)
        self.assertEqual("tracking", _classify_status(record, pr, None, []))

    def test_pr_none_with_ci_failure(self) -> None:
        from contribarena.models import CiStatus

        record = self._record()
        ci = CiStatus(status="failure")
        self.assertEqual("needs_response", _classify_status(record, None, ci, []))

    def test_pr_none_tracking(self) -> None:
        record = self._record()
        self.assertEqual("tracking", _classify_status(record, None, None, []))

    def test_merged_overrides_changes(self) -> None:
        record = self._record()
        pr = PullRequestStatusResult(ok=True, number=1, state="closed", merged=True)
        reviews = [_ReviewStub(state="CHANGES_REQUESTED")]
        self.assertEqual("merged", _classify_status(record, pr, None, reviews))


class SignalsFromStatusTest(unittest.TestCase):
    def _record(self, **kwargs: object) -> PrLifecycleRecord:
        defaults: dict[str, object] = {
            "repository": "example/repo",
            "number": 1,
        }
        defaults.update(kwargs)
        return PrLifecycleRecord(**defaults)  # type: ignore[arg-type]

    def test_not_needs_response(self) -> None:
        record = self._record()
        result = _signals_from_status(record, "tracking", [], datetime.now(UTC))
        self.assertEqual([], result)

    def test_needs_response_no_changes(self) -> None:
        record = self._record()
        result = _signals_from_status(record, "needs_response", [], datetime.now(UTC))
        self.assertEqual([], result)

    def test_creates_signal(self) -> None:
        record = self._record()
        reviews = [_ReviewStub(state="CHANGES_REQUESTED")]
        result = _signals_from_status(record, "needs_response", reviews, datetime.now(UTC))
        self.assertEqual(1, len(result))
        self.assertEqual("process_feedback", result[0].kind)
        self.assertEqual("medium", result[0].severity)
        self.assertEqual("example/repo", result[0].repository)
        self.assertEqual("example", result[0].organization)
        self.assertEqual("pr#1", result[0].source)

    def test_deduplicates_existing_signal(self) -> None:
        existing = MaintainerSignal(
            repository="example/repo",
            kind="process_feedback",
            source="pr#1",
        )
        record = self._record(maintainer_signals=[existing])
        reviews = [_ReviewStub(state="CHANGES_REQUESTED")]
        result = _signals_from_status(record, "needs_response", reviews, datetime.now(UTC))
        self.assertEqual([], result)


@dataclass
class _ReviewStub:
    state: str
