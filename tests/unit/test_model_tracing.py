from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agents import ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelProvider
from agents.tool import Tool
from agents.usage import Usage

from contribarena.providers.tracing import TracingModelProvider
from contribarena.trace import TraceWriter


class TracingModelProviderTest(unittest.TestCase):
    def test_traces_model_turn_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            model = TracingModelProvider(
                FakeModelProvider(FakeModel()),
                TraceWriter(path, "run-1"),
                heartbeat_interval_seconds=0,
            ).get_model("compatible/test-model")

            response = asyncio.run(_get_response(model))

            self.assertEqual("req-1", response.request_id)
            events = _events(path)
            self.assertEqual(["model_turn.started", "model_turn.finished"], _event_names(events))
            started = events[0]["payload"]
            finished = events[1]["payload"]
            self.assertEqual("compatible/test-model#1", started["turn_id"])
            self.assertEqual("compatible", started["provider"])
            self.assertEqual("compatible/test-model#1", finished["turn_id"])
            self.assertEqual("req-1", finished["request_id"])
            self.assertEqual("resp-1", finished["response_id"])
            self.assertEqual(0, finished["output_items"])
            self.assertIn("elapsed_ms", finished)

    def test_traces_model_turn_failure_without_secret_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            fake_secret = "sk-" + "secret123"
            model = TracingModelProvider(
                FakeModelProvider(FakeModel(error=RuntimeError(f"bad {fake_secret} value"))),
                TraceWriter(path, "run-1"),
                heartbeat_interval_seconds=0,
            ).get_model("responses/test-model")

            with self.assertRaises(RuntimeError):
                asyncio.run(_get_response(model))

            events = _events(path)
            self.assertEqual(["model_turn.started", "model_turn.failed"], _event_names(events))
            failed = events[1]["payload"]
            self.assertEqual("RuntimeError", failed["error_type"])
            self.assertNotIn(fake_secret, failed["error"])
            self.assertIn("sk-[REDACTED]", failed["error"])

    def test_traces_heartbeat_during_long_model_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            model = TracingModelProvider(
                FakeModelProvider(FakeModel(delay_seconds=0.03)),
                TraceWriter(path, "run-1"),
                heartbeat_interval_seconds=0.01,
            ).get_model("anthropic/claude")

            asyncio.run(_get_response(model))

            events = _events(path)
            names = _event_names(events)
            self.assertIn("model_turn.heartbeat", names)
            self.assertEqual("model_turn.started", names[0])
            self.assertEqual("model_turn.finished", names[-1])
            heartbeat = next(event for event in events if event["event"] == "model_turn.heartbeat")
            self.assertEqual("anthropic", heartbeat["payload"]["provider"])
            self.assertGreaterEqual(heartbeat["payload"]["beat"], 1)


async def _get_response(model: Model) -> ModelResponse:
    return await model.get_response(
        None,
        "prompt",
        ModelSettings(max_tokens=100),
        [],
        None,
        [],
        None,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _event_names(events: list[dict[str, Any]]) -> list[str]:
    return [event["event"] for event in events]


class FakeModelProvider(ModelProvider):
    def __init__(self, model: Model) -> None:
        self.model = model

    def get_model(self, model_name: str | None) -> Model:
        return self.model


class FakeModel(Model):
    def __init__(self, delay_seconds: float = 0, error: Exception | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.error = error

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
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return ModelResponse(
            output=[],
            usage=Usage(requests=1, input_tokens=2, output_tokens=3, total_tokens=5),
            response_id="resp-1",
            request_id="req-1",
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
        raise NotImplementedError


if __name__ == "__main__":
    unittest.main()
