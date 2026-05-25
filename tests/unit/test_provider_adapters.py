from __future__ import annotations

import json
import unittest

from agents import function_tool
from agents.items import ModelResponse
from agents.models.chatcmpl_converter import Converter
from agents.usage import Usage
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.responses import ResponseFunctionToolCall
from openai.types.responses.response_reasoning_item import ResponseReasoningItem

from contribarena.providers.action_guard import (
    RECOVERY_TOOL_NAME,
    guard_model_response,
    guard_structured_model_response,
    visible_text_from_turn,
)
from contribarena.providers.adapters import (
    _apply_anthropic_thinking,
    _anthropic_response_to_chat_message,
    _gemini_response_to_chat_message,
    _repair_structured_output_message,
    _to_anthropic_tools,
    _to_gemini_contents,
    _to_gemini_tools,
)
from contribarena.config.schema import AnthropicModelConfig
from contribarena.models.assistant_updates import AssistantUpdate
from contribarena.providers.turns import provider_turn_from_response


class ProviderAdapterRepairTest(unittest.TestCase):
    def test_repairs_fenced_json_with_prefix_text(self) -> None:
        message = ChatCompletionMessage(
            role="assistant",
            content='Here is the result:\n```json\n{"status": "completed"}\n```',
        )

        repaired = _repair_structured_output_message(message)

        self.assertEqual(json.loads(repaired.content or "{}"), {"status": "completed"})

    def test_repairs_gemini_wrapped_json(self) -> None:
        message = ChatCompletionMessage(
            role="assistant",
            content='{"status": "completed", "\nrepo": {"name": "openai\n-agents-python"}, "risk": "medium\n", "duration": 12.3\n45}',
        )

        repaired = _repair_structured_output_message(message)
        payload = json.loads(repaired.content or "{}")

        self.assertEqual(payload["repo"]["name"], "openai\n-agents-python")
        self.assertEqual(payload["risk"], "medium")
        self.assertEqual(payload["duration"], 12.345)


class ProviderToolSchemaTest(unittest.TestCase):
    def test_anthropic_thinking_defaults_to_adaptive_high(self) -> None:
        body: dict[str, object] = {}

        _apply_anthropic_thinking(
            body,
            AnthropicModelConfig(base_url="https://example.com/anthropic/v1"),
        )

        self.assertEqual({"type": "adaptive"}, body["thinking"])
        self.assertEqual({"effort": "high"}, body["output_config"])

    def test_anthropic_thinking_can_use_legacy_budget_tokens(self) -> None:
        body: dict[str, object] = {}

        _apply_anthropic_thinking(
            body,
            AnthropicModelConfig(
                base_url="https://example.com/anthropic/v1",
                thinking_type="enabled",
                thinking_budget_tokens=4096,
            ),
        )

        self.assertEqual(
            {"type": "enabled", "budget_tokens": 4096},
            body["thinking"],
        )
        self.assertNotIn("output_config", body)

    def test_anthropic_thinking_can_be_disabled(self) -> None:
        body: dict[str, object] = {}

        _apply_anthropic_thinking(
            body,
            AnthropicModelConfig(
                base_url="https://example.com/anthropic/v1",
                thinking_enabled=False,
            ),
        )

        self.assertEqual({}, body)

    def test_anthropic_and_gemini_preserve_aci_apply_patch_schema(self) -> None:
        anthropic_tools = _to_anthropic_tools([_patch_tool])
        gemini_tools = _to_gemini_tools([_patch_tool])

        anthropic_schema = anthropic_tools[0]["input_schema"]
        gemini_schema = gemini_tools[0]["functionDeclarations"][0]["parameters"]

        self.assertEqual("aci_apply_patch", anthropic_tools[0]["name"])
        self.assertEqual(
            "aci_apply_patch",
            gemini_tools[0]["functionDeclarations"][0]["name"],
        )
        for schema in [anthropic_schema, gemini_schema]:
            self.assertIn("operations_json", schema["properties"])
            self.assertIn("rationale", schema["properties"])
            self.assertIn("expected_files_json", schema["properties"])
            self.assertIn("operations_json", schema["required"])

    def test_gemini_groups_multiple_tool_outputs_after_multi_tool_call_turn(self) -> None:
        contents = _to_gemini_contents(
            [
                {
                    "type": "function_call",
                    "call_id": "call-a",
                    "name": "repo_search",
                    "arguments": "{}",
                },
                {
                    "type": "function_call",
                    "call_id": "call-b",
                    "name": "aci_runtime_get_context",
                    "arguments": '{"scope":"run"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-a",
                    "output": "search ok",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-b",
                    "output": "runtime ok",
                },
            ]
        )

        self.assertEqual("model", contents[0]["role"])
        self.assertEqual(2, len(contents[0]["parts"]))
        self.assertEqual("user", contents[1]["role"])
        self.assertEqual(2, len(contents[1]["parts"]))
        response_names = [
            part["functionResponse"]["name"]
            for part in contents[1]["parts"]
        ]
        self.assertEqual(["repo_search", "aci_runtime_get_context"], response_names)


