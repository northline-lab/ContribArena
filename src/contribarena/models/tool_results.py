from __future__ import annotations

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    duration_seconds: float
    timed_out: bool = False


class PatchResult(BaseModel):
    success: bool
    files_modified: list[str] = Field(default_factory=list)
    error: str | None = None


class AciResult(BaseModel):
    tool: str
    success: bool
    output: str = ""
    files_modified: list[str] = Field(default_factory=list)
    error: str | None = None


class AgentStep(BaseModel):
    step: int
    agent: str = "builtin"
    phase: str
    tool: str
    input_summary: str = ""
    result_summary: str = ""
    state: str
    duration_seconds: float
    error: str | None = None


class RepoMetadata(BaseModel):
    owner: str
    repo: str
    full_name: str
    url: str
    description: str = ""
    stars: int = 0
    forks: int = 0
    language: str = ""
    last_push: str | None = None
    created_at: str | None = None
    open_issues: int = 0
    default_branch: str = "main"
    fallback: bool = False


class IssueCandidate(BaseModel):
    number: int
    title: str
    url: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class EligibilityResult(BaseModel):
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks_performed: list[str] = Field(default_factory=list)
