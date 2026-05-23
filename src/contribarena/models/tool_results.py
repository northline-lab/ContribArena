from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CommandType = Literal["verification", "setup", "discovery", "other"]


class CommandResult(BaseModel):
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    duration_seconds: float
    timed_out: bool = False
    command_type: CommandType = "other"


class PatchResult(BaseModel):
    success: bool
    files_modified: list[str] = Field(default_factory=list)
    error: str | None = None


class PatchOperation(BaseModel):
    type: str
    path: str
    content: str | None = None
    diff: str | None = None
    destination: str | None = None


class AciResult(BaseModel):
    tool: str
    success: bool
    output: str = ""
    files_modified: list[str] = Field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None
    recovery_kind: str | None = None
    terminal_status: str | None = None
    review_notes: str = ""
    retry_count: int = 0
    terminal_after_retries: bool = False


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
    accepted: bool = True
    recovery_kind: str | None = None
    terminal_status: str | None = None
    retry_count: int = 0
    terminal_after_retries: bool = False


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


class PullRequestCandidate(BaseModel):
    number: int
    title: str
    url: str = ""
    state: str = ""
    author: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    merged_at: str | None = None
    draft: bool = False
    linked_issues: list[int] = Field(default_factory=list)


class IssueLinkage(BaseModel):
    issue_number: int
    assignees: list[str] = Field(default_factory=list)
    linked_prs: list[PullRequestCandidate] = Field(default_factory=list)
    recent_comments: list[str] = Field(default_factory=list)


class RepoSetupProbeResult(BaseModel):
    full_name: str
    success: bool
    probe_failed: bool = False
    default_branch: str = "main"
    package_managers: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    setup_difficulty: str = "unknown"
    duration_seconds: float = 0.0
    error: str = ""


class RepoReadmeResult(BaseModel):
    full_name: str
    success: bool
    path: str = "README"
    content: str = ""
    error: str = ""


class EligibilityResult(BaseModel):
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks_performed: list[str] = Field(default_factory=list)
