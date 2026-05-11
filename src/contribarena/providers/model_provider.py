from __future__ import annotations

import os
from pathlib import Path

from agents.models.interface import Model, ModelProvider
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_provider import OpenAIProvider
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from contribarena.config.schema import ModelsConfig
from contribarena.providers.adapters import AnthropicMessagesModel, GeminiGenerateContentModel


class ContribArenaModelProvider(ModelProvider):
    """Resolve ContribArena model names into Agents SDK Model instances."""

    def __init__(self, config: ModelsConfig | None = None) -> None:
        _load_local_env()
        self.config = config or ModelsConfig()
        self._openai: OpenAIProvider | None = None
        self._compatible_cache: dict[str, Model] = {}
        self._responses_cache: dict[str, Model] = {}
        self._anthropic_cache: dict[str, Model] = {}
        self._gemini_cache: dict[str, Model] = {}

    def get_model(self, model_name: str | None) -> Model:
        if model_name is None:
            if self._openai is None:
                self._openai = OpenAIProvider()
            return self._openai.get_model(None)
        if model_name == "local-stub":
            raise ValueError("local-stub is handled by ContributorAgent and is not a real model")
        if model_name.startswith("openai/"):
            return self._get_openai_model(model_name.removeprefix("openai/"))
        if model_name.startswith("compatible/"):
            return self._get_compatible_model(model_name.removeprefix("compatible/"))
        if model_name.startswith("responses/"):
            return self._get_responses_model(model_name.removeprefix("responses/"))
        if model_name.startswith("anthropic/"):
            return self._get_anthropic_model(model_name.removeprefix("anthropic/"))
        if model_name.startswith("gemini/"):
            return self._get_gemini_model(model_name.removeprefix("gemini/"))
        raise ValueError(f"unknown model provider prefix: {model_name}")

    def _get_openai_model(self, name: str) -> Model:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for openai/* models")
        if self._openai is None:
            self._openai = OpenAIProvider(api_key=api_key)
        return self._openai.get_model(name)

    def _get_compatible_model(self, name: str) -> Model:
        if name in self._compatible_cache:
            return self._compatible_cache[name]
        provider_config = self.config.providers.compatible.get(name)
        if provider_config is None:
            raise ValueError(f"missing compatible model config: {name}")
        api_key = os.environ.get(provider_config.api_key_env, "EMPTY")
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=_openai_compatible_base_url(_resolve_env_value(
                provider_config.base_url,
                provider_config.base_url_env,
                "compatible base_url",
            )),
        )
        model = OpenAIChatCompletionsModel(
            model=provider_config.model or name,
            openai_client=client,
        )
        self._compatible_cache[name] = model
        return model

    def _get_responses_model(self, name: str) -> Model:
        if name in self._responses_cache:
            return self._responses_cache[name]
        provider_config = self.config.providers.responses.get(name)
        if provider_config is None:
            raise ValueError(f"missing responses model config: {name}")
        api_key = os.environ.get(provider_config.api_key_env, "EMPTY")
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=_openai_compatible_base_url(_resolve_env_value(
                provider_config.base_url,
                provider_config.base_url_env,
                "responses base_url",
            )),
        )
        model = OpenAIResponsesModel(
            model=provider_config.model or name,
            openai_client=client,
        )
        self._responses_cache[name] = model
        return model

    def _get_anthropic_model(self, name: str) -> Model:
        if name in self._anthropic_cache:
            return self._anthropic_cache[name]
        provider_config = self.config.providers.anthropic.get(name)
        if provider_config is None:
            raise ValueError(f"missing anthropic model config: {name}")
        api_key = os.environ.get(provider_config.api_key_env)
        if not api_key:
            raise ValueError(f"{provider_config.api_key_env} is required for anthropic/{name}")
        provider_config.base_url = _resolve_env_value(
            provider_config.base_url,
            provider_config.base_url_env,
            "anthropic base_url",
        )
        model = AnthropicMessagesModel(name, provider_config, api_key)
        self._anthropic_cache[name] = model
        return model

    def _get_gemini_model(self, name: str) -> Model:
        if name in self._gemini_cache:
            return self._gemini_cache[name]
        provider_config = self.config.providers.gemini.get(name)
        if provider_config is None:
            raise ValueError(f"missing gemini model config: {name}")
        api_key = os.environ.get(provider_config.api_key_env)
        if not api_key:
            raise ValueError(f"{provider_config.api_key_env} is required for gemini/{name}")
        provider_config.endpoint = _resolve_env_value(
            provider_config.endpoint,
            provider_config.endpoint_env,
            "gemini endpoint",
        )
        model = GeminiGenerateContentModel(name, provider_config, api_key)
        self._gemini_cache[name] = model
        return model

    async def aclose(self) -> None:
        cached_models = [
            *self._compatible_cache.values(),
            *self._responses_cache.values(),
            *self._anthropic_cache.values(),
            *self._gemini_cache.values(),
        ]
        for model in cached_models:
            await model.close()


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_env_value(value: str | None, env_name: str | None, label: str) -> str:
    if value:
        return value
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
        raise ValueError(f"{env_name} is required for {label}")
    raise ValueError(f"{label} is required")


def _openai_compatible_base_url(url: str) -> str:
    stripped = url.rstrip("/")
    suffix = "/chat/completions"
    if stripped.endswith(suffix):
        return stripped[: -len(suffix)]
    return stripped
