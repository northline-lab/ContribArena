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
            )
            reloaded = GoalService(config, run_id="run-2")

            self.assertTrue(created.success)
            self.assertEqual("goal_created", created.event.event_type if created.event else "")
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

    def test_terminal_goal_can_be_replaced_by_new_active_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "runs")
            service = GoalService(config, run_id="run-1")

            first = service.update(objective="Finish first PR.", status="active")
            service.update(status="abandoned", evidence="Repository guidance rejected the task.")
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
