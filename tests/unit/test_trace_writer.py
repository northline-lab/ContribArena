from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from contribarena.trace import TraceWriter


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TraceWriterTest(unittest.TestCase):
    def test_creates_parent_directory_and_trace_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "trace.jsonl"

            TraceWriter(path, "run-1")

            self.assertTrue(path.parent.is_dir())
            self.assertEqual("", path.read_text(encoding="utf-8"))

    def test_writes_jsonl_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = TraceWriter(path, "run-1")
            writer.write("run_started", "run.started", {"ok": True})

            [event] = _read_events(path)
            self.assertEqual("run-1", event["run_id"])
            self.assertEqual("run_started", event["state"])
            self.assertEqual("run.started", event["event"])
            self.assertTrue(event["payload"]["ok"])

    def test_defaults_missing_payload_to_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = TraceWriter(path, "run-1")

            writer.write("tool_started", "tool.started")

            [event] = _read_events(path)
            self.assertEqual({}, event["payload"])

    def test_appends_events_in_write_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = TraceWriter(path, "run-1")

            writer.write("first", "event.first", {"index": 1})
            writer.write("second", "event.second", {"index": 2})

            events = _read_events(path)
            self.assertEqual(["event.first", "event.second"], [event["event"] for event in events])
            self.assertEqual([1, 2], [event["payload"]["index"] for event in events])

    def test_round_trips_non_ascii_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = TraceWriter(path, "run-1")

            writer.write("message", "message.created", {"text": "café"})

            [event] = _read_events(path)
            self.assertEqual("café", event["payload"]["text"])


if __name__ == "__main__":
    unittest.main()
