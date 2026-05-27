from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.operator_events import (
    OperatorProgressWriter,
    _bounded_list,
    _bounded_payload,
    _bounded_value,
    truncate_for_operator,
)


class TruncateForOperatorTest(unittest.TestCase):
    def test_short_string_passes_through(self) -> None:
        self.assertEqual("hello", truncate_for_operator("hello"))

    def test_exact_length_passes_through(self) -> None:
        text = "a" * 180
        self.assertEqual(text, truncate_for_operator(text))

    def test_long_string_is_truncated(self) -> None:
        text = "a" * 200
        result = truncate_for_operator(text)
        self.assertTrue(result.endswith("...[truncated]"))
        self.assertEqual(len(result), 180 + len("...[truncated]"))

    def test_custom_max_chars(self) -> None:
        text = "hello world"
        result = truncate_for_operator(text, max_chars=5)
        self.assertEqual(result, "hello...[truncated]")

    def test_non_string_object(self) -> None:
        result = truncate_for_operator(42)
        self.assertEqual(result, "42")


class BoundedListTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _bounded_list([]))

    def test_short_list_preserved(self) -> None:
        items = ["a", "bb", "ccc"]
        result = _bounded_list(items)
        self.assertEqual(items, result)

    def test_long_list_truncated_to_20(self) -> None:
        items = [str(i) for i in range(30)]
        result = _bounded_list(items)
        self.assertEqual(20, len(result))

    def test_long_items_truncated(self) -> None:
        items = ["a" * 200, "b"]
        result = _bounded_list(items)
        self.assertTrue(result[0].endswith("...[truncated]"))
        self.assertEqual("b", result[1])


class BoundedPayloadTest(unittest.TestCase):
    def test_empty_dict(self) -> None:
        self.assertEqual({}, _bounded_payload({}))

    def test_small_dict_preserved(self) -> None:
        payload = {"key": "value", "num": 42}
        result = _bounded_payload(payload)
        self.assertEqual(payload, result)

    def test_long_dict_truncated_to_30_keys(self) -> None:
        payload = {f"k{i}": f"v{i}" for i in range(40)}
        result = _bounded_payload(payload)
        self.assertEqual(31, len(result))
        self.assertTrue(result["truncated"])

    def test_nested_dict_bounded(self) -> None:
        payload = {"outer": {"inner_key": "inner_value" * 50}}
        result = _bounded_payload(payload)
        self.assertTrue(result["outer"]["inner_key"].endswith("...[truncated]"))

    def test_nested_list_bounded(self) -> None:
        payload = {"items": ["x" * 300] * 25}
        result = _bounded_payload(payload)
        self.assertEqual(20, len(result["items"]))
        self.assertTrue(result["items"][0].endswith("...[truncated]"))


class BoundedValueTest(unittest.TestCase):
    def test_dict(self) -> None:
        result = _bounded_value({"k": "v"})
        self.assertEqual({"k": "v"}, result)

    def test_list(self) -> None:
        result = _bounded_value([1, 2, 3])
        self.assertEqual([1, 2, 3], result)

    def test_string(self) -> None:
        result = _bounded_value("hello")
        self.assertEqual("hello", result)

    def test_long_string_truncated(self) -> None:
        result = _bounded_value("x" * 300)
        self.assertTrue(result.endswith("...[truncated]"))

    def test_int_preserved(self) -> None:
        self.assertEqual(42, _bounded_value(42))

    def test_float_preserved(self) -> None:
        self.assertEqual(3.14, _bounded_value(3.14))

    def test_bool_preserved(self) -> None:
        self.assertTrue(_bounded_value(True))
        self.assertFalse(_bounded_value(False))

    def test_none_preserved(self) -> None:
        self.assertIsNone(_bounded_value(None))

    def test_other_object_truncated(self) -> None:
        class CustomObj:
            def __str__(self) -> str:
                return "obj"
        result = _bounded_value(CustomObj())
        self.assertEqual("obj", result)


class OperatorProgressWriterTest(unittest.TestCase):
    def test_write_creates_file_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-123")
            writer.write("scout", "complete", "Found 3 repos")
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(1, len(lines))
            event = json.loads(lines[0])
            self.assertEqual("run-123", event["run_id"])
            self.assertEqual("harness", event["source"])
            self.assertEqual("scout", event["phase"])
            self.assertEqual("complete", event["status"])
            self.assertEqual("Found 3 repos", event["summary"])
            self.assertEqual([], event["evidence"])
            self.assertEqual({}, event["payload"])

    def test_write_with_evidence_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-456")
            writer.write(
                "work", "active", "Editing file",
                evidence=["workspace:repo/app.py", "tool_call:12"],
                payload={"file": "repo/app.py", "lines": 100},
            )
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            event = json.loads(lines[0])
            self.assertEqual("run-456", event["run_id"])
            self.assertEqual("work", event["phase"])
            self.assertEqual("active", event["status"])
            self.assertEqual(
                ["workspace:repo/app.py", "tool_call:12"], event["evidence"]
            )
            self.assertEqual({"file": "repo/app.py", "lines": 100}, event["payload"])

    def test_multiple_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-789")
            writer.write("scout", "active", "step-1")
            writer.write("work", "active", "step-2")
            writer.write("review", "complete", "step-3")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(3, len(lines))
            summaries = [json.loads(line)["summary"] for line in lines]
            self.assertEqual(["step-1", "step-2", "step-3"], summaries)

    def test_payload_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-big")
            large_payload = {f"key_{i}": f"value_{i}" for i in range(40)}
            writer.write("work", "active", "large", payload=large_payload)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            event = json.loads(lines[0])
            self.assertTrue(event["payload"]["truncated"])

    def test_evidence_list_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-ev")
            ev_list = [f"workspace:file_{i}.py" for i in range(30)]
            writer.write("work", "active", "many evidence", evidence=ev_list)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            event = json.loads(lines[0])
            self.assertLessEqual(len(event["evidence"]), 20)

    def test_summary_truncated_when_long(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-long")
            writer.write("work", "active", "a" * 500)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            event = json.loads(lines[0])
            self.assertTrue(event["summary"].endswith("...[truncated]"))

    def test_stream_flag_does_not_break_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-stream", stream=True)
            writer.write("work", "active", "streamed")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(1, len(lines))
            event = json.loads(lines[0])
            self.assertEqual("streamed", event["summary"])

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "dir" / "events.jsonl"
            writer = OperatorProgressWriter(path, run_id="run-mkdir")
            writer.write("scout", "complete", "done")
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(1, len(lines))

