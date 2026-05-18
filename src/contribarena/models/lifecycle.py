from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TerminalStatus = Literal["completed", "blocked", "failed"]
TerminalLayer = Literal[
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


class TerminalState(BaseModel):
    status: TerminalStatus
    reason: str
    layer: TerminalLayer
    message: str = ""
    agent_status: str | None = None
    harness_status: str | None = None


class QualityGateCheck(BaseModel):
    name: str
    status: Literal["pass", "block", "warn"]
    detail: str = ""


class QualityGateResult(BaseModel):
    status: Literal["pass", "block", "fail"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[QualityGateCheck] = Field(default_factory=list)


class PullRequestDraft(BaseModel):
    title: str
    branch: str
    labels: list[str] = Field(default_factory=list)
    body: str


class CiCheck(BaseModel):
    name: str
    status: Literal["success", "failure", "skipped"]
    details: str = ""


class CiStatus(BaseModel):
    status: Literal["success", "failure", "not_run", "pending"]
    source: str = "dry_run"
    checks: list[CiCheck] = Field(default_factory=list)
