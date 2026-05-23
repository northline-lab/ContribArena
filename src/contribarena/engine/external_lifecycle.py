from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from contribarena.models import CiStatus, MaintainerSignal, PrLifecycleRecord
from contribarena.tools.github_pr import PullRequestStatusResult


@dataclass(frozen=True)
class ExternalLifecycleObservation:
    record: PrLifecycleRecord
    github_status: PullRequestStatusResult | None = None
    action: str = "wait"
    public_comment_event: str = ""
    public_comment_body: str = ""


def lifecycle_record_for_opened_pr(
    *,
    repository: str,
    number: int,
    url: str,
    branch: str,
    head: str,
    base: str,
    head_sha: str,
    ci_status: CiStatus | None,
    poll_interval_seconds: int,
    initial_poll_delay_seconds: int | None = None,
    originating_run_dir: str = "",
    season_id: str = "",
    participant_id: str = "",
    now: datetime | None = None,
) -> PrLifecycleRecord:
    observed_at = _iso(now or datetime.now(UTC))
    next_poll_delay = (
        poll_interval_seconds
        if initial_poll_delay_seconds is None
        else initial_poll_delay_seconds
    )
    next_poll_at = _iso(_parse(observed_at) + timedelta(seconds=next_poll_delay))
    return PrLifecycleRecord(
        season_id=season_id,
        participant_id=participant_id,
        repository=repository,
        number=number,
        url=url,
        originating_run_dir=originating_run_dir,
        branch=branch,
        head=head,
        base=base,
        head_sha=head_sha,
        state="open",
        lifecycle_status="tracking",
        last_observed_at=observed_at,
        last_poll_at="",
        next_poll_at=next_poll_at,
        ci_status=ci_status.status if ci_status is not None else "not_run",
        summary="external PR opened and lifecycle tracking scheduled",
    )


def observe_lifecycle_record(
    *,
    record: PrLifecycleRecord,
    pr_status: PullRequestStatusResult | None,
    ci_status: CiStatus | None,
    reviews: list[object] | None = None,
    poll_interval_seconds: int,
    now: datetime | None = None,
) -> ExternalLifecycleObservation:
    observed = now or datetime.now(UTC)
    status = _classify_status(record, pr_status, ci_status, reviews or [])
    signals = list(record.maintainer_signals)
    signals.extend(_signals_from_status(record, status, reviews or [], observed))
    updated = record.model_copy(
        update={
            "state": _github_state(pr_status),
            "lifecycle_status": status,
            "head_sha": pr_status.head_sha if pr_status and pr_status.head_sha else record.head_sha,
            "last_observed_at": _iso(observed),
            "last_poll_at": _iso(observed),
            "next_poll_at": _iso(observed + timedelta(seconds=poll_interval_seconds)),
            "lifecycle_retry_count": 0,
            "ci_status": ci_status.status if ci_status is not None else record.ci_status,
            "maintainer_signals": signals,
            "summary": _summary_for_status(status),
        }
    )
    return ExternalLifecycleObservation(
        record=updated,
        github_status=pr_status,
        action="terminal" if status in {"merged", "closed", "rejected", "failed"} else "wait",
    )


def mark_lifecycle_observation_failed(
    *,
    record: PrLifecycleRecord,
    error: str,
    poll_interval_seconds: int,
    now: datetime | None = None,
) -> PrLifecycleRecord:
    observed = now or datetime.now(UTC)
    retry_count = record.lifecycle_retry_count + 1
    multiplier = min(2 ** (retry_count - 1), 4)
    backoff_seconds = max(poll_interval_seconds, 1) * multiplier
    return record.model_copy(
        update={
            "last_poll_at": _iso(observed),
            "next_poll_at": _iso(observed + timedelta(seconds=backoff_seconds)),
            "lifecycle_retry_count": retry_count,
            "summary": f"external PR lifecycle observation failed transiently: {error[:160]}",
        }
    )


def lifecycle_record_due(record: PrLifecycleRecord, now: datetime | None = None) -> bool:
    if record.lifecycle_status in {"merged", "closed", "rejected", "failed"}:
        return False
    if not record.next_poll_at:
        return True
    return (now or datetime.now(UTC)) >= _parse(record.next_poll_at)


def _classify_status(
    record: PrLifecycleRecord,
    pr_status: PullRequestStatusResult | None,
    ci_status: CiStatus | None,
    reviews: list[object],
) -> str:
    if pr_status is not None and not pr_status.ok:
        return "failed"
    if pr_status is not None and pr_status.merged:
        return "merged"
    if _has_rejection_signal(record.maintainer_signals):
        return "rejected"
    if pr_status is not None and pr_status.state == "closed":
        return "closed"
    if _has_requested_changes(reviews):
        return "needs_response"
    if ci_status is not None and ci_status.status == "failure":
        return "needs_response"
    return "tracking"


def _signals_from_status(
    record: PrLifecycleRecord,
    status: str,
    reviews: list[object],
    observed: datetime,
) -> list[MaintainerSignal]:
    if status != "needs_response":
        return []
    if not _has_requested_changes(reviews):
        return []
    signal_key = (record.repository, "process_feedback", f"pr#{record.number}")
    existing_keys = {
        (signal.repository, signal.kind, signal.source) for signal in record.maintainer_signals
    }
    if signal_key in existing_keys:
        return []
    return [
        MaintainerSignal(
            repository=record.repository,
            organization=record.repository.split("/", 1)[0],
            kind="process_feedback",
            severity="medium",
            message="review requested changes on external PR",
            source=f"pr#{record.number}",
            created_at=_iso(observed),
        )
    ]


def _github_state(pr_status: PullRequestStatusResult | None) -> str:
    if pr_status is None or not pr_status.ok:
        return "open"
    if pr_status.merged:
        return "merged"
    if pr_status.state == "closed":
        return "closed"
    return "open"


def _has_requested_changes(reviews: list[object]) -> bool:
    for review in reviews:
        state = getattr(review, "state", "")
        if str(state).upper() == "CHANGES_REQUESTED":
            return True
    return False


def _has_rejection_signal(signals: list[MaintainerSignal]) -> bool:
    return any(signal.kind in {"rejection", "opt_out", "anti_ai_or_bot"} for signal in signals)


def _summary_for_status(status: str) -> str:
    if status == "merged":
        return "external PR was merged"
    if status == "closed":
        return "external PR was closed"
    if status == "rejected":
        return "external PR has maintainer rejection signal"
    if status == "needs_response":
        return "external PR needs agent follow-up"
    if status == "failed":
        return "external PR lifecycle observation failed"
    return "external PR remains open and tracked"


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
