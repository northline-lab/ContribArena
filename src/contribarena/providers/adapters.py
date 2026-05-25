from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
from json_repair import repair_json
from agents import ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from agents.tool import FunctionTool, Tool
from agents.usage import Usage
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)

from contribarena.config.schema import AnthropicModelConfig, GeminiModelConfig


class AnthropicMessagesModel(Model):
    """Minimal Agents SDK model adapter for Anthropic Messages-compatible endpoints."""

    def __init__(self, name: str, config: AnthropicModelConfig, api_key: str) -> None:
        self.name = name
        self.config = config
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=120)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> ModelResponse:
        _reject_unsupported(handoffs, prompt)
        messages, system = _to_anthropic_messages(
            self.model_name, system_instructions, input, output_schema
        )
        anthropic_tools = _to_anthropic_tools(tools)
        system = _append_text_tool_protocol(system, anthropic_tools)
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": model_settings.max_tokens or self.config.max_tokens,
        }
        if system:
            body["system"] = system
        if anthropic_tools:
            body["tools"] = anthropic_tools
        if model_settings.temperature is not None:
            body["temperature"] = model_settings.temperature
        if model_settings.top_p is not None:
            body["top_p"] = model_settings.top_p
        _apply_anthropic_thinking(body, self.config)

        endpoint = _anthropic_messages_endpoint(self.config.base_url)
        response = await self._client.post(
            endpoint,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            json=body,
        )
        _raise_for_status_with_body(response)
        payload = response.json()
        message, hidden_dropped_count = _anthropic_response_to_chat_message(payload)
        message = _repair_structured_output_message(message)
        output = Converter.message_to_output_items(
            message, provider_data={"model": self.model_name}
        )
        _annotate_hidden_dropped(output, hidden_dropped_count)
        return ModelResponse(
            output=output,
            usage=_anthropic_usage(payload),
            response_id=payload.get("id"),
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("Anthropic streaming is not implemented")

    @property
    def model_name(self) -> str:
        return self.config.model or self.name


def _apply_anthropic_thinking(body: dict[str, Any], config: AnthropicModelConfig) -> None:
    if not config.thinking_enabled:
        return
    if config.thinking_type == "adaptive":
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": config.thinking_effort}
        return
    if config.thinking_budget_tokens is None:
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": config.thinking_effort}
        return
    body["thinking"] = {
        "type": "enabled",
        "budget_tokens": config.thinking_budget_tokens,
    }


class GeminiGenerateContentModel(Model):
    """Minimal Agents SDK model adapter for Gemini generateContent-compatible endpoints."""

    def __init__(self, name: str, config: GeminiModelConfig, api_key: str) -> None:
        self.name = name
        self.config = config
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=180)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> ModelResponse:
        _reject_unsupported(handoffs, prompt)
        body = {
            "contents": _to_gemini_contents(input),
            "generationConfig": _gemini_generation_config(
                model_settings, output_schema, self.config
            ),
        }
        system_text = _system_with_schema(system_instructions, output_schema)
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            body["tools"] = gemini_tools

        response = await self._client.post(
            self.config.endpoint,
            headers=_gemini_headers(self.config, self._api_key),
            json=body,
        )
        _raise_for_status_with_body(response)
        payload = _parse_gemini_payload(response)
        message, hidden_dropped_count = _gemini_response_to_chat_message(payload)
        message = _repair_structured_output_message(message)
        output = Converter.message_to_output_items(
            message, provider_data={"model": self.model_name}
        )
        _annotate_hidden_dropped(output, hidden_dropped_count)
        return ModelResponse(
            output=output,
            usage=_gemini_usage(payload),
            response_id=None,
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: Any,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("Gemini streaming is not implemented")

    @property
    def model_name(self) -> str:
        return self.config.model or self.name


def _reject_unsupported(handoffs: list[Handoff], prompt: Any) -> None:
    if handoffs:
        raise ValueError("handoffs are not supported by provider adapters yet")
    if prompt is not None:
        raise ValueError("prompt config is not supported by provider adapters yet")


def _raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text[:2000]
        raise httpx.HTTPStatusError(
            f"{exc} Response body: {body}",
            request=exc.request,
            response=exc.response,
        ) from exc


def _anthropic_messages_endpoint(base_url: str | None) -> str:
    if not base_url:
        raise ValueError("Anthropic adapter requires base_url")
    stripped = base_url.rstrip("/")
    if stripped.endswith("/messages"):
        return stripped
    return f"{stripped}/messages"


def _system_with_schema(
    system_instructions: str | None,
    output_schema: AgentOutputSchemaBase | None,
) -> str | None:
    parts = [system_instructions] if system_instructions else []
    if output_schema and not output_schema.is_plain_text():
        parts.append(
            "When producing the final answer, return only valid JSON matching this schema:\n"
            + json.dumps(output_schema.json_schema(), ensure_ascii=True)
        )
    return "\n\n".join(parts) if parts else None


def _append_text_tool_protocol(system: str | None, tools: list[dict[str, Any]]) -> str | None:
    if not tools:
        return system
    protocol = [
        "If the API does not emit native tool_use blocks, request exactly one tool call using:",
        "[Tool call: tool_name]",
        '{"arg": "value"}',
        "Use only these tool names and schemas:",
        json.dumps(tools, ensure_ascii=True),
    ]
    return "\n\n".join(part for part in [system, "\n".join(protocol)] if part)


def _chat_messages(input: str | list[TResponseInputItem], model: str) -> list[dict[str, Any]]:
    return [dict(message) for message in Converter.items_to_messages(input, model=model)]


def _text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in {"text", "input_text", "output_text"}:
                    texts.append(str(part.get("text", "")))
                elif "content" in part:
                    texts.append(str(part.get("content", "")))
            else:
                texts.append(str(part))
        return "\n".join(text for text in texts if text)
    return str(content)


def _to_anthropic_messages(
    model: str,
    system_instructions: str | None,
    input: str | list[TResponseInputItem],
    output_schema: AgentOutputSchemaBase | None,
) -> tuple[list[dict[str, Any]], str | None]:
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    system_text = _system_with_schema(system_instructions, output_schema)
    if system_text:
        system_parts.append(system_text)

    for message in _chat_messages(input, model):
        role = message.get("role")
        if role in {"system", "developer"}:
            system_parts.append(_text_content(message.get("content")))
            continue
        if role == "user":
            messages.append({"role": "user", "content": _text_content(message.get("content"))})
            continue
        if role == "assistant":
            content: list[dict[str, Any]] = []
            text = _text_content(message.get("content"))
            if text:
                content.append({"type": "text", "text": text})
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function", {})
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": function.get("name"),
                        "input": _json_loads(function.get("arguments") or "{}"),
                    }
                )
            messages.append({"role": "assistant", "content": content or text})
            continue
        if role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id"),
                            "content": _text_content(message.get("content")),
                        }
                    ],
                }
            )
    return messages, "\n\n".join(system_parts) if system_parts else None


