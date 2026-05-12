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


class GovernanceState(BaseModel):
    attempts: list[GovernanceAttempt] = Field(default_factory=list)
    pull_requests: list[GovernancePrRef] = Field(default_factory=list)
