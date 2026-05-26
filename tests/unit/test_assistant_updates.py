from __future__ import annotations

from datetime import datetime
import unittest

from contribarena.models.assistant_updates import AssistantUpdate


class AssistantUpdateTest(unittest.TestCase):
    def test_defaults_are_stable_for_minimal_update(self) -> None:
        update = AssistantUpdate(run_id="run-1", text="Inspecting the model.")

        datetime.fromisoformat(update.ts)
        self.assertEqual("run-1", update.run_id)
        self.assertEqual("Inspecting the model.", update.text)
        self.assertEqual("", update.season_id)
        self.assertEqual("", update.participant_id)
        self.assertEqual(0, update.invocation_seq)
        self.assertEqual("", update.turn_id)
        self.assertEqual("", update.phase)
        self.assertEqual("", update.sub_phase)
        self.assertEqual("intent", update.kind)
        self.assertEqual("before_tool", update.position)
        self.assertEqual("", update.tool_name)
        self.assertEqual([], update.evidence_refs)
        self.assertFalse(update.truncated)
        self.assertFalse(update.redacted)
        self.assertEqual(0, update.hidden_dropped_count)
        self.assertEqual("operator", update.visibility)

    def test_evidence_refs_can_be_set_per_update(self) -> None:
        first = AssistantUpdate(
            run_id="run-1",
            text="Viewed model source.",
            evidence_refs=["workspace:repo/src/contribarena/models/assistant_updates.py"],
        )
        second = AssistantUpdate(run_id="run-1", text="No evidence yet.")

        self.assertEqual(
            ["workspace:repo/src/contribarena/models/assistant_updates.py"],
            first.evidence_refs,
        )
        self.assertEqual([], second.evidence_refs)


if __name__ == "__main__":
    unittest.main()