def _to_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            raise ValueError(f"unsupported tool for Anthropic adapter: {type(tool).__name__}")
        result.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.params_json_schema,
            }
        )
    return result


def _anthropic_response_to_chat_message(payload: dict[str, Any]) -> tuple[ChatCompletionMessage, int]:
    text_parts: list[str] = []
    tool_calls: list[ChatCompletionMessageFunctionToolCall] = []
    hidden_dropped_count = 0
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ChatCompletionMessageFunctionToolCall(
                    id=block.get("id"),
                    type="function",
                    function=Function(
                        name=block.get("name"),
                        arguments=json.dumps(block.get("input") or {}, ensure_ascii=True),
                    ),
                )
            )
        elif block.get("type") in {"thinking", "redacted_thinking"}:
            hidden_dropped_count += 1
    text = "\n".join(text_parts) if text_parts else None
    parsed_text, parsed_calls = _parse_text_tool_calls(text or "")
    tool_calls.extend(parsed_calls)
    return (
        ChatCompletionMessage(
            role="assistant",
            content=parsed_text or None,
            tool_calls=tool_calls or None,
        ),
        hidden_dropped_count,
    )


def _parse_text_tool_calls(
    text: str,
) -> tuple[str, list[ChatCompletionMessageFunctionToolCall]]:
    pattern = re.compile(
        r"\[Tool call:\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\]\s*"
        r"(?P<args>\{.*?\})(?=\s*\[Tool call:|\s*$)",
        flags=re.DOTALL,
    )
    calls: list[ChatCompletionMessageFunctionToolCall] = []
    for index, match in enumerate(pattern.finditer(text), start=1):
        calls.append(
            ChatCompletionMessageFunctionToolCall(
                id=f"text-tool-call-{index}",
                type="function",
                function=Function(
                    name=match.group("name"),
                    arguments=json.dumps(_json_loads(match.group("args")), ensure_ascii=True),
                ),
            )
        )
    cleaned = pattern.sub("", text).strip()
    return cleaned, calls


