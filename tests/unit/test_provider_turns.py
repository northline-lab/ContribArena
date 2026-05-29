from __future__ import annotations

import unittest
from types import SimpleNamespace

from contribarena.providers.turns import (
    ProviderTurn,
    VisibleSegment,
    _is_hidden_item,
    _item_text,
)


class VisibleSegmentTest(unittest.TestCase):
    def test_defaults(self) -> None:
        segment = VisibleSegment(text="hello")
        self.assertEqual(segment.text, "hello")
        self.assertEqual(segment.position_hint, "unknown")
        self.assertIsNone(segment.provider_index)

    def test_explicit_values(self) -> None:
        segment = VisibleSegment(text="world", position_hint="before_tool", provider_index=5)
        self.assertEqual(segment.text, "world")
        self.assertEqual(segment.position_hint, "before_tool")
        self.assertEqual(segment.provider_index, 5)

    def test_frozen_dataclass(self) -> None:
        segment = VisibleSegment(text="test")
        with self.assertRaises(AttributeError):
            segment.text = "modified"  # type: ignore[misc]

    def test_all_position_literals(self) -> None:
        for position in ("before_tool", "after_tool", "unknown"):
            segment = VisibleSegment(text="x", position_hint=position)  # type: ignore[arg-type]
            self.assertEqual(segment.position_hint, position)


class ProviderTurnTest(unittest.TestCase):
    def test_defaults(self) -> None:
        turn = ProviderTurn()
        self.assertEqual(turn.visible_segments, [])
        self.assertEqual(turn.tool_calls, [])
        self.assertIsNone(turn.final_signal)
        self.assertEqual(turn.hidden_dropped_count, 0)
        self.assertIsNone(turn.provider_response_id)

    def test_explicit_values(self) -> None:
        segments = [VisibleSegment(text="a"), VisibleSegment(text="b")]
        turn = ProviderTurn(
            visible_segments=segments,
            tool_calls=[],
            final_signal="done",
            hidden_dropped_count=3,
            provider_response_id="resp_123",
        )
        self.assertEqual(len(turn.visible_segments), 2)
        self.assertEqual(turn.final_signal, "done")
        self.assertEqual(turn.hidden_dropped_count, 3)
        self.assertEqual(turn.provider_response_id, "resp_123")

    def test_factory_independence(self) -> None:
        turn1 = ProviderTurn()
        turn2 = ProviderTurn()
        turn1.visible_segments.append(VisibleSegment(text="x"))
        self.assertEqual(len(turn1.visible_segments), 1)
        self.assertEqual(len(turn2.visible_segments), 0)


class IsHiddenItemTest(unittest.TestCase):
    def test_reasoning_type_returns_true(self) -> None:
        item = SimpleNamespace()
        self.assertTrue(_is_hidden_item(item, "reasoning"))
        self.assertTrue(_is_hidden_item(item, "reasoning_content"))
        self.assertTrue(_is_hidden_item(item, "reasoning_thought"))

    def test_thinking_type_returns_true(self) -> None:
        item = SimpleNamespace()
        self.assertTrue(_is_hidden_item(item, "thinking"))

    def test_redacted_thinking_type_returns_true(self) -> None:
        item = SimpleNamespace()
        self.assertTrue(_is_hidden_item(item, "redacted_thinking"))

    def test_encrypted_content_returns_true(self) -> None:
        item = SimpleNamespace(encrypted_content="secret")
        self.assertTrue(_is_hidden_item(item, "message"))

    def test_normal_type_returns_false(self) -> None:
        item = SimpleNamespace()
        self.assertFalse(_is_hidden_item(item, "message"))
        self.assertFalse(_is_hidden_item(item, "function_call"))
        self.assertFalse(_is_hidden_item(item, ""))

    def test_empty_encrypted_content_returns_false(self) -> None:
        item = SimpleNamespace(encrypted_content="")
        self.assertFalse(_is_hidden_item(item, "message"))

    def test_none_encrypted_content_returns_false(self) -> None:
        item = SimpleNamespace(encrypted_content=None)
        self.assertFalse(_is_hidden_item(item, "message"))


class ItemTextTest(unittest.TestCase):
    def test_extracts_text_from_content_list(self) -> None:
        content1 = SimpleNamespace(type="text", text="hello")
        content2 = SimpleNamespace(type="text", text="world")
        item = SimpleNamespace(content=[content1, content2])
        result = _item_text(item)
        self.assertEqual(result, "hello\nworld")

    def test_skips_reasoning_content(self) -> None:
        content1 = SimpleNamespace(type="reasoning", text="hidden")
        content2 = SimpleNamespace(type="text", text="visible")
        item = SimpleNamespace(content=[content1, content2])
        result = _item_text(item)
        self.assertEqual(result, "visible")

    def test_skips_thinking_content(self) -> None:
        content = SimpleNamespace(type="thinking", text="thought")
        item = SimpleNamespace(content=[content])
        result = _item_text(item)
        self.assertEqual(result, "")

    def test_skips_redacted_thinking_content(self) -> None:
        content = SimpleNamespace(type="redacted_thinking", text="secret")
        item = SimpleNamespace(content=[content])
        result = _item_text(item)
        self.assertEqual(result, "")

    def test_falls_back_to_item_text_attribute(self) -> None:
        item = SimpleNamespace(text="fallback")
        result = _item_text(item)
        self.assertEqual(result, "fallback")

    def test_returns_empty_when_no_content(self) -> None:
        item = SimpleNamespace()
        result = _item_text(item)
        self.assertEqual(result, "")

    def test_returns_empty_when_content_empty(self) -> None:
        item = SimpleNamespace(content=[])
        result = _item_text(item)
        self.assertEqual(result, "")

    def test_skips_empty_text_in_content(self) -> None:
        content1 = SimpleNamespace(type="text", text="")
        content2 = SimpleNamespace(type="text", text="visible")
        item = SimpleNamespace(content=[content1, content2])
        result = _item_text(item)
        self.assertEqual(result, "visible")

    def test_handles_none_content_list(self) -> None:
        item = SimpleNamespace(content=None)
        result = _item_text(item)
        self.assertEqual(result, "")

    def test_content_with_no_text_attribute(self) -> None:
        content = SimpleNamespace(type="text")
        item = SimpleNamespace(content=[content])
        result = _item_text(item)
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
