from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.operator_events import (
    OperatorProgressWriter,
    truncate_for_operator,
)


class TruncateForOperatorTest(unittest.TestCase):
    def test_short_text_passes_through(self) -> None:
        self.assertEqual("hello", truncate_for_operator("hello"))

    def test_default_max_chars_truncates(self) -> None:
        text = "a" * 300
        result = truncate_for_operator(text)
        self.assertTrue(result.endswith("...[truncated]"))
        self.assertTrue(len(result) < len(text))

    def test_custom_max_chars(self) -> None:
        text = "a" * 50
        result = truncate_for_operator(text, max_chars=30)
        self.assertEqual(result, "a" * 30 + "...[truncated]")

    def test_text_within_limit_is_not_truncated(self) -> None:
        text = "a" * 180
        self.assertEqual(text, truncate_for_operator(text))

    def test_non_string_input_is_str_coerced(self) -> None:
        self.assertEqual("42", truncate_for_operator(42))


class OperatorProgressWriterTest(unittest.TestCase):
    def test_write_creates_jsonl_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")
            writer.write("scout", "active", "Looking for tasks")

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(event["run_id"], "run-1")
            self.assertEqual(event["phase"], "scout")
            self.assertEqual(event["status"], "active")
            self.assertEqual(event["summary"], "Looking for tasks")
            self.assertEqual(event["source"], "harness")
            self.assertIn("ts", event)

    def test_write_with_evidence_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-2")
            writer.write(
                "work",
                "complete",
                "Patch submitted",
                evidence=["artifact:repo/app.py#L10"],
                payload={"files": 2},
            )

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["evidence"], ["artifact:repo/app.py#L10"])
            self.assertEqual(event["payload"]["files"], 2)

    def test_write_appends_to_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-3")
            writer.write("scout", "active", "Step 1")
            writer.write("scout", "active", "Step 2")

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_write_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-4")
            writer.write("scout", "active", "Created dirs")

            self.assertTrue(path.exists())
            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["summary"], "Created dirs")

    def test_write_truncates_long_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-5")
            long_summary = "x" * 300
            writer.write("scout", "active", long_summary)

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(event["summary"].endswith("...[truncated]"))
            self.assertTrue(len(event["summary"]) < len(long_summary))

    def test_stream_flag_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-6", stream=True)
            writer.write("scout", "active", "Streaming test")

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)

    def test_custom_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-7")
            writer.write("review", "active", "Reviewing", source="agent")

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["source"], "agent")

    def test_long_evidence_items_are_bounded(self) -> None:
        # Tests bounded list logic through the public API
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-8")
            long_evidence = ["x" * 300 for _ in range(30)]
            writer.write("work", "active", "Big evidence", evidence=long_evidence)

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(len(event["evidence"]) <= 20)
            self.assertTrue(any(item.endswith("...[truncated]") for item in event["evidence"]))

    def test_large_payload_is_bounded(self) -> None:
        # Tests bounded payload logic through the public API
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-9")
            large_payload = {f"key_{i}": "v" * 300 for i in range(40)}
            writer.write("review", "active", "Big payload", payload=large_payload)

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(len(event["payload"]) <= 31)
            self.assertTrue(event["payload"].get("truncated", False))

    def test_nested_payload_values_are_bounded(self) -> None:
        # Tests _bounded_value recursion through the public API
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = OperatorProgressWriter(path, "run-10")
            nested_payload = {
                "inner": {
                    "text": "a" * 300,
                    "numbers": [1, 2, 3],
                }
            }
            writer.write("work", "active", "Nested payload", payload=nested_payload)

            event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            inner_text = event["payload"]["inner"]["text"]
            self.assertTrue(inner_text.endswith("...[truncated]"))


if __name__ == "__main__":
    unittest.main()
