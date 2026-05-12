from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from agents import ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelProvider
from agents.tool import Tool

from contribarena.models import RunState
from contribarena.trace import TraceWriter


class TracingModelProvider(ModelProvider):
    """ModelProvider wrapper that emits ContribArena trace events for model turns."""

    def __init__(
        self,
        provider: ModelProvider,
        trace: TraceWriter,
        *,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._trace = trace
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._cache: dict[str | None, Model] = {}

    def get_model(self, model_name: str | None) -> Model:
        if model_name not in self._cache:
            self._cache[model_name] = TracingModel(
                self._provider.get_model(model_name),
                self._trace,
                model_name=model_name or "default",
                heartbeat_interval_seconds=self._heartbeat_interval_seconds,
            )
        return self._cache[model_name]


class TracingModel(Model):
    def __init__(
        self,
        model: Model,
        trace: TraceWriter,
        *,
        model_name: str,
        heartbeat_interval_seconds: float,
    ) -> None:
        self._model = model
        self._trace = trace
        self._model_name = model_name
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._turn = 0

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
        turn_id = self._next_turn_id()
        started = time.monotonic()
        self._write_started(
            turn_id,
            mode="response",
            model_settings=model_settings,
            tools=tools,
            output_schema=output_schema,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
        )
        heartbeat = self._start_heartbeat(turn_id, started)
        try:
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
        except Exception as exc:
            self._write_failed(turn_id, started, exc)
            raise
        finally:
            await _cancel_task(heartbeat)
        self._write_finished(turn_id, started, response)
        return response

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
        async def traced_stream() -> AsyncIterator[TResponseStreamEvent]:
            turn_id = self._next_turn_id()
            started = time.monotonic()
            self._write_started(
                turn_id,
                mode="stream",
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
            )
            heartbeat = self._start_heartbeat(turn_id, started)
            event_count = 0
            try:
                async for event in self._model.stream_response(
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
                ):
                    event_count += 1
                    yield event
            except Exception as exc:
                self._write_failed(turn_id, started, exc)
                raise
            finally:
                await _cancel_task(heartbeat)
            self._write_stream_finished(turn_id, started, event_count)

        return traced_stream()

    async def close(self) -> None:
        close = getattr(self._model, "close", None)
        if close is not None:
            await close()

    def _next_turn_id(self) -> str:
        self._turn += 1
        return f"{self._model_name}#{self._turn}"

    def _write_started(
        self,
        turn_id: str,
        *,
        mode: str,
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        previous_response_id: str | None,
        conversation_id: str | None,
    ) -> None:
        self._trace.write(
            RunState.MODEL_TURN_STARTED,
            "model_turn.started",
            {
                "turn_id": turn_id,
                "mode": mode,
                "model": self._model_name,
                "provider": _provider_prefix(self._model_name),
                "tools_count": len(tools),
                "has_output_schema": output_schema is not None,
                "has_previous_response": previous_response_id is not None,
                "has_conversation": conversation_id is not None,
                "max_tokens": getattr(model_settings, "max_tokens", None),
                "heartbeat_interval_seconds": self._heartbeat_interval_seconds,
            },
        )

    def _write_finished(self, turn_id: str, started: float, response: ModelResponse) -> None:
        self._trace.write(
            RunState.MODEL_TURN_FINISHED,
            "model_turn.finished",
            {
                "turn_id": turn_id,
                "model": self._model_name,
                "provider": _provider_prefix(self._model_name),
                "elapsed_ms": _elapsed_ms(started),
                "request_id": response.request_id or "",
                "response_id": response.response_id or "",
                "output_items": len(response.output),
                "usage": _usage_payload(response.usage),
            },
        )

    def _write_stream_finished(self, turn_id: str, started: float, event_count: int) -> None:
        self._trace.write(
            RunState.MODEL_TURN_FINISHED,
            "model_turn.finished",
            {
                "turn_id": turn_id,
                "model": self._model_name,
                "provider": _provider_prefix(self._model_name),
                "elapsed_ms": _elapsed_ms(started),
                "stream_events": event_count,
            },
        )

    def _write_failed(self, turn_id: str, started: float, exc: Exception) -> None:
        self._trace.write(
            RunState.MODEL_TURN_FAILED,
            "model_turn.failed",
            {
                "turn_id": turn_id,
                "model": self._model_name,
                "provider": _provider_prefix(self._model_name),
                "elapsed_ms": _elapsed_ms(started),
                "error_type": type(exc).__name__,
                "error": _safe_error_message(str(exc)),
            },
        )

    def _start_heartbeat(self, turn_id: str, started: float) -> asyncio.Task[None] | None:
        if self._heartbeat_interval_seconds <= 0:
            return None
        return asyncio.create_task(
            _heartbeat(
                self._trace,
                turn_id=turn_id,
                model=self._model_name,
                started=started,
                interval=self._heartbeat_interval_seconds,
            )
        )


async def _heartbeat(
    trace: TraceWriter,
    *,
    turn_id: str,
    model: str,
    started: float,
    interval: float,
) -> None:
    beat = 0
    while True:
        await asyncio.sleep(interval)
        beat += 1
        trace.write(
            RunState.MODEL_TURN_HEARTBEAT,
            "model_turn.heartbeat",
            {
                "turn_id": turn_id,
                "model": model,
                "provider": _provider_prefix(model),
                "beat": beat,
                "elapsed_ms": _elapsed_ms(started),
            },
        )


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return


def _provider_prefix(model_name: str) -> str:
    if "/" not in model_name:
        return "default"
    return model_name.split("/", 1)[0]


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _usage_payload(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        dumped = usage.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(usage, "__dict__"):
        return {
            str(key): value
            for key, value in vars(usage).items()
            if isinstance(value, str | int | float | bool | type(None))
        }
    return {}


def _safe_error_message(message: str) -> str:
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_[REDACTED]", message)
    redacted = re.sub(r"ghp_[A-Za-z0-9_]+", "ghp_[REDACTED]", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[REDACTED]", redacted)
    return redacted[:500]
