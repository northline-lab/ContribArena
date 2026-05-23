from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any

from agents import ModelSettings, ModelTracing
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem
from agents.models.interface import Model, ModelProvider
from agents.tool import Tool
from agents.usage import Usage

from contribarena.config.schema import (
    ArtifactConfig,
    CompatibleModelConfig,
    DiscoveryConfig,
    JudgementConfig,
    JudgementJudgeConfig,
    ModelProvidersConfig,
    ModelsConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.engine.provider_preflight import (
    check_season_provider_connectivity,
    raise_for_provider_preflight,
    season_provider_models,
)
from contribarena.errors import ContribArenaError


class ProviderPreflightTests(unittest.TestCase):
    def test_collects_unique_season_agent_and_judge_models(self) -> None:
        config = _config(
            participants=[
                SeasonParticipantConfig(model="compatible/qwen", role=["agent", "judge"]),
                SeasonParticipantConfig(model="responses/gpt", role=["agent"]),
            ],
            judges=[JudgementJudgeConfig(id="judge-a", model="anthropic/opus")],
        )

        self.assertEqual(
            ["compatible/qwen", "responses/gpt", "anthropic/opus"],
            season_provider_models(config, "season_0"),
        )

    def test_default_judge_panel_uses_only_season_judge_participants(self) -> None:
        config = _config(
            participants=[
                SeasonParticipantConfig(model="compatible/qwen", role=["agent"]),
                SeasonParticipantConfig(model="responses/gpt", role=["agent", "judge"]),
            ],
            explicit_judges=False,
        )
        config.models.providers.compatible["qwen"] = CompatibleModelConfig(
            base_url="https://example.invalid/v1",
            model="qwen",
        )

        self.assertEqual(["compatible/qwen", "responses/gpt"], season_provider_models(config, "season_0"))

    def test_checks_models_and_skips_local_stub(self) -> None:
        config = _config(
            participants=[
                SeasonParticipantConfig(model="local-stub"),
                SeasonParticipantConfig(model="compatible/qwen"),
            ],
            judges=[JudgementJudgeConfig(id="judge-a", model="compatible/qwen")],
        )
        provider = FakeProvider({"compatible/qwen": FakeModel()})

        result = check_season_provider_connectivity(config, model_provider=provider)

        self.assertEqual(
            [("local-stub", "skipped"), ("compatible/qwen", "ok")],
            [(check.model, check.status) for check in result.checks],
        )
        self.assertEqual(["compatible/qwen"], provider.requested)
        self.assertEqual(ModelTracing.DISABLED, provider.models["compatible/qwen"].tracing)

    def test_failed_model_blocks_preflight(self) -> None:
        config = _config(
            participants=[SeasonParticipantConfig(model="compatible/qwen")],
            judges=[],
        )
        provider = FakeProvider({"compatible/qwen": FakeModel(error=RuntimeError("boom"))})

        result = check_season_provider_connectivity(config, model_provider=provider)

        self.assertEqual(["compatible/qwen"], [check.model for check in result.failed])
        with self.assertRaisesRegex(ContribArenaError, "season provider preflight failed"):
            raise_for_provider_preflight(result)

    def test_closes_provider_in_same_event_loop_as_probe(self) -> None:
        config = _config(
            participants=[SeasonParticipantConfig(model="compatible/qwen")],
            judges=[],
        )
        model = LoopBoundFakeModel()
        provider = LoopBoundFakeProvider({"compatible/qwen": model})

        result = check_season_provider_connectivity(config, model_provider=provider)

        self.assertIn(("compatible/qwen", "ok"), [(check.model, check.status) for check in result.checks])
        self.assertTrue(provider.closed)


class FakeProvider(ModelProvider):
    def __init__(self, models: dict[str, Model]) -> None:
        self.models = models
        self.requested: list[str] = []
        self.closed = False

    def get_model(self, model_name: str | None) -> Model:
        assert model_name is not None
        self.requested.append(model_name)
        return self.models[model_name]

    async def aclose(self) -> None:
        self.closed = True


class LoopBoundFakeProvider(FakeProvider):
    async def aclose(self) -> None:
        close_loop = asyncio.get_running_loop()
        for model in self.models.values():
            if isinstance(model, LoopBoundFakeModel):
                if model.loop is not close_loop:
                    raise RuntimeError("closed in a different event loop")
        await super().aclose()


class FakeModel(Model):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.tracing: Any = None

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
        self.tracing = tracing
        if self.error is not None:
            raise self.error
        return ModelResponse(
            output=[],
            usage=Usage(requests=1),
            response_id="response-1",
            request_id="request-1",
        )

    def stream_response(self, *args: object, **kwargs: object) -> Any:
        raise NotImplementedError


class LoopBoundFakeModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.loop: asyncio.AbstractEventLoop | None = None

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
        self.loop = asyncio.get_running_loop()
        return await super().get_response(
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


def _config(
    *,
    participants: list[SeasonParticipantConfig],
    judges: list[JudgementJudgeConfig] | None = None,
    explicit_judges: bool = True,
) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="shadow", model="local-stub"),
        discovery=DiscoveryConfig(
            candidates=[RepoCandidate(owner="example", repo="repo", url="https://example.invalid")]
        ),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=Path("/tmp/contribarena-test-runs")),
        models=ModelsConfig(providers=ModelProvidersConfig()),
        judgement=JudgementConfig(judges=judges if explicit_judges else []),
        season=SeasonConfig(
            id="season_0",
            state_root=Path("/tmp/contribarena-test-seasons"),
            participants=participants,
        ),
    )