class ProviderActionGuardTest(unittest.TestCase):
    def test_rejects_multiple_valid_tool_calls(self) -> None:
        response = _model_response(
            [
                _tool_call("sample_tool", {"path": "repo/app.py"}),
                _tool_call("view_tool", {"path": "repo/other.py"}),
            ]
        )

        guarded = guard_model_response(response, [_sample_tool, _view_tool, _recovery_tool])

        recovery = guarded.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        payload = json.loads(recovery.arguments)
        self.assertEqual("multiple_tool_calls", payload["recovery_kind"])

    def test_rejects_invalid_call_inside_multiple_tool_calls(self) -> None:
        response = _model_response(
            [
                _tool_call("sample_tool", {"path": "repo/app.py"}),
                _tool_call("sample_tool", {}),
            ]
        )

        guarded = guard_model_response(response, [_sample_tool, _view_tool, _recovery_tool])

        self.assertEqual(1, len(guarded.output))
        recovery = guarded.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        self.assertEqual(RECOVERY_TOOL_NAME, recovery.name)
        payload = json.loads(recovery.arguments)
        self.assertEqual("multiple_tool_calls", payload["recovery_kind"])

    def test_captures_visible_text_with_single_tool_call(self) -> None:
        captured: list[AssistantUpdate] = []
        response = _text_and_tool_response("I will inspect the file.", "sample_tool", {"path": "repo/app.py"})

        guarded = guard_structured_model_response(
            response,
            [_sample_tool, _recovery_tool],
            _StructuredSchema(),
            update_builder=lambda turn, call: AssistantUpdate(
                run_id="run-a",
                text="\n".join(segment.text for segment in turn.visible_segments),
                tool_name=call.name if call else "",
                evidence_refs=[f"tool_call:{call.call_id}"] if call else [],
                hidden_dropped_count=turn.hidden_dropped_count,
            ),
            update_sink=captured.append,
        )

        self.assertIs(guarded, response)
        self.assertEqual(1, len(captured))
        self.assertEqual("I will inspect the file.", captured[0].text)
        self.assertEqual("sample_tool", captured[0].tool_name)
        self.assertEqual(["tool_call:call-sample_tool"], captured[0].evidence_refs)

    def test_rejects_missing_required_tool_argument(self) -> None:
        response = _model_response([_tool_call("sample_tool", {})])

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])

        recovery = guarded.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        payload = json.loads(recovery.arguments)
        self.assertEqual("invalid_tool_arguments", payload["recovery_kind"])
        self.assertIn("missing required argument", payload["message"])

    def test_recovery_tool_call_ids_are_unique(self) -> None:
        response = _model_response([_tool_call("sample_tool", {})])

        first = guard_model_response(response, [_sample_tool, _recovery_tool]).output[0]
        second = guard_model_response(response, [_sample_tool, _recovery_tool]).output[0]

        self.assertIsInstance(first, ResponseFunctionToolCall)
        self.assertIsInstance(second, ResponseFunctionToolCall)
        self.assertNotEqual(first.call_id, second.call_id)
        self.assertEqual(first.call_id, first.id)
        self.assertEqual(second.call_id, second.id)
        self.assertTrue(first.call_id.startswith("contribarena-invalid-action-recovery-"))

    def test_accepts_single_valid_tool_call(self) -> None:
        response = _model_response([_tool_call("sample_tool", {"path": "repo/app.py"})])

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])

        self.assertIs(guarded, response)

    def test_accepts_missing_argument_when_schema_has_default(self) -> None:
        response = _model_response([_tool_call("view_tool", {"path": "repo/app.py"})])

        guarded = guard_model_response(response, [_view_tool, _recovery_tool])

        self.assertIs(guarded, response)

    def test_rejects_plain_text_when_structured_output_is_required(self) -> None:
        response = ModelResponse(
            output=Converter.message_to_output_items(
                ChatCompletionMessage(role="assistant", content="Proceeding to clone."),
                provider_data={"model": "adapter"},
            ),
            usage=Usage(),
            response_id="response-id",
        )

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])
        self.assertIs(guarded, response)

        from contribarena.providers.action_guard import guard_structured_model_response

        structured = guard_structured_model_response(
            response,
            [_sample_tool, _recovery_tool],
            _StructuredSchema(),
        )

        recovery = structured.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        self.assertEqual(RECOVERY_TOOL_NAME, recovery.name)
        payload = json.loads(recovery.arguments)
        self.assertEqual("non_tool_text_response", payload["recovery_kind"])

    def test_converts_text_json_tool_intent_to_tool_call(self) -> None:
        examples = [
            {"name": "sample_tool", "arguments": {"path": "repo/app.py"}},
            [{"name": "sample_tool", "arguments": {"path": "repo/app.py"}}],
            {"call": "sample_tool", "args": {"path": "repo/app.py"}},
            ["sample_tool", {"path": "repo/app.py"}],
        ]

        for payload in examples:
            with self.subTest(payload=payload):
                response = _text_response(json.dumps(payload))

                guarded = guard_structured_model_response(
                    response,
                    [_sample_tool, _recovery_tool],
                    _StructuredSchema(),
                )

                self.assertEqual(1, len(guarded.output))
                call = guarded.output[0]
                self.assertIsInstance(call, ResponseFunctionToolCall)
                self.assertEqual("sample_tool", call.name)
                self.assertEqual({"path": "repo/app.py"}, json.loads(call.arguments))

    def test_leaves_non_tool_json_content_for_harness_review(self) -> None:
        response = _text_response('{"status": "calling_tool"}')

        guarded = guard_structured_model_response(
            response,
            [_sample_tool, _recovery_tool],
            _StructuredSchema(),
        )

        self.assertIs(guarded, response)

    def test_anthropic_drops_thinking_blocks_from_visible_text(self) -> None:
        message, hidden = _anthropic_response_to_chat_message(
            {
                "content": [
                    {"type": "thinking", "thinking": "hidden chain"},
                    {"type": "text", "text": "Visible update."},
                    {"type": "redacted_thinking", "data": "..."},
                ]
            }
        )

        self.assertEqual("Visible update.", message.content)
        self.assertEqual(2, hidden)

    def test_gemini_drops_thought_text_and_carries_signature(self) -> None:
        message, hidden = _gemini_response_to_chat_message(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hidden", "thought": True},
                                {"text": "Visible update."},
                                {
                                    "functionCall": {"name": "sample_tool", "args": {"path": "repo/app.py"}},
                                    "thoughtSignature": "opaque",
                                },
                            ]
                        }
                    }
                ]
            }
        )

        self.assertEqual("Visible update.", message.content)
        self.assertEqual(2, hidden)
        self.assertEqual("opaque", message.tool_calls[0].extra_content["google"]["thought_signature"])

    def test_provider_turn_drops_openai_reasoning_items(self) -> None:
        reasoning = ResponseReasoningItem(
            id="rsn_123",
            summary=[],
            type="reasoning",
        )
        response = ModelResponse(
            output=[reasoning, *_text_response("Visible update.").output],
            usage=Usage(),
            response_id="response-id",
        )

        turn = provider_turn_from_response(response)

        self.assertEqual("Visible update.", turn.visible_segments[0].text)
        self.assertEqual(1, turn.hidden_dropped_count)

    def test_visible_text_is_redacted_and_truncated(self) -> None:
        response = _text_response("api_key=abcdef1234567890 " + ("x" * 600))

        text, redacted, truncated = visible_text_from_turn(provider_turn_from_response(response), max_chars=80)

        self.assertTrue(redacted)
        self.assertTrue(truncated)
        self.assertIn("***", text)
        self.assertLessEqual(len(text), 80)