def _anthropic_usage(payload: dict[str, Any]) -> Usage:
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return Usage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _to_gemini_contents(input: str | list[TResponseInputItem]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    pending_tool_parts: list[dict[str, Any]] = []

    def flush_tool_parts() -> None:
        nonlocal pending_tool_parts
        if pending_tool_parts:
            contents.append({"role": "user", "parts": pending_tool_parts})
            pending_tool_parts = []

    for message in _chat_messages(input, model="gemini"):
        role = message.get("role")
        if role in {"system", "developer"}:
            flush_tool_parts()
            contents.append(
                {"role": "user", "parts": [{"text": _text_content(message.get("content"))}]}
            )
            continue
        if role == "user":
            flush_tool_parts()
            contents.append(
                {"role": "user", "parts": [{"text": _text_content(message.get("content"))}]}
            )
            continue
        if role == "assistant":
            flush_tool_parts()
            parts: list[dict[str, Any]] = []
            text = _text_content(message.get("content"))
            if text:
                parts.append({"text": text})
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function", {})
                call_id = str(tool_call.get("id") or "")
                name = str(function.get("name") or "")
                if call_id and name:
                    call_names[call_id] = name
                part = {
                    "functionCall": {
                        "name": name,
                        "args": _json_loads(function.get("arguments") or "{}"),
                    }
                }
                google_extra = (tool_call.get("extra_content") or {}).get("google") or {}
                thought_signature = google_extra.get("thought_signature")
                if thought_signature:
                    part["thoughtSignature"] = thought_signature
                parts.append(part)
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            pending_tool_parts.append(
                {
                    "functionResponse": {
                        "name": call_names.get(call_id, call_id or "tool"),
                        "response": {"result": _text_content(message.get("content"))},
                    }
                }
            )
    flush_tool_parts()
    return contents


def _to_gemini_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    declarations = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            raise ValueError(f"unsupported tool for Gemini adapter: {type(tool).__name__}")
        declarations.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.params_json_schema,
            }
        )
    return [{"functionDeclarations": declarations}] if declarations else []


def _gemini_generation_config(
    model_settings: ModelSettings,
    output_schema: AgentOutputSchemaBase | None,
    config: GeminiModelConfig,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "maxOutputTokens": model_settings.max_tokens or config.max_tokens,
    }
    if model_settings.temperature is not None:
        result["temperature"] = model_settings.temperature
    if model_settings.top_p is not None:
        result["topP"] = model_settings.top_p
    if output_schema and not output_schema.is_plain_text():
        result["responseMimeType"] = "application/json"
        result["responseJsonSchema"] = output_schema.json_schema()
    return result


