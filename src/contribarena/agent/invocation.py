from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from contribarena.models import AgentFinalResult


InvocationStopReason = Literal[
    "content",
    "local_stub",
    "legacy_final_result",
    "max_turns",
    "provider_error",
]


@dataclass
class AgentInvocationResult:
    """One SDK invocation result, not the ContribArena run result."""

    content: str = ""
    stopped_reason: InvocationStopReason = "content"
    legacy_final_result: AgentFinalResult | None = None
    usage: Any | None = None
    tool_call_count: int = 0
    error_message: str = ""
