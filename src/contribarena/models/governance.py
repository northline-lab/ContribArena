from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


GovernanceDecisionStatus = Literal["pass", "block"]


class GovernanceDecision(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    status: GovernanceDecisionStatus
    reasons: list[str] = Field(default_factory=list)
    target_repository: str
    action: str
    contribution_class: str = ""
    external_write: bool = False
    actor: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def passed(self) -> bool:
        return self.status == "pass"


class GovernancePrRef(BaseModel):
    repository: str
    number: int
    url: str = ""
    branch: str = ""
    state: Literal["open", "closed", "merged"] = "open"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class GovernanceAttempt(BaseModel):
    repository: str
    action: str
    status: Literal["prepared", "opened", "blocked", "failed", "skipped"]
    decision_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class MaintainerSignal(BaseModel):
    repository: str
    organization: str = ""
    kind: Literal[
        "rejection",
        "opt_out",
        "anti_ai_or_bot",
        "positive",
        "process_feedback",
        "other",
    ]
    severity: Literal["low", "medium", "high"] = "medium"
    message: str = ""
    source: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class PrLifecycleRecord(BaseModel):
    repository: str
    number: int
    url: str = ""
    originating_run_dir: str = ""
    branch: str = ""
    head: str = ""
    base: str = "main"
    head_sha: str = ""
    state: Literal["open", "closed", "merged"] = "open"
    lifecycle_status: Literal[
        "tracking",
        "merged",
        "closed",
        "rejected",
        "stale",
        "needs_response",
        "blocked",
        "failed",
    ] = "tracking"
    last_observed_at: str = ""
    last_poll_at: str = ""
    next_poll_at: str = ""
    lifecycle_retry_count: int = 0
    ci_status: str = "not_run"
    review_cursor: str = ""
    comment_cursor: str = ""
    summary: str = ""
    maintainer_signals: list[MaintainerSignal] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class GovernanceState(BaseModel):
    attempts: list[GovernanceAttempt] = Field(default_factory=list)
    pull_requests: list[GovernancePrRef] = Field(default_factory=list)
    lifecycle_records: list[PrLifecycleRecord] = Field(default_factory=list)
    maintainer_signals: list[MaintainerSignal] = Field(default_factory=list)
