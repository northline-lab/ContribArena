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


class EligibilityResult(BaseModel):
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    checks_performed: list[str] = Field(default_factory=list)
