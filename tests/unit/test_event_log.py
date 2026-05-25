from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.memory.event_log import MemoryEventLog
from contribarena.memory.schema import MemoryEvent


def _make_event(
    event_id: str = "evt-1",
    event_type: str = "unit_test",
    run_id: str = "run-1",
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        event_type=event_type,
        run_id=run_id,
        repo_full_name="owner/repo",
        payload={"key": "value"},
        confidence="medium",
        created_at="2026-01-01T00:00:00+00:00",
    )


class MemoryEventLogTest(unittest.TestCase):
    def test_init_creates_events_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = MemoryEventLog(root, "run-1")

            self.assertTrue((root / "events").is_dir())
            self.assertEqual(root / "events" / "run-1.jsonl", log.path)
            # File itself is not created until the first append.
            self.assertFalse(log.path.exists())

    def test_read_events_returns_empty_list_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")

            self.assertEqual([], log.read_events())

    def test_read_text_returns_empty_string_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")

            self.assertEqual("", log.read_text())

    def test_append_writes_jsonl_line_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            event = _make_event()

            log.append(event)

            text = log.path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(1, len(text.strip().splitlines()))

            events = log.read_events()
            self.assertEqual(1, len(events))
            self.assertEqual("evt-1", events[0].event_id)
            self.assertEqual("unit_test", events[0].event_type)
            self.assertEqual("owner/repo", events[0].repo_full_name)
            self.assertEqual({"key": "value"}, events[0].payload)

    def test_append_preserves_order_across_multiple_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")

            log.append(_make_event(event_id="evt-1"))
            log.append(_make_event(event_id="evt-2"))
            log.append(_make_event(event_id="evt-3"))

            events = log.read_events()
            self.assertEqual(["evt-1", "evt-2", "evt-3"], [e.event_id for e in events])

    def test_read_events_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            log.append(_make_event(event_id="evt-1"))
            # Inject blank lines around the real event line.
            with log.path.open("a", encoding="utf-8") as handle:
                handle.write("\n")
                handle.write("   \n")
            log.append(_make_event(event_id="evt-2"))

            events = log.read_events()
            self.assertEqual(["evt-1", "evt-2"], [e.event_id for e in events])

    def test_read_events_skips_malformed_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            log.append(_make_event(event_id="evt-1"))
            with log.path.open("a", encoding="utf-8") as handle:
                handle.write("this is not json\n")
            log.append(_make_event(event_id="evt-2"))

            events = log.read_events()
            self.assertEqual(["evt-1", "evt-2"], [e.event_id for e in events])

    def test_read_events_skips_json_lines_that_fail_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            log.append(_make_event(event_id="evt-1"))
            # Valid JSON but missing required MemoryEvent fields.
            with log.path.open("a", encoding="utf-8") as handle:
                handle.write('{"event_id": "evt-x"}\n')

            events = log.read_events()
            self.assertEqual(["evt-1"], [e.event_id for e in events])

    def test_read_text_returns_raw_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = MemoryEventLog(Path(tmp), "run-1")
            log.append(_make_event(event_id="evt-1"))
            log.append(_make_event(event_id="evt-2"))

            text = log.read_text()
            self.assertEqual(log.path.read_text(encoding="utf-8"), text)
            self.assertEqual(2, len([line for line in text.splitlines() if line.strip()]))

    def test_separate_run_ids_write_to_separate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_a = MemoryEventLog(root, "run-a")
            log_b = MemoryEventLog(root, "run-b")

            log_a.append(_make_event(event_id="a-1", run_id="run-a"))
            log_b.append(_make_event(event_id="b-1", run_id="run-b"))

            self.assertEqual(["a-1"], [e.event_id for e in log_a.read_events()])
            self.assertEqual(["b-1"], [e.event_id for e in log_b.read_events()])
            self.assertNotEqual(log_a.path, log_b.path)

    def test_existing_events_directory_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events").mkdir()

            log = MemoryEventLog(root, "run-1")
            log.append(_make_event(event_id="evt-1"))

            self.assertEqual(["evt-1"], [e.event_id for e in log.read_events()])


if __name__ == "__main__":
    unittest.main()
