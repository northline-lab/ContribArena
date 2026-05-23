from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from contribarena.models.assistant_updates import AssistantUpdate

from contribarena.models import AgentFinalResult
from contribarena.models.goals import RunPhase, SubPhase


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


@dataclass
class AgentInvocationContext:
    """State shared across SDK invocations within one ContribArena run."""

    sdk_session: Any | None = None
    current_phase: RunPhase = "scout"
    current_sub_phase: SubPhase = "project"
    invocation_seq: int = 0
    assistant_update_builder: Callable[[Any, Any], AssistantUpdate | None] | None = None
    assistant_update_sink: Callable[[AssistantUpdate], None] | None = None
