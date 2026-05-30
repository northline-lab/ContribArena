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
    def test_short_values_are_preserved(self) -> None:
        self.assertEqual("ready", truncate_for_operator("ready", max_chars=10))

    def test_non_string_values_are_stringified(self) -> None:
        self.assertEqual("123", truncate_for_operator(123, max_chars=10))

    def test_long_values_are_truncated_with_marker(self) -> None:
        result = truncate_for_operator("abcdefghijklmnopqrstuvwxyz", max_chars=8)

        self.assertEqual("abcdefgh...[truncated]", result)


class BoundedListTest(unittest.TestCase):
    def test_values_are_stringified_truncated_and_limited(self) -> None:
        values = ["short", "x" * 200, 42, "ignored"]

        result = _bounded_list(values, max_items=3)

        self.assertEqual(3, len(result))
        self.assertEqual("short", result[0])
        self.assertEqual("x" * 180 + "...[truncated]", result[1])
        self.assertEqual("42", result[2])


class BoundedPayloadTest(unittest.TestCase):
    def test_keys_are_stringified_and_payload_is_limited(self) -> None:
        result = _bounded_payload({1: "one", "two": 2, "three": 3}, max_items=2)

        self.assertEqual({"1": "one", "two": 2, "truncated": True}, result)

    def test_nested_values_are_bounded_recursively(self) -> None:
        payload = {
            "nested": {"message": "y" * 300},
            "items": list(range(25)),
            "object": Path("example.txt"),
            "flag": True,
        }

        result = _bounded_payload(payload)

        self.assertEqual("y" * 240 + "...[truncated]", result["nested"]["message"])
        self.assertEqual(list(range(20)), result["items"])
        self.assertEqual("example.txt", result["object"])
        self.assertIs(True, result["flag"])


class BoundedValueTest(unittest.TestCase):
    def test_scalar_values_are_preserved(self) -> None:
        self.assertEqual(1, _bounded_value(1))
        self.assertEqual(1.5, _bounded_value(1.5))
        self.assertIs(False, _bounded_value(False))
        self.assertIsNone(_bounded_value(None))

    def test_string_values_are_truncated_at_payload_limit(self) -> None:
        self.assertEqual("z" * 240 + "...[truncated]", _bounded_value("z" * 300))


class OperatorProgressWriterTest(unittest.TestCase):
    def test_init_creates_parent_directory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "operator_events.jsonl"

            writer = OperatorProgressWriter(path, "run-1")

            self.assertEqual(path, writer.path)
            self.assertTrue(path.exists())
            self.assertEqual("", path.read_text(encoding="utf-8"))

    def test_write_appends_bounded_jsonl_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator_events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")

            writer.write(
                "work",
                "active",
                "s" * 300,
                source="agent",
                evidence=["e" * 200, "artifact:trace.jsonl#L1"],
                payload={
                    "message": "p" * 300,
                    "nested": {"path": Path("artifact.txt")},
                },
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            event = json.loads(lines[0])

            self.assertEqual("run-1", event["run_id"])
            self.assertEqual("agent", event["source"])
            self.assertEqual("work", event["phase"])
            self.assertEqual("active", event["status"])
            self.assertEqual("s" * 240 + "...[truncated]", event["summary"])
            self.assertEqual("e" * 180 + "...[truncated]", event["evidence"][0])
            self.assertEqual("artifact:trace.jsonl#L1", event["evidence"][1])
            self.assertEqual("p" * 240 + "...[truncated]", event["payload"]["message"])
            self.assertEqual("artifact.txt", event["payload"]["nested"]["path"])

    def test_write_appends_multiple_events_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator_events.jsonl"
            writer = OperatorProgressWriter(path, "run-1")

            writer.write("scout", "active", "first")
            writer.write("work", "active", "second")

            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(["first", "second"], [event["summary"] for event in events])


if __name__ == "__main__":
    unittest.main()
