from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call

from contribarena.engine.middleware.trace import traced_tool, _safe_result


class SafeResultTest(unittest.TestCase):
    """Tests for the _safe_result helper."""

    def test_pydantic_model_dump_json(self) -> None:
        model = MagicMock()
        model.model_dump.return_value = {"key": "value"}
        result = _safe_result(model)
        self.assertEqual({"key": "value"}, result)
        model.model_dump.assert_called_once_with(mode="json")

    def test_non_pydantic_object_stringified(self) -> None:
        result = _safe_result(42)
        self.assertEqual("42", result)

    def test_none_stringified(self) -> None:
        result = _safe_result(None)
        self.assertEqual("None", result)

    def test_list_stringified(self) -> None:
        result = _safe_result([1, 2, 3])
        self.assertEqual("[1, 2, 3]", result)

    def test_dict_without_model_dump_stringified(self) -> None:
        result = _safe_result({"a": 1})
        self.assertEqual("{'a': 1}", result)


class TracedToolTest(unittest.TestCase):
    """Tests for the traced_tool decorator."""

    def test_calls_wrapped_function_with_positional_args(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value="ok")
        wrapped = traced_tool(trace, "scout", "repo_search", fn)
        result = wrapped("arg1", "arg2")
        self.assertEqual("ok", result)
        fn.assert_called_once_with("arg1", "arg2")

    def test_calls_wrapped_function_with_keyword_args(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value=42)
        wrapped = traced_tool(trace, "work", "edit", fn)
        result = wrapped(x=1, y=2)
        self.assertEqual(42, result)
        fn.assert_called_once_with(x=1, y=2)

    def test_emits_started_event_before_call(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value="result")
        wrapped = traced_tool(trace, "scout", "repo_search", fn)
        wrapped("hello", key="world")
        trace.write.assert_any_call(
            "scout",
            "repo_search.started",
            {"args": ["hello"], "kwargs": {"key": "world"}},
        )

    def test_emits_finished_event_after_call(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value="result")
        wrapped = traced_tool(trace, "scout", "repo_search", fn)
        wrapped()
        trace.write.assert_any_call(
            "scout",
            "repo_search.finished",
            {"result": "result"},
        )

    def test_started_event_precedes_finished_event(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value=99)
        wrapped = traced_tool(trace, "work", "patch", fn)
        wrapped()
        expected_calls = [
            call("work", "patch.started", {"args": [], "kwargs": {}}),
            call("work", "patch.finished", {"result": "99"}),
        ]
        self.assertEqual(expected_calls, trace.write.call_args_list)

    def test_pydantic_result_serialized_as_dict(self) -> None:
        trace = MagicMock()
        model = MagicMock()
        model.model_dump.return_value = {"status": "ok"}
        fn = MagicMock(return_value=model)
        wrapped = traced_tool(trace, "work", "submit", fn)
        wrapped()
        trace.write.assert_any_call(
            "work",
            "submit.finished",
            {"result": {"status": "ok"}},
        )

    def test_raises_exception_propagates(self) -> None:
        trace = MagicMock()
        fn = MagicMock(side_effect=ValueError("boom"))
        wrapped = traced_tool(trace, "work", "fail", fn)
        with self.assertRaises(ValueError):
            wrapped()
        trace.write.assert_called_once_with(
            "work", "fail.started", {"args": [], "kwargs": {}}
        )

    def test_returns_wrapper_function(self) -> None:
        trace = MagicMock()
        fn = MagicMock(return_value=None)
        wrapped = traced_tool(trace, "scout", "search", fn)
        self.assertTrue(callable(wrapped))
        self.assertIsNot(wrapped, fn)


if __name__ == "__main__":
    unittest.main()
