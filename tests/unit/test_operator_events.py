from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.operator_events import (
    OperatorProgressWriter,
    truncate_for_operator,
    _bounded_list,
    _bounded_payload,
    _bounded_value,
)


class OperatorProgressWriterTest(unittest.TestCase):
    def test_constructor_creates_parent_directory_and_touches_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            self.assertTrue(path.parent.exists())
            self.assertTrue(path.exists())
            self.assertEqual("run-1", writer.run_id)
            self.assertFalse(writer.stream)

    def test_constructor_stream_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-2", stream=True)
            self.assertTrue(writer.stream)

    def test_constructor_reuses_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            Path(tmp).mkdir(parents=True, exist_ok=True)
            OperatorProgressWriter(path, "run-3")
            self.assertTrue(path.exists())

    def test_write_appends_jsonl_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "started", "Discovering repos")

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual("run-1", event["run_id"])
            self.assertEqual("harness", event["source"])
            self.assertEqual("scout", event["phase"])
            self.assertEqual("started", event["status"])
            self.assertEqual("Discovering repos", event["summary"])
            self.assertEqual([], event["evidence"])
            self.assertEqual({}, event["payload"])

    def test_write_with_source_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("work", "active", "Editing code", source="agent")

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual("agent", event["source"])

    def test_write_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("review", "pass", "All checks passed", evidence=["ref-1", "ref-2"])

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual(["ref-1", "ref-2"], event["evidence"])

    def test_write_with_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "done", "Done", payload={"files": 3})

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual({"files": 3}, event["payload"])

    def test_write_truncates_long_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            long_summary = "x" * 300
            writer.write("scout", "started", long_summary)

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertTrue(event["summary"].endswith("...[truncated]"))
            self.assertTrue(len(event["summary"]) <= 240 + len("...[truncated]"))

    def test_multiple_writes_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "started", "Discovering")
            writer.write("work", "active", "Editing")
            writer.write("review", "pass", "All checks passed")

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(3, len(lines))
            phases = [json.loads(line)["phase"] for line in lines]
            self.assertEqual(["scout", "work", "review"], phases)

    def test_event_has_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "started", "Discovering")

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertIn("ts", event)
            self.assertTrue(len(event["ts"]) > 0)


class TruncateForOperatorTest(unittest.TestCase):
    def test_short_string_passthrough(self) -> None:
        result = truncate_for_operator("hello world")
        self.assertEqual("hello world", result)

    def test_default_max_chars_is_180(self) -> None:
        text = "a" * 180
        self.assertEqual(text, truncate_for_operator(text))
        long = "a" * 181
        self.assertTrue(truncate_for_operator(long).endswith("...[truncated]"))

    def test_custom_max_chars(self) -> None:
        text = "b" * 50
        self.assertEqual(text, truncate_for_operator(text, max_chars=60))
        long = "b" * 61
        truncated = truncate_for_operator(long, max_chars=60)
        self.assertTrue(truncated.endswith("...[truncated]"))
        self.assertEqual(long[:60], truncated[:60])

    def test_non_string_input(self) -> None:
        self.assertEqual("42", truncate_for_operator(42))
        self.assertEqual("3.14", truncate_for_operator(3.14))
        self.assertEqual("True", truncate_for_operator(True))
        self.assertEqual("None", truncate_for_operator(None))

    def test_empty_string(self) -> None:
        self.assertEqual("", truncate_for_operator(""))

    def test_truncated_text_prefix_matches_original(self) -> None:
        text = "abcdefghij" * 20  # 200 chars
        truncated = truncate_for_operator(text, max_chars=50)
        self.assertTrue(truncated.startswith(text[:50]))
        self.assertTrue(truncated.endswith("...[truncated]"))


