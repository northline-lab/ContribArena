from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from contribarena.trace import TraceEvent
from contribarena.trace.events import TraceEvent as TraceEventDirect


class TraceEventDefaultsTest(unittest.TestCase):
    def test_required_fields_with_minimal_construction(self) -> None:
        event = TraceEvent(run_id="run-1", state="run_started", event="run.started")

        self.assertEqual("run-1", event.run_id)
        self.assertEqual("run_started", event.state)
        self.assertEqual("run.started", event.event)

    def test_payload_defaults_to_empty_dict(self) -> None:
        event = TraceEvent(run_id="r", state="s", event="e")

        self.assertEqual({}, event.payload)

    def test_payload_default_factory_returns_independent_dicts(self) -> None:
        first = TraceEvent(run_id="r1", state="s", event="e")
        second = TraceEvent(run_id="r2", state="s", event="e")

        first.payload["k"] = "v"

        self.assertEqual({}, second.payload)

    def test_ts_default_is_iso_parseable(self) -> None:
        event = TraceEvent(run_id="r", state="s", event="e")

        parsed = datetime.fromisoformat(event.ts)
        self.assertIsNotNone(parsed.tzinfo)

    def test_ts_default_is_monotonic_non_decreasing(self) -> None:
        first = TraceEvent(run_id="r", state="s", event="e")
        second = TraceEvent(run_id="r", state="s", event="e")

        # Both default timestamps should be valid ISO strings, and the
        # second construction must not produce an earlier ts than the first.
        first_dt = datetime.fromisoformat(first.ts)
        second_dt = datetime.fromisoformat(second.ts)
        self.assertLessEqual(first_dt, second_dt)


class TraceEventExplicitValuesTest(unittest.TestCase):
    def test_explicit_ts_is_preserved(self) -> None:
        event = TraceEvent(
            ts="2026-01-02T03:04:05+00:00",
            run_id="r",
            state="s",
            event="e",
        )

        self.assertEqual("2026-01-02T03:04:05+00:00", event.ts)

    def test_explicit_payload_is_preserved(self) -> None:
        payload = {"ok": True, "count": 3, "nested": {"k": "v"}}
        event = TraceEvent(run_id="r", state="s", event="e", payload=payload)

        self.assertEqual(payload, event.payload)

    def test_payload_supports_mixed_value_types(self) -> None:
        payload = {
            "str": "x",
            "int": 1,
            "float": 1.5,
            "bool": False,
            "none": None,
            "list": [1, 2, 3],
        }
        event = TraceEvent(run_id="r", state="s", event="e", payload=payload)

        self.assertEqual(payload, event.payload)


class TraceEventValidationTest(unittest.TestCase):
    def test_missing_run_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(state="s", event="e")  # type: ignore[call-arg]

    def test_missing_state_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(run_id="r", event="e")  # type: ignore[call-arg]

    def test_missing_event_raises(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(run_id="r", state="s")  # type: ignore[call-arg]

    def test_non_dict_payload_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TraceEvent(run_id="r", state="s", event="e", payload=["not", "a", "dict"])  # type: ignore[arg-type]


class TraceEventSerializationTest(unittest.TestCase):
    def test_model_dump_round_trips_payload(self) -> None:
        event = TraceEvent(
            run_id="r",
            state="s",
            event="e",
            payload={"k": "v"},
        )

        dumped = event.model_dump(mode="json")

        self.assertEqual("r", dumped["run_id"])
        self.assertEqual("s", dumped["state"])
        self.assertEqual("e", dumped["event"])
        self.assertEqual({"k": "v"}, dumped["payload"])
        self.assertIn("ts", dumped)

    def test_model_validate_round_trip(self) -> None:
        original = TraceEvent(run_id="r", state="s", event="e", payload={"k": 1})

        rebuilt = TraceEvent.model_validate(original.model_dump(mode="json"))

        self.assertEqual(original.run_id, rebuilt.run_id)
        self.assertEqual(original.state, rebuilt.state)
        self.assertEqual(original.event, rebuilt.event)
        self.assertEqual(original.payload, rebuilt.payload)
        self.assertEqual(original.ts, rebuilt.ts)


class TraceEventImportTest(unittest.TestCase):
    def test_reexport_from_package_is_same_class(self) -> None:
        self.assertIs(TraceEvent, TraceEventDirect)


if __name__ == "__main__":
    unittest.main()
