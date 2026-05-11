from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


TerminalStatus = Literal["completed", "blocked", "failed"]
TerminalLayer = Literal[
    "run",
    "workspace",
    "agent",
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

