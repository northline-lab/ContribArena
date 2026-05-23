from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

AssistantUpdateKind = Literal[
    "intent",
    "observation",
    "decision",
    "blocker",
    "verification",
    "review_response",
]

AssistantUpdatePosition = Literal["before_tool", "after_tool", "commentary_only"]


class AssistantUpdate(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str
    season_id: str = ""
    participant_id: str = ""
    invocation_seq: int = 0
    turn_id: str = ""
    phase: str = ""
    sub_phase: str = ""
    kind: AssistantUpdateKind = "intent"
    text: str
    position: AssistantUpdatePosition = "before_tool"
    tool_name: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    truncated: bool = False
    redacted: bool = False
    hidden_dropped_count: int = 0
    visibility: str = "operator"
