from __future__ import annotations

import json
import unittest

from pydantic import BaseModel

from contribarena.agent.model_view import (
    MODEL_VIEW_MAX_LIST_ITEMS,
    MODEL_VIEW_MAX_STRING_CHARS,
    TRUNCATION_MARKER,
    to_model_json,
)
from contribarena.models import CommandResult


class SampleProjectionModel(BaseModel):
    name: str
    nested: dict[object, object]


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

    def test_string_at_limit_is_preserved_without_marker(self) -> None:
        projected = json.loads(to_model_json({"text": "x" * MODEL_VIEW_MAX_STRING_CHARS}))

        self.assertEqual("x" * MODEL_VIEW_MAX_STRING_CHARS, projected["text"])
        self.assertNotIn(TRUNCATION_MARKER, projected["text"])

    def test_dict_projection_coerces_keys_and_caps_nested_strings(self) -> None:
        projected = json.loads(
            to_model_json({1: {"nested": "x" * (MODEL_VIEW_MAX_STRING_CHARS + 1)}})
        )

        self.assertIn("1", projected)
        self.assertIn(TRUNCATION_MARKER, projected["1"]["nested"])

    def test_pydantic_models_are_dumped_before_projection(self) -> None:
        value = SampleProjectionModel(
            name="example",
            nested={1: ["x" * (MODEL_VIEW_MAX_STRING_CHARS + 1)]},
        )

        projected = json.loads(to_model_json(value))

        self.assertEqual("example", projected["name"])
        self.assertIn("1", projected["nested"])
        self.assertIn(TRUNCATION_MARKER, projected["nested"]["1"][0])


if __name__ == "__main__":
    unittest.main()
