from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agents.items import ModelResponse
from openai.types.responses import ResponseFunctionToolCall

VisibleSegmentPosition = Literal["before_tool", "after_tool", "unknown"]


@dataclass(frozen=True)
class VisibleSegment:
    text: str
    position_hint: VisibleSegmentPosition = "unknown"
    provider_index: int | None = None


@dataclass(frozen=True)
class ProviderTurn:
    visible_segments: list[VisibleSegment] = field(default_factory=list)
    tool_calls: list[ResponseFunctionToolCall] = field(default_factory=list)
    final_signal: str | None = None
    hidden_dropped_count: int = 0
    provider_response_id: str | None = None


def provider_turn_from_response(response: ModelResponse) -> ProviderTurn:
    """Build a best-effort turn IR from Agents SDK output items.

    Custom adapters should attach richer provider-index metadata before lossy
    conversion. This fallback keeps the contract available for OpenAI Responses
    and OpenAI-compatible SDK output items.
    """

    segments: list[VisibleSegment] = []
    tool_calls: list[ResponseFunctionToolCall] = []
    hidden_dropped = 0
    first_tool_index: int | None = None
    text_items: list[tuple[int, str]] = []
    for index, item in enumerate(response.output):
        item_type = str(getattr(item, "type", "") or "")
        if _is_hidden_item(item, item_type):
            hidden_dropped += 1
            continue
        if isinstance(item, ResponseFunctionToolCall):
            if first_tool_index is None:
                first_tool_index = index
            tool_calls.append(item)
            hidden_dropped += int(getattr(item, "hidden_dropped_count", 0) or 0)
            continue
        hidden_dropped += int(getattr(item, "hidden_dropped_count", 0) or 0)
        text = _item_text(item)
        if text:
            text_items.append((index, text))
    for index, text in text_items:
        if first_tool_index is None:
            position: VisibleSegmentPosition = "unknown"
        elif index <= first_tool_index:
            position = "before_tool"
        else:
            position = "after_tool"
        segments.append(VisibleSegment(text=text, position_hint=position, provider_index=index))
    return ProviderTurn(
        visible_segments=segments,
        tool_calls=tool_calls,
        hidden_dropped_count=hidden_dropped,
        provider_response_id=response.response_id,
    )


def _is_hidden_item(item: object, item_type: str) -> bool:
    if item_type.startswith("reasoning"):
        return True
    if item_type in {"thinking", "redacted_thinking"}:
        return True
    if getattr(item, "encrypted_content", None):
        return True
    return False


def _item_text(item: object) -> str:
    chunks: list[str] = []
    for content in getattr(item, "content", []) or []:
        content_type = str(getattr(content, "type", "") or "")
        if content_type.startswith("reasoning") or content_type in {"thinking", "redacted_thinking"}:
            continue
        text = getattr(content, "text", "")
        if text:
            chunks.append(str(text))
    if chunks:
        return "\n".join(chunks)
    text = getattr(item, "text", "")
    if text:
        return str(text)
    return ""
