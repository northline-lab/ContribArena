from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from agents import ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelProvider
from agents.tool import FunctionTool, Tool
from openai.types.responses import ResponseFunctionToolCall

RECOVERY_TOOL_NAME = "aci_recover_invalid_action"


@dataclass(frozen=True)
class ToolActionViolation:
    recovery_kind: str
    message: str
    attempted_tool: str = ""


class ActionGuardedModel(Model):
    """Model wrapper that turns invalid tool actions into recoverable observations."""

    def __init__(self, model: Model) -> None:
        self._model = model

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
        response = await self._model.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        return guard_structured_model_response(response, tools, output_schema)

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
        return self._model.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )

    async def close(self) -> None:
        close = getattr(self._model, "close", None)
        if close is not None:
            await close()


class ActionGuardingModelProvider(ModelProvider):
    """ModelProvider wrapper that applies action guarding only inside contributor runs."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self._cache: dict[str | None, Model] = {}

    def get_model(self, model_name: str | None) -> Model:
        if model_name not in self._cache:
            self._cache[model_name] = ActionGuardedModel(self._provider.get_model(model_name))
        return self._cache[model_name]


def guard_model_response(response: ModelResponse, tools: list[Tool]) -> ModelResponse:
    return _guard_model_response(response, tools, output_schema=None)


def guard_structured_model_response(
    response: ModelResponse,
    tools: list[Tool],
    output_schema: AgentOutputSchemaBase | None,
) -> ModelResponse:
    return _guard_model_response(response, tools, output_schema=output_schema)


def _guard_model_response(
    response: ModelResponse,
    tools: list[Tool],
    output_schema: AgentOutputSchemaBase | None,
) -> ModelResponse:
    tool_calls = [item for item in response.output if isinstance(item, ResponseFunctionToolCall)]
    if not tool_calls:
        violation = _non_tool_text_violation(response, tools, output_schema)
        if violation is not None:
            return ModelResponse(
                output=[_recovery_call(violation)],
                usage=response.usage,
                response_id=response.response_id,
                request_id=response.request_id,
            )
        return response
    violation = _tool_action_violation(tool_calls, tools)
    if violation is None:
        return response
    return ModelResponse(
        output=[_recovery_call(violation)],
        usage=response.usage,
        response_id=response.response_id,
        request_id=response.request_id,
    )


def _non_tool_text_violation(
    response: ModelResponse,
    tools: list[Tool],
    output_schema: AgentOutputSchemaBase | None,
) -> ToolActionViolation | None:
    if output_schema is None or output_schema.is_plain_text():
        return None
    if RECOVERY_TOOL_NAME not in {tool.name for tool in tools if isinstance(tool, FunctionTool)}:
        return None
    text = _response_text(response).strip()
    if not text or _looks_like_json(text):
        return None
    return ToolActionViolation(
        recovery_kind="non_tool_text_response",
        message=(
            "Rejected plain assistant text while a structured final result or one tool call "
            "was required. Use a tool call to continue, or return valid final JSON only when "
            "the task is complete."
        ),
    )


def _tool_action_violation(
    tool_calls: list[ResponseFunctionToolCall],
    tools: list[Tool],
) -> ToolActionViolation | None:
    tool_schemas = {
        tool.name: tool.params_json_schema for tool in tools if isinstance(tool, FunctionTool)
    }
    if RECOVERY_TOOL_NAME not in tool_schemas:
        return None
    if len(tool_calls) > 1:
        names = ", ".join(call.name for call in tool_calls)
        return ToolActionViolation(
            recovery_kind="multi_tool_action",
            message=(
                "Rejected multiple tool calls in one model step. "
                f"Attempted tools: {names}."
            ),
            attempted_tool=names,
        )
    call = tool_calls[0]
    if call.name == RECOVERY_TOOL_NAME:
        return None
    schema = tool_schemas.get(call.name)
    if schema is None:
        return ToolActionViolation(
            recovery_kind="unknown_tool",
            message=f"Rejected unknown tool call: {call.name}.",
            attempted_tool=call.name,
        )
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        return ToolActionViolation(
            recovery_kind="malformed_action",
            message=f"Rejected malformed JSON arguments for {call.name}: {exc.msg}.",
            attempted_tool=call.name,
        )
    if not isinstance(args, dict):
        return ToolActionViolation(
            recovery_kind="invalid_tool_arguments",
            message=f"Rejected non-object arguments for {call.name}.",
            attempted_tool=call.name,
        )
    return _schema_violation(call.name, args, schema)


def _schema_violation(
    tool_name: str,
    args: dict[str, Any],
    schema: dict[str, Any],
) -> ToolActionViolation | None:
    properties = schema.get("properties") or {}
    required = {
        str(name)
        for name in schema.get("required") or []
        if "default" not in (properties.get(str(name)) or {})
    }
    missing = sorted(required - set(args))
    if missing:
        return ToolActionViolation(
            recovery_kind="invalid_tool_arguments",
            message=(
                f"Rejected tool call {tool_name}: missing required argument(s) "
                + ", ".join(missing)
                + "."
            ),
            attempted_tool=tool_name,
        )
    if schema.get("additionalProperties") is False:
        property_names = set(properties.keys())
        unexpected = sorted(set(args) - property_names)
        if unexpected:
            return ToolActionViolation(
                recovery_kind="invalid_tool_arguments",
                message=(
                    f"Rejected tool call {tool_name}: unexpected argument(s) "
                    + ", ".join(unexpected)
                    + "."
                ),
                attempted_tool=tool_name,
            )
    return None


def _response_text(response: ModelResponse) -> str:
    chunks: list[str] = []
    for item in response.output:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def _recovery_call(violation: ToolActionViolation) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(
            {
                "recovery_kind": violation.recovery_kind,
                "message": violation.message,
                "attempted_tool": violation.attempted_tool,
            },
            ensure_ascii=True,
        ),
        call_id="contribarena-invalid-action-recovery",
        name=RECOVERY_TOOL_NAME,
        type="function_call",
        id="contribarena-invalid-action-recovery",
    )