class BoundedListTest(unittest.TestCase):
    def test_empty_list(self) -> None:
        self.assertEqual([], _bounded_list([]))

    def test_short_list_no_truncation(self) -> None:
        values = ["a", "b", "c"]
        result = _bounded_list(values)
        self.assertEqual(["a", "b", "c"], result)

    def test_long_list_truncated_to_max_items(self) -> None:
        values = [str(i) for i in range(25)]
        result = _bounded_list(values)
        self.assertEqual(20, len(result))
        self.assertEqual("0", result[0])

    def test_custom_max_items(self) -> None:
        values = [str(i) for i in range(10)]
        result = _bounded_list(values, max_items=5)
        self.assertEqual(5, len(result))

    def test_values_are_truncated(self) -> None:
        values = ["x" * 300, "short"]
        result = _bounded_list(values, max_items=20)
        self.assertTrue(result[0].endswith("...[truncated]"))
        self.assertEqual("short", result[1])


class BoundedPayloadTest(unittest.TestCase):
    def test_empty_payload(self) -> None:
        self.assertEqual({}, _bounded_payload({}))

    def test_small_payload_no_truncation(self) -> None:
        payload = {"key1": "value1", "key2": 42}
        result = _bounded_payload(payload)
        self.assertEqual("value1", result["key1"])
        self.assertEqual(42, result["key2"])

    def test_large_payload_truncated_at_max_items(self) -> None:
        payload = {f"k{i}": i for i in range(35)}
        result = _bounded_payload(payload)
        # _bounded_payload adds a "truncated" sentinel key when it exceeds
        # max_items, so the result has 30 data keys + 1 sentinel = 31 total.
        self.assertEqual(31, len(result))
        self.assertIn("truncated", result)

    def test_nested_dict_recursion(self) -> None:
        payload = {"outer": {"inner": "value"}}
        result = _bounded_payload(payload)
        self.assertEqual("value", result["outer"]["inner"])

    def test_string_values_truncated(self) -> None:
        payload = {"key": "x" * 300}
        result = _bounded_payload(payload)
        self.assertTrue(result["key"].endswith("...[truncated]"))

    def test_custom_max_items(self) -> None:
        payload = {f"k{i}": i for i in range(40)}
        result = _bounded_payload(payload, max_items=10)
        # 10 data keys + 1 "truncated" sentinel = 11 total keys
        self.assertEqual(11, len(result))
        self.assertIn("truncated", result)


class BoundedValueTest(unittest.TestCase):
    def test_dict_value(self) -> None:
        result = _bounded_value({"a": "b"})
        self.assertIsInstance(result, dict)
        self.assertEqual("b", result["a"])

    def test_list_value(self) -> None:
        result = _bounded_value([1, 2, 3])
        self.assertIsInstance(result, list)
        self.assertEqual([1, 2, 3], result)

    def test_string_value_truncated(self) -> None:
        result = _bounded_value("x" * 300)
        self.assertTrue(result.endswith("...[truncated]"))

    def test_int_value_preserved(self) -> None:
        self.assertEqual(42, _bounded_value(42))

    def test_float_value_preserved(self) -> None:
        self.assertEqual(3.14, _bounded_value(3.14))

    def test_bool_value_preserved(self) -> None:
        self.assertEqual(True, _bounded_value(True))

    def test_none_preserved(self) -> None:
        self.assertIsNone(_bounded_value(None))

    def test_unknown_type_coerced_to_string(self) -> None:
        result = _bounded_value(object())
        self.assertIsInstance(result, str)

    def test_long_list_value_truncated(self) -> None:
        result = _bounded_value([str(i) for i in range(25)])
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) <= 20)


class TruncationIntegrationTest(unittest.TestCase):
    def test_writer_evidence_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "done", "Done", evidence=["x" * 300])

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertTrue(event["evidence"][0].endswith("...[truncated]"))

    def test_writer_payload_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "done", "Done", payload={"log": "x" * 300})

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertTrue(event["payload"]["log"].endswith("...[truncated]"))

    def test_writer_evidence_empty_when_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "done", "Done", evidence=None)

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual([], event["evidence"])

    def test_writer_payload_empty_when_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "done", "Done", payload=None)

            [line] = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(line)
            self.assertEqual({}, event["payload"])


if __name__ == "__main__":
    unittest.main()
