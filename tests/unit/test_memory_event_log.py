from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.memory.event_log import MemoryEventLog
from contribarena.memory.schema import MemoryEvent


def _make_event(
    event_type: str,
    run_id: str,
    text: str = "",
    **kwargs: object,
) -> MemoryEvent:
    return MemoryEvent(
        event_id="evt-1",
        event_type=event_type,
        run_id=run_id,
        created_at="2026-05-23T00:00:00Z",
        payload={"text": text, **(dict(kwargs) if kwargs else {})},
    )


class MemoryEventLogTest(unittest.TestCase):
    def test_append_and_read_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            log.append(_make_event("goal_created", "run-1", text="Created goal"))
            log.append(_make_event("fact_updated", "run-1", text="Found README"))
            events = log.read_events()
            self.assertEqual(2, len(events))
            self.assertEqual("goal_created", events[0].event_type)
            self.assertEqual("fact_updated", events[1].event_type)

    def test_read_events_returns_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-2")
            events = log.read_events()
            self.assertEqual([], events)

    def test_read_text_returns_raw_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-3")
            log.append(_make_event("note_added", "run-3", text="Hello world"))
            text = log.read_text()
            self.assertIn("note_added", text)
            self.assertIn("Hello world", text)

    def test_read_text_returns_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-4")
            text = log.read_text()
            self.assertEqual("", text)

    def test_read_events_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-5")
            log.path.write_text(
                "not valid json\n"
                '{"event_id": "x", "event_type": "goal_created", "run_id": "run-5", "created_at": "2026-01-01T00:00:00Z", "payload": {}}\n'
                "\n",
                encoding="utf-8",
            )
            events = log.read_events()
            self.assertEqual(1, len(events))
            self.assertEqual("goal_created", events[0].event_type)

    def test_multiple_runs_write_separate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_a = MemoryEventLog(Path(tmp), "run-a")
            log_b = MemoryEventLog(Path(tmp), "run-b")
            log_a.append(_make_event("goal_created", "run-a", text="A"))
            log_b.append(_make_event("fact_updated", "run-b", text="B"))
            self.assertEqual(1, len(log_a.read_events()))
            self.assertEqual(1, len(log_b.read_events()))
            self.assertEqual("goal_created", log_a.read_events()[0].event_type)
            self.assertEqual("fact_updated", log_b.read_events()[0].event_type)
