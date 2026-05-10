from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .tool_results import CommandResult


class RepoSummary(BaseModel):
    owner: str
    name: str
    url: str


class OpportunitySummary(BaseModel):
    title: str
    rationale: str = ""
    risk: Literal["low", "medium", "high"] = "low"
    source: str = ""


class SelectedTask(BaseModel):
    title: str
    rationale: str = ""
    expected_change: str = ""
    risk: Literal["low", "medium", "high"] = "low"


class WorkspaceSummary(BaseModel):
    commands_run: list[CommandResult] = Field(default_factory=list)
    patch_applied: bool = False
    notes: str = ""


class AgentFinalResult(BaseModel):
    status: Literal["completed", "blocked", "failed"]
    repo: RepoSummary
    repo_profile: str
    opportunities: list[OpportunitySummary]
    selected_task: SelectedTask
    workspace_summary: WorkspaceSummary = Field(default_factory=WorkspaceSummary)
    blockers: list[str] = Field(default_factory=list)
