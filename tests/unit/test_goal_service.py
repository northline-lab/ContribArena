from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.goals import GoalService, goal_state_path


class GoalServiceTest(unittest.TestCase):
    def test_goal_context_starts_with_long_term_goal_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")

            service = GoalService(config, run_id="run-1")

            self.assertTrue(service.context.enabled)
            self.assertIn("meaningful engineering contributions", service.context.long_term_objective)
            self.assertIsNone(service.context.short_term)
            self.assertEqual(config.artifacts.output_root / "goal_state.json", goal_state_path(config))

    def test_short_term_goal_create_update_complete_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")

            created = service.update(objective="Find one small code PR.", status="active")
            updated = service.update(objective="Submit one verified small code PR.", status="active")
            missing_evidence = service.update(status="complete")
            completed = service.update(
                status="complete",
                evidence="Patch submitted and focused verification passed.",
                evidence_refs=["tool_call:aci_submit_patch:1"],
            )
            reloaded = GoalService(config, run_id="run-2")

            self.assertTrue(created.success)
            self.assertEqual("goal_created", created.event.event_type if created.event else "")
            self.assertEqual("contribution", created.goals.short_term.scope)
            self.assertEqual("work", created.goals.current_phase)
            self.assertTrue(updated.success)
            self.assertEqual("goal_updated", updated.event.event_type if updated.event else "")
            self.assertFalse(missing_evidence.success)
            self.assertEqual("missing_goal_evidence", missing_evidence.error_kind)
            self.assertTrue(completed.success)
            self.assertEqual("complete", completed.goals.short_term.status)
            self.assertEqual("complete", reloaded.context.short_term.status)
            self.assertIn("goal_completed", service.events_text())
            state = json.loads(goal_state_path(config).read_text(encoding="utf-8"))
            self.assertEqual("complete", state["short_term"]["status"])

    def test_goal_scope_derives_phase_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")

            repo = service.update(
                objective="Audit configured repo.",
                status="active",
                scope="repo",
            )
            opportunity = service.update(
                objective="Find a non-duplicate issue.",
                status="active",
                scope="opportunity",
                evidence="Repo audit identified a viable project.",
            )
            contribution = service.update(
                objective="Implement the selected opportunity.",
                status="active",
                scope="contribution",
                evidence="Opportunity comparison selected a target.",
            )

            self.assertTrue(repo.success)
            self.assertEqual("scout", repo.goals.current_phase)
            self.assertEqual("project", repo.goals.current_sub_phase)
            self.assertTrue(opportunity.success)
            self.assertEqual("scout", opportunity.goals.current_phase)
            self.assertEqual("opportunity", opportunity.goals.current_sub_phase)
            self.assertTrue(contribution.success)
            self.assertEqual("work", contribution.goals.current_phase)
            self.assertIsNone(contribution.goals.current_sub_phase)
            projection = service.phase_transition_text()
            self.assertIn('"source": "goal_events.jsonl"', projection)
            self.assertIn('"phase": "work"', projection)

            service.record_draft_submitted(evidence="Draft patch captured.")

            self.assertEqual("review", service.context.current_phase)
            self.assertIsNone(service.context.current_sub_phase)
            projection = service.phase_transition_text()
            self.assertIn('"event_type": "draft_submitted"', projection)
            self.assertIn('"phase": "review"', projection)

    def test_goal_update_validates_canonical_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")
            service.update(objective="Implement selected opportunity.", status="active", scope="contribution")

            invalid = service.update(
                status="abandoned",
                scope="opportunity",
                evidence="Duplicate PR found.",
                evidence_refs=["not-a-ref"],
            )
            missing = service.update(
                status="abandoned",
                scope="opportunity",
                evidence="Duplicate PR found.",
            )
            valid = service.update(
                status="abandoned",
                scope="opportunity",
                evidence="Duplicate PR found.",
                evidence_refs=["artifact:phase_scout_duplicate_check.jsonl#L4"],
            )

            self.assertFalse(invalid.success)
            self.assertEqual("invalid_evidence_ref", invalid.error_kind)
            self.assertFalse(missing.success)
            self.assertEqual("missing_evidence_refs", missing.error_kind)
            self.assertTrue(valid.success)
            self.assertEqual("scout", valid.goals.current_phase)
            self.assertEqual("opportunity", valid.goals.current_sub_phase)

    def test_evidence_invalid_is_recoverable_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            calls: list[str] = []

            def validate(ref: str) -> bool:
                calls.append(ref)
                return ref == "tool_call:aci_verify"

            service = GoalService(config, run_id="run-1", evidence_ref_validator=validate)
            service.update(
                objective="Implement selected opportunity.",
                status="active",
                scope="contribution",
            )

            bad = service.update(
                status="complete",
                evidence="Verification passed.",
                evidence_refs=["tool_call:missing"],
            )
            retry = service.update(
                status="complete",
                evidence="Verification passed.",
                evidence_refs=["tool_call:aci_verify"],
            )

            self.assertFalse(bad.success)
            self.assertEqual("evidence_invalid", bad.error_kind)
            self.assertTrue(retry.success)
            self.assertEqual(["tool_call:missing", "tool_call:aci_verify"], calls)

    def test_superseded_goal_keeps_active_goal_with_next_objective(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")
            service.update(
                objective="Implement selected opportunity.",
                status="active",
                scope="contribution",
            )

            superseded = service.update(
                status="superseded",
                scope="contribution",
                evidence="Review showed a smaller strategy is safer.",
                evidence_refs=["tool_call:aci_verify:2"],
                next_objective="Implement the smaller strategy.",
            )

            self.assertTrue(superseded.success)
            self.assertEqual("active", superseded.goals.short_term.status)
            self.assertEqual("Implement the smaller strategy.", superseded.goals.short_term.objective)
            self.assertIn("goal_superseded", service.events_text())

    def test_terminal_goal_can_be_replaced_by_new_active_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")

            first = service.update(objective="Finish first PR.", status="active")
            service.update(
                status="abandoned",
                evidence="Repository guidance rejected the task.",
                evidence_refs=["tool_call:aci_view:1"],
            )
            second = service.update(objective="Find a better PR.", status="active")

            self.assertNotEqual(
                first.goals.short_term.goal_id,
                second.goals.short_term.goal_id,
            )
            self.assertEqual("active", second.goals.short_term.status)


def _config(output_root: Path) -> RunConfig:
    return RunConfig(
        run=RunSection(id="run-1", mode="shadow"),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=output_root),
    )


if __name__ == "__main__":
    unittest.main()
