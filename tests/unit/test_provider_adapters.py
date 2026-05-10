from __future__ import annotations

import json
import unittest

from openai.types.chat import ChatCompletionMessage

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


if __name__ == "__main__":
    unittest.main()