@function_tool(name_override="sample_tool")
def _sample_tool(path: str) -> str:
    """Sample tool used to expose a strict JSON schema."""
    return path


@function_tool(name_override="view_tool")
def _view_tool(path: str, start_line: int = 1) -> str:
    """Sample tool with a defaulted argument."""
    return f"{path}:{start_line}"


@function_tool(name_override=RECOVERY_TOOL_NAME)
def _recovery_tool(recovery_kind: str, message: str, attempted_tool: str = "") -> str:
    """Recovery tool used by the action guard."""
    return f"{recovery_kind}: {message}: {attempted_tool}"


@function_tool(name_override="aci_apply_patch")
def _patch_tool(
    operations_json: str,
    rationale: str = "",
    expected_files_json: str = "[]",
) -> str:
    """Patch tool used to verify provider-neutral edit schema conversion."""
    return f"{operations_json}: {rationale}: {expected_files_json}"


def _tool_call(name: str, arguments: dict[str, object]) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments),
        call_id=f"call-{name}",
        name=name,
        type="function_call",
    )


def _model_response(output: list[ResponseFunctionToolCall]) -> ModelResponse:
    return ModelResponse(output=output, usage=Usage(), response_id="response-id")


def _text_and_tool_response(
    text: str,
    name: str,
    arguments: dict[str, object],
) -> ModelResponse:
    message = ChatCompletionMessage(
        role="assistant",
        content=text,
        tool_calls=[
            ChatCompletionMessageFunctionToolCall(
                id=f"call-{name}",
                type="function",
                function=Function(name=name, arguments=json.dumps(arguments)),
            )
        ],
    )
    return ModelResponse(
        output=Converter.message_to_output_items(message, provider_data={"model": "adapter"}),
        usage=Usage(),
        response_id="response-id",
    )


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        output=Converter.message_to_output_items(
            ChatCompletionMessage(role="assistant", content=text),
            provider_data={"model": "adapter"},
        ),
        usage=Usage(),
        response_id="response-id",
    )


class _StructuredSchema:
    def is_plain_text(self) -> bool:
        return False

    def json_schema(self) -> dict[str, object]:
        return {
            "type": "object",
            "required": ["status", "searched_query", "note"],
            "properties": {
                "status": {"type": "string"},
                "searched_query": {"type": "string"},
                "note": {"type": "string"},
            },
        }


if __name__ == "__main__":
    unittest.main()
