from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from pydantic import ValidationError

from contribarena.trace import TraceEvent


class TraceEventRequiredFieldsTest(unittest.TestCase):
    """TraceEvent requires run_id, state, and event."""

    def test_minimal_construction(self) -> None:
        event = TraceEvent(run_id="run-1", state="run_started", event="run.started")
        self.assertEqual("run-1", event.run_id)
        self.assertEqual("run_started", event.state)
        self.assertEqual("run.started", event.event)

    def test_missing_run_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(state="run_started", event="run.started")  # type: ignore[call-arg]

    def test_missing_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(run_id="run-1", event="run.started")  # type: ignore[call-arg]

    def test_missing_event_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(run_id="run-1", state="run_started")  # type: ignore[call-arg]


class TraceEventDefaultsTest(unittest.TestCase):
    """TraceEvent provides defaults for ts and payload."""

    def test_payload_defaults_to_empty_dict(self) -> None:
        event = TraceEvent(run_id="run-1", state="run_started", event="run.started")
        self.assertEqual({}, event.payload)

    def test_ts_defaults_to_iso_string(self) -> None:
        event = TraceEvent(run_id="run-1", state="run_started", event="run.started")
        self.assertIsInstance(event.ts, str)
        parsed = datetime.fromisoformat(event.ts)
        self.assertIsNotNone(parsed.tzinfo)

    def test_ts_default_is_utc(self) -> None:
        event = TraceEvent(run_id="run-1", state="run_started", event="run.started")
        parsed = datetime.fromisoformat(event.ts)
        self.assertEqual(timedelta(0), parsed.utcoffset())

    def test_payload_factory_independence(self) -> None:
        first = TraceEvent(run_id="run-1", state="run_started", event="run.started")
        second = TraceEvent(run_id="run-2", state="run_started", event="run.started")
        first.payload["x"] = 1
        self.assertEqual({}, second.payload)


class TraceEventExplicitValuesTest(unittest.TestCase):
    """TraceEvent preserves explicit values for ts and payload."""

    def test_explicit_ts(self) -> None:
        event = TraceEvent(
            run_id="run-1",
            state="run_started",
            event="run.started",
            ts="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual("2026-01-01T00:00:00+00:00", event.ts)

    def test_explicit_payload(self) -> None:
        payload = {"ok": True, "count": 3, "nested": {"a": 1}}
        event = TraceEvent(
            run_id="run-1",
            state="run_started",
            event="run.started",
            payload=payload,
        )
        self.assertEqual(payload, event.payload)

    def test_model_dump_round_trips(self) -> None:
        event = TraceEvent(
            run_id="run-1",
            state="run_started",
            event="run.started",
            payload={"ok": True},
        )
        dumped = event.model_dump(mode="json")
        self.assertEqual("run-1", dumped["run_id"])
        self.assertEqual("run_started", dumped["state"])
        self.assertEqual("run.started", dumped["event"])
        self.assertEqual({"ok": True}, dumped["payload"])
        self.assertIn("ts", dumped)


if __name__ == "__main__":
    unittest.main()
