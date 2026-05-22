from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents import ModelSettings
from agents.models.interface import ModelProvider

from contribarena.config.schema import RunConfig
from contribarena.engine.judgement import _configured_judge_models
from contribarena.errors import ContribArenaError
from contribarena.providers.model_provider import ContribArenaModelProvider


@dataclass(frozen=True)
class ProviderPreflightCheck:
    model: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ProviderPreflightResult:
    checks: list[ProviderPreflightCheck]

    @property
    def failed(self) -> list[ProviderPreflightCheck]:
        return [check for check in self.checks if check.status == "failed"]


def season_provider_models(config: RunConfig, season_id: str | None = None) -> list[str]:
    season = config.season
    if season is None:
        return [config.run.model]
    if season_id is not None and season.id != season_id:
        return [config.run.model]

    models: list[str] = []
    for participant in season.participants:
        if "agent" in participant.role or "judge" in participant.role:
            models.append(participant.model)
    if config.judgement.enabled:
        if config.judgement.judges:
            models.extend(judge.model for judge in config.judgement.judges)
        else:
            models.extend(_configured_judge_models(config))
    return _unique_models(models or [config.run.model])


def check_season_provider_connectivity(
    config: RunConfig,
    *,
    season_id: str | None = None,
    model_provider: ModelProvider | None = None,
) -> ProviderPreflightResult:
    provider = model_provider or ContribArenaModelProvider(config.models)
    return asyncio.run(_check_models_and_close(provider, season_provider_models(config, season_id)))


async def _check_models_and_close(
    model_provider: ModelProvider,
    models: list[str],
) -> ProviderPreflightResult:
    try:
        return await _check_models(model_provider, models)
    finally:
        close = getattr(model_provider, "aclose", None)
        if close is not None:
            await close()


async def _check_models(
    model_provider: ModelProvider,
    models: list[str],
) -> ProviderPreflightResult:
    checks: list[ProviderPreflightCheck] = []
    for model_name in models:
        if model_name == "local-stub":
            checks.append(ProviderPreflightCheck(model=model_name, status="skipped"))
            continue
        try:
            model = model_provider.get_model(model_name)
            await model.get_response(
                "You are a connectivity probe. Reply with OK.",
                "Reply with OK.",
                ModelSettings(max_tokens=8),
                [],
                None,
                [],
                None,
                previous_response_id=None,
                conversation_id=None,
                prompt=None,
            )
        except Exception as exc:  # noqa: BLE001 - provider probes must surface any failure.
            checks.append(
                ProviderPreflightCheck(
                    model=model_name,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            checks.append(ProviderPreflightCheck(model=model_name, status="ok"))
    return ProviderPreflightResult(checks=checks)


def raise_for_provider_preflight(result: ProviderPreflightResult) -> None:
    if not result.failed:
        return
    lines = ["season provider preflight failed:"]
    for failure in result.failed:
        detail = f" - {failure.detail}" if failure.detail else ""
        lines.append(f"  {failure.model}: failed{detail}")
    raise ContribArenaError("\n".join(lines))


def _unique_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        unique.append(model)
    return unique
