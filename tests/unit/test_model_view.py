from __future__ import annotations

import json
import unittest

from contribarena.agent.model_view import (
    MODEL_VIEW_MAX_LIST_ITEMS,
    MODEL_VIEW_MAX_STRING_CHARS,
    TRUNCATION_MARKER,
    to_model_json,
)
from contribarena.models import CommandResult


class ModelViewTest(unittest.TestCase):
    def test_command_result_projection_caps_model_visible_output(self) -> None:
        result = CommandResult(
            command="pytest -q",
            stdout="x" * (MODEL_VIEW_MAX_STRING_CHARS + 100),
            stderr="",
            exit_code=0,
            duration_seconds=0.01,
        )

        projected = json.loads(to_model_json(result))

        self.assertEqual("pytest -q", projected["command"])
        self.assertLess(len(projected["stdout"]), MODEL_VIEW_MAX_STRING_CHARS + 100)
        self.assertIn(TRUNCATION_MARKER, projected["stdout"])

    def test_list_projection_keeps_bounded_prefix_and_marker(self) -> None:
        projected = json.loads(to_model_json([{"index": index} for index in range(40)]))

        self.assertEqual(MODEL_VIEW_MAX_LIST_ITEMS + 1, len(projected))
        self.assertEqual(0, projected[0]["index"])
        self.assertEqual(10, projected[-1]["truncated_items"])
        self.assertEqual(TRUNCATION_MARKER, projected[-1]["message"])


if __name__ == "__main__":
    unittest.main()
