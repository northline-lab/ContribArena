from __future__ import annotations

import json
import unittest

from agents import function_tool
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.chat import ChatCompletionMessage
from openai.types.responses import ResponseFunctionToolCall

from contribarena.providers.action_guard import RECOVERY_TOOL_NAME, guard_model_response
from contribarena.providers.adapters import _repair_structured_output_message


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


class ProviderActionGuardTest(unittest.TestCase):
    def test_rejects_multiple_tool_calls_as_recovery_tool_call(self) -> None:
        response = _model_response(
            [
                _tool_call("aci_view", {"path": "repo/app.py"}),
                _tool_call("aci_search", {"pattern": "needle"}),
            ]
        )

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])

        self.assertEqual(1, len(guarded.output))
        recovery = guarded.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        self.assertEqual(RECOVERY_TOOL_NAME, recovery.name)
        payload = json.loads(recovery.arguments)
        self.assertEqual("multi_tool_action", payload["recovery_kind"])

    def test_rejects_missing_required_tool_argument(self) -> None:
        response = _model_response([_tool_call("sample_tool", {})])

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])

        recovery = guarded.output[0]
        self.assertIsInstance(recovery, ResponseFunctionToolCall)
        payload = json.loads(recovery.arguments)
        self.assertEqual("invalid_tool_arguments", payload["recovery_kind"])
        self.assertIn("missing required argument", payload["message"])

    def test_accepts_single_valid_tool_call(self) -> None:
        response = _model_response([_tool_call("sample_tool", {"path": "repo/app.py"})])

        guarded = guard_model_response(response, [_sample_tool, _recovery_tool])

        self.assertIs(guarded, response)

    def test_accepts_missing_argument_when_schema_has_default(self) -> None:
        response = _model_response([_tool_call("view_tool", {"path": "repo/app.py"})])

        guarded = guard_model_response(response, [_view_tool, _recovery_tool])

        self.assertIs(guarded, response)


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


def _tool_call(name: str, arguments: dict[str, object]) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments),
        call_id=f"call-{name}",
        name=name,
        type="function_call",
    )


def _model_response(output: list[ResponseFunctionToolCall]) -> ModelResponse:
    return ModelResponse(output=output, usage=Usage(), response_id="response-id")


if __name__ == "__main__":
    unittest.main()
