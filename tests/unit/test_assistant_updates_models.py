from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import get_args

from pydantic import ValidationError

from contribarena.models.assistant_updates import (
    AssistantUpdate,
    AssistantUpdateKind,
    AssistantUpdatePosition,
)


class AssistantUpdateKindLiteralTest(unittest.TestCase):
    """AssistantUpdateKind defines the accepted set of update kinds."""

    EXPECTED_KINDS = (
        "intent",
        "observation",
        "decision",
        "blocker",
        "verification",
        "review_response",
    )

    def test_expected_kinds_match_definition(self) -> None:
        self.assertEqual(self.EXPECTED_KINDS, get_args(AssistantUpdateKind))

    def test_all_valid_kinds_accepted(self) -> None:
        for kind in self.EXPECTED_KINDS:
            with self.subTest(kind=kind):
                update = AssistantUpdate(run_id="run-1", text="t", kind=kind)
                self.assertEqual(kind, update.kind)

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AssistantUpdate(run_id="run-1", text="t", kind="not-a-kind")


class AssistantUpdatePositionLiteralTest(unittest.TestCase):
    """AssistantUpdatePosition defines where the update is emitted."""

    EXPECTED_POSITIONS = ("before_tool", "after_tool", "commentary_only")

    def test_expected_positions_match_definition(self) -> None:
        self.assertEqual(self.EXPECTED_POSITIONS, get_args(AssistantUpdatePosition))

    def test_all_valid_positions_accepted(self) -> None:
        for position in self.EXPECTED_POSITIONS:
            with self.subTest(position=position):
                update = AssistantUpdate(run_id="run-1", text="t", position=position)
                self.assertEqual(position, update.position)

    def test_invalid_position_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AssistantUpdate(run_id="run-1", text="t", position="sideways")


class AssistantUpdateRequiredFieldsTest(unittest.TestCase):
    """AssistantUpdate requires run_id and text; other fields have defaults."""

    def test_minimal_construction_succeeds(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="hello")
        self.assertEqual("run-1", update.run_id)
        self.assertEqual("hello", update.text)

    def test_missing_run_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AssistantUpdate(text="hello")  # type: ignore[call-arg]

    def test_missing_text_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AssistantUpdate(run_id="run-1")  # type: ignore[call-arg]


class AssistantUpdateDefaultsTest(unittest.TestCase):
    """AssistantUpdate provides safe defaults for optional fields."""

    def test_string_defaults_are_empty(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        self.assertEqual("", update.season_id)
        self.assertEqual("", update.participant_id)
        self.assertEqual("", update.turn_id)
        self.assertEqual("", update.phase)
        self.assertEqual("", update.sub_phase)
        self.assertEqual("", update.tool_name)

    def test_numeric_and_bool_defaults(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        self.assertEqual(0, update.invocation_seq)
        self.assertEqual(0, update.hidden_dropped_count)
        self.assertFalse(update.truncated)
        self.assertFalse(update.redacted)

    def test_kind_and_position_defaults(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        self.assertEqual("intent", update.kind)
        self.assertEqual("before_tool", update.position)

    def test_visibility_default(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        self.assertEqual("operator", update.visibility)

    def test_evidence_refs_default_is_empty_list(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        self.assertEqual([], update.evidence_refs)

    def test_evidence_refs_factory_independence(self) -> None:
        a = AssistantUpdate(run_id="run-1", text="t")
        b = AssistantUpdate(run_id="run-2", text="t")
        a.evidence_refs.append("workspace:repo/file.py")
        self.assertEqual([], b.evidence_refs)

    def test_ts_default_is_iso_utc(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="t")
        parsed = datetime.fromisoformat(update.ts)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(timedelta(0), parsed.utcoffset())


class AssistantUpdateExplicitValuesTest(unittest.TestCase):
    """Explicit values for all fields are preserved."""

    def test_full_construction_round_trips_via_model_dump(self) -> None:
        update = AssistantUpdate(
            ts="2026-05-29T00:00:00+00:00",
            run_id="run-1",
            season_id="season_0",
            participant_id="season_0:agent",
            invocation_seq=3,
            turn_id="turn-7",
            phase="work",
            sub_phase="edit",
            kind="observation",
            text="saw something",
            position="after_tool",
            tool_name="aci_view",
            evidence_refs=["workspace:repo/a.py", "tool_call:xyz"],
            truncated=True,
            redacted=True,
            hidden_dropped_count=2,
            visibility="public",
        )
        dumped = update.model_dump()
        self.assertEqual("2026-05-29T00:00:00+00:00", dumped["ts"])
        self.assertEqual("run-1", dumped["run_id"])
        self.assertEqual("season_0", dumped["season_id"])
        self.assertEqual("season_0:agent", dumped["participant_id"])
        self.assertEqual(3, dumped["invocation_seq"])
        self.assertEqual("turn-7", dumped["turn_id"])
        self.assertEqual("work", dumped["phase"])
        self.assertEqual("edit", dumped["sub_phase"])
        self.assertEqual("observation", dumped["kind"])
        self.assertEqual("saw something", dumped["text"])
        self.assertEqual("after_tool", dumped["position"])
        self.assertEqual("aci_view", dumped["tool_name"])
        self.assertEqual(["workspace:repo/a.py", "tool_call:xyz"], dumped["evidence_refs"])
        self.assertTrue(dumped["truncated"])
        self.assertTrue(dumped["redacted"])
        self.assertEqual(2, dumped["hidden_dropped_count"])
        self.assertEqual("public", dumped["visibility"])


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
