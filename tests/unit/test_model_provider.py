from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.models.interface import ModelTracing
from agents import ModelSettings
from agents.items import ModelResponse
from openai import AsyncOpenAI
from openai.types.shared.reasoning import Reasoning

from contribarena.config.schema import (
    AnthropicModelConfig,
    CompatibleModelConfig,
    GeminiModelConfig,
    ModelProvidersConfig,
    ModelsConfig,
    ResponsesModelConfig,
)
from contribarena.providers.adapters import AnthropicMessagesModel, GeminiGenerateContentModel
from contribarena.providers import ContribArenaModelProvider
from contribarena.providers.model_provider import (
    SafeOpenAIChatCompletionsModel,
    SafeOpenAIResponsesModel,
    _compatible_chat_input,
    _non_empty_chat_text,
    _responses_model_settings,
)


class ContribArenaModelProviderTest(unittest.TestCase):
    def test_resolves_openai_prefix_to_native_responses_model(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            model = ContribArenaModelProvider().get_model("openai/gpt-4.1-mini")

        self.assertIsInstance(model, OpenAIResponsesModel)

    def test_openai_prefix_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                ContribArenaModelProvider().get_model("openai/gpt-4.1-mini")

    def test_resolves_compatible_prefix_to_chat_completions_model(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    compatible={
                        "qwen3-coder": CompatibleModelConfig(
                            base_url="http://localhost:8000/v1",
                            model="qwen/qwen3-coder",
                            api_key_env="MISSING_LOCAL_KEY",
                        )
                    }
                )
            )
        )

        model = provider.get_model("compatible/qwen3-coder")

        self.assertIsInstance(model, OpenAIChatCompletionsModel)
        self.assertIsInstance(model, SafeOpenAIChatCompletionsModel)

    def test_compatible_chat_input_replaces_empty_tool_outputs(self) -> None:
        normalized = _compatible_chat_input(
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": None,
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_3",
                    "output": "real output",
                },
            ]
        )

        self.assertEqual("[tool completed with no output]", normalized[0]["output"])
        self.assertEqual("[tool completed with no output]", normalized[1]["output"])
        self.assertEqual("real output", normalized[2]["output"])

    def test_compatible_chat_input_replaces_empty_assistant_content(self) -> None:
        normalized = _compatible_chat_input(
            [
                {"role": "assistant", "content": ""},
                {"role": "system", "content": None},
                {"role": "user", "content": ""},
            ]
        )

        self.assertEqual(" ", normalized[0]["content"])
        self.assertEqual(" ", normalized[1]["content"])
        self.assertEqual("", normalized[2]["content"])

    def test_non_empty_chat_text_replaces_empty_system_instruction(self) -> None:
        self.assertEqual(" ", _non_empty_chat_text(""))
        self.assertIsNone(_non_empty_chat_text(None))
        self.assertEqual("hello", _non_empty_chat_text("hello"))

    def test_safe_compatible_chat_model_normalizes_before_fetch(self) -> None:
        class FakeChatModel(SafeOpenAIChatCompletionsModel):
            def __init__(self) -> None:
                super().__init__(
                    model="test-model",
                    openai_client=AsyncOpenAI(api_key="test-key", base_url="https://example.com/v1"),
                )
                self.seen_system_instructions = None
                self.seen_input = None

            async def _fetch_response(  # type: ignore[no-untyped-def]
                self,
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                span,
                tracing,
                stream=False,
                prompt=None,
            ):
                self.seen_system_instructions = system_instructions
                self.seen_input = input
                raise RuntimeError("stop after capture")

        async def call_model(model: FakeChatModel) -> None:
            await model.get_response(
                system_instructions="",
                input=[
                    {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": "",
                    }
                ],
                model_settings=ModelSettings(),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
            )

        model = FakeChatModel()

        with self.assertRaisesRegex(RuntimeError, "stop after capture"):
            asyncio.run(call_model(model))

        self.assertEqual(" ", model.seen_system_instructions)
        self.assertEqual("[tool completed with no output]", model.seen_input[0]["output"])

    def test_compatible_base_url_env_accepts_full_chat_completions_endpoint(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    compatible={
                        "chat": CompatibleModelConfig(
                            base_url_env="TEST_CHAT_ENDPOINT",
                            model="gpt-5",
                            api_key_env="TEST_CHAT_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict(
            "os.environ",
            {
                "TEST_CHAT_ENDPOINT": "https://example.com/api/v1/chat/completions",
                "TEST_CHAT_KEY": "test-key",
            },
        ):
            model = provider.get_model("compatible/chat")

        self.assertIsInstance(model, OpenAIChatCompletionsModel)
        self.assertEqual("https://example.com/api/v1/", str(model._client.base_url))

    def test_resolves_responses_prefix_to_responses_model(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    responses={
                        "responses-provider": ResponsesModelConfig(
                            base_url_env="TEST_RESPONSES_BASE_URL",
                            model="gpt-5.4",
                            api_key_env="TEST_RESPONSES_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict(
            "os.environ",
            {
                "TEST_RESPONSES_BASE_URL": "https://example.com/v1",
                "TEST_RESPONSES_KEY": "test-key",
            },
        ):
            model = provider.get_model("responses/responses-provider")

        self.assertIsInstance(model, OpenAIResponsesModel)
        self.assertEqual("https://example.com/v1/", str(model._client.base_url))

    def test_responses_model_settings_default_reasoning_effort_is_high(self) -> None:
        settings = _responses_model_settings(ModelSettings())

        self.assertIsNotNone(settings.reasoning)
        self.assertEqual("high", settings.reasoning.effort)

    def test_responses_model_settings_preserves_explicit_reasoning(self) -> None:
        settings = _responses_model_settings(
            ModelSettings(reasoning=Reasoning(effort="medium"))
        )

        self.assertEqual("medium", settings.reasoning.effort)

    def test_responses_model_rejects_non_response_object_payloads_clearly(self) -> None:
        class FakeResponsesModel(SafeOpenAIResponsesModel):
            def __init__(self) -> None:
                pass

            async def _fetch_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return 123

        async def call_model() -> None:
            await FakeResponsesModel().get_response(
                system_instructions=None,
                input="hello",
                model_settings=ModelSettings(),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=None,
            )

        with self.assertRaisesRegex(TypeError, "expected Response-like object"):
            asyncio.run(call_model())

    def test_responses_model_accepts_json_string_text_payload(self) -> None:
        class FakeResponsesModel(SafeOpenAIResponsesModel):
            def __init__(self) -> None:
                pass

            async def _fetch_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return '{"id": "resp_1", "output_text": "hello"}'

        async def call_model() -> ModelResponse:
            return await FakeResponsesModel().get_response(
                system_instructions=None,
                input="hello",
                model_settings=ModelSettings(),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=None,
            )

        response = asyncio.run(call_model())

        self.assertEqual("resp_1", response.response_id)
        self.assertEqual("hello", response.output[0].content[0].text)

    def test_responses_model_accepts_plain_string_text_payload(self) -> None:
        class FakeResponsesModel(SafeOpenAIResponsesModel):
            def __init__(self) -> None:
                pass

            async def _fetch_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return "plain assistant text"

        async def call_model() -> ModelResponse:
            return await FakeResponsesModel().get_response(
                system_instructions=None,
                input="hello",
                model_settings=ModelSettings(),
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=None,
            )

        response = asyncio.run(call_model())

        self.assertEqual("", response.response_id)
        self.assertEqual("plain assistant text", response.output[0].content[0].text)

    def test_resolves_anthropic_prefix_to_messages_adapter(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    anthropic={
                        "claude": AnthropicModelConfig(
                            base_url="https://example.com/anthropic/v1",
                            model="claude-sonnet",
                            api_key_env="TEST_ANTHROPIC_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict("os.environ", {"TEST_ANTHROPIC_KEY": "test-key"}):
            model = provider.get_model("anthropic/claude")

        self.assertIsInstance(model, AnthropicMessagesModel)

    def test_anthropic_base_url_env_is_supported(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    anthropic={
                        "claude": AnthropicModelConfig(
                            base_url_env="TEST_ANTHROPIC_ENDPOINT",
                            model="claude-sonnet",
                            api_key_env="TEST_ANTHROPIC_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict(
            "os.environ",
            {
                "TEST_ANTHROPIC_ENDPOINT": "https://example.com/anthropic/v1/messages",
                "TEST_ANTHROPIC_KEY": "test-key",
            },
        ):
            model = provider.get_model("anthropic/claude")

        self.assertIsInstance(model, AnthropicMessagesModel)
        self.assertEqual("https://example.com/anthropic/v1/messages", model.config.base_url)

    def test_resolves_gemini_prefix_to_generate_content_adapter(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    gemini={
                        "flash": GeminiModelConfig(
                            endpoint="https://example.com/v1beta/models/flash:generateContent",
                            model="gemini-flash",
                            api_key_env="TEST_GEMINI_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict("os.environ", {"TEST_GEMINI_KEY": "test-key"}):
            model = provider.get_model("gemini/flash")

        self.assertIsInstance(model, GeminiGenerateContentModel)

    def test_gemini_endpoint_env_is_supported(self) -> None:
        provider = ContribArenaModelProvider(
            ModelsConfig(
                providers=ModelProvidersConfig(
                    gemini={
                        "flash": GeminiModelConfig(
                            endpoint_env="TEST_GEMINI_ENDPOINT",
                            model="gemini-flash",
                            api_key_env="TEST_GEMINI_KEY",
                        )
                    }
                )
            )
        )

        with patch.dict(
            "os.environ",
            {
                "TEST_GEMINI_ENDPOINT": "https://example.com/v1beta/models/flash:generateContent",
                "TEST_GEMINI_KEY": "test-key",
            },
        ):
            model = provider.get_model("gemini/flash")

        self.assertIsInstance(model, GeminiGenerateContentModel)
        self.assertEqual("https://example.com/v1beta/models/flash:generateContent", model.config.endpoint)

    def test_anthropic_prefix_requires_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing anthropic model config"):
            ContribArenaModelProvider().get_model("anthropic/missing")

    def test_gemini_prefix_requires_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing gemini model config"):
            ContribArenaModelProvider().get_model("gemini/missing")

    def test_rejects_unknown_provider_prefix(self) -> None:
        with self.assertRaises(ValueError):
            ContribArenaModelProvider().get_model("unknown/model")


if __name__ == "__main__":
    unittest.main()