def _gemini_headers(config: GeminiModelConfig, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key_header.lower() == "authorization":
        headers["Authorization"] = f"{config.auth_scheme} {api_key}".strip()
    else:
        headers[config.api_key_header] = api_key
    return headers


def _parse_gemini_payload(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()

    merged: dict[str, Any] = {"candidates": [{"content": {"role": "model", "parts": []}}]}
    for line in response.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        chunk = json.loads(data)
        candidates = chunk.get("candidates") or []
        if candidates:
            candidate = candidates[0] or {}
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            merged["candidates"][0]["content"]["parts"].extend(parts)
            for key, value in candidate.items():
                if key != "content":
                    merged["candidates"][0][key] = value
        for key, value in chunk.items():
            if key != "candidates":
                merged[key] = value
    if not merged["candidates"][0]["content"]["parts"]:
        raise ValueError("empty Gemini SSE response")
    return merged


def _gemini_response_to_chat_message(payload: dict[str, Any]) -> tuple[ChatCompletionMessage, int]:
    candidates = payload.get("candidates") or []
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
    text_parts: list[str] = []
    tool_calls: list[ChatCompletionMessageFunctionToolCall] = []
    last_thought_signature: str | None = None
    hidden_dropped_count = 0
    for index, part in enumerate(parts):
        if part.get("thought") is True:
            hidden_dropped_count += 1
        elif "text" in part:
            text_parts.append(str(part["text"]))
        function_call = part.get("functionCall") or part.get("function_call")
        if function_call:
            name = function_call.get("name")
            args = function_call.get("args") or {}
            call_id = f"gemini-call-{uuid4().hex[:12]}-{index}"
            tool_call = ChatCompletionMessageFunctionToolCall(
                id=call_id,
                type="function",
                function=Function(name=name, arguments=json.dumps(args, ensure_ascii=True)),
            )
            thought_signature = part.get("thoughtSignature") or part.get("thought_signature")
            if thought_signature:
                last_thought_signature = thought_signature
                hidden_dropped_count += 1
            elif last_thought_signature:
                thought_signature = last_thought_signature
            if thought_signature:
                tool_call.extra_content = {"google": {"thought_signature": thought_signature}}
            tool_calls.append(tool_call)
    return (
        ChatCompletionMessage(
            role="assistant",
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
        ),
        hidden_dropped_count,
    )


def _annotate_hidden_dropped(output: list[Any], hidden_dropped_count: int) -> None:
    if hidden_dropped_count <= 0:
        return
    for item in output:
        setattr(item, "hidden_dropped_count", hidden_dropped_count)
        return


def _gemini_usage(payload: dict[str, Any]) -> Usage:
    metadata = payload.get("usageMetadata") or payload.get("usage_metadata") or {}
    input_tokens = int(metadata.get("promptTokenCount") or metadata.get("prompt_token_count") or 0)
    output_tokens = int(
        metadata.get("candidatesTokenCount") or metadata.get("candidates_token_count") or 0
    )
    total_tokens = int(metadata.get("totalTokenCount") or metadata.get("total_token_count") or 0)
    return Usage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens or input_tokens + output_tokens,
    )


def _repair_structured_output_message(message: ChatCompletionMessage) -> ChatCompletionMessage:
    if not message.content or message.tool_calls:
        return message
    content = _extract_json_candidate(message.content)
    if not content:
        return message
    repaired_text = _repair_json_control_chars(content)
    try:
        parsed = json.loads(repaired_text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(repair_json(content))
        except (ValueError, json.JSONDecodeError):
            return message
    normalized = _strip_object_keys(parsed)
    return ChatCompletionMessage(
        role="assistant",
        content=json.dumps(normalized, ensure_ascii=True),
        tool_calls=None,
    )


def _extract_json_candidate(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        return stripped

    fenced = re.search(r"```(?:json)?\s*(?P<body>[\s\S]*?)\s*```", stripped, re.IGNORECASE)
    if fenced:
        body = fenced.group("body").strip()
        if body.startswith(("{", "[")):
            return body

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return None


def _repair_json_control_chars(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
        if in_string and char in {"\n", "\r", "\t"}:
            result.append("\\n" if char in {"\n", "\r"} else "\\t")
            continue
        result.append(char)
    repaired = "".join(result)
    return re.sub(r"(?<=[0-9.])\s+(?=[0-9])", "", repaired)


def _strip_object_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            re.sub(r"\s+", "", str(key)): _strip_object_keys(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_strip_object_keys(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _json_loads(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"value": value}
