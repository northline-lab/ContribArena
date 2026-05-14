from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    DiscoveryConfig,
    MemoryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.goals import GoalService
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.service import MemoryService
from contribarena.tools.registry import ToolRegistry
from contribarena.trace import TraceWriter


class ToolRegistryMemoryTest(unittest.TestCase):
    def test_aci_memory_get_context_run_returns_guidance_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior_run = root / "prior"
            prior_run.mkdir()
            (prior_run / "repo_guidance.json").write_text(
                '{"summary": "Prior CONTRIBUTING guidance verification exists"}\n',
                encoding="utf-8",
            )
            HistoryIndex(root / "memory").index_run_dir(
                prior_run,
                run_id="prior-run",
                repo_full_name="example/repo",
            )
            config = RunConfig(
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
                memory=MemoryConfig(root=root / "memory"),
            )
            memory = MemoryService(
                config.memory,
                run_id="run-1",
                repo_full_name="example/repo",
            )
            goals = GoalService(config, run_id="run-1")
            memory.set_goal_context(goals.context)
            memory.start_run_context("example/repo")
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                memory=memory,
                goals=goals,
            )

            runtime = registry.aci_runtime_get_context("run")
            runtime_payload = json.loads(runtime.output)
            result = registry.aci_memory_get_context("run")
            payload = json.loads(result.output)

            self.assertTrue(runtime.success)
            self.assertEqual("shadow", runtime_payload["run_mode"])
            self.assertEqual("run-1", runtime_payload["run_id"])
            self.assertTrue(runtime_payload["guidance"]["available"])
            self.assertIn(
                "meaningful engineering contributions",
                runtime_payload["goals"]["long_term_objective"],
            )
            self.assertEqual("repo_context", runtime_payload["memory_hints"][0]["category"])
            self.assertTrue(result.success)
            self.assertTrue(payload["guidance"]["available"])
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                payload["guidance"]["entry_path"],
            )
            self.assertEqual(
                ".contribarena/guidance/guidance_manifest.json",
                payload["guidance"]["manifest_path"],
            )
            self.assertEqual("workspace_root", payload["guidance"]["path_relative_to"])
            self.assertEqual("repo_context", payload["memory_hints"][0]["category"])
            self.assertEqual("repo_context", payload["memory_hints"][0]["suggested_intent"])
            self.assertIn("suggested_query", payload["memory_hints"][0])
            self.assertFalse(payload["memory_capabilities"]["repo_scope_persistent"])
            self.assertFalse(payload["memory_capabilities"]["global_scope_persistent"])
            self.assertIn("meaningful engineering contributions", payload["goals"]["long_term_objective"])
            self.assertEqual("aci_goal_update", payload["goals"]["update_tool"])

            update = registry.aci_goal_update("Submit a verified small code PR.", "active", "")
            complete_without_evidence = registry.aci_goal_update(status="complete")
            complete = registry.aci_goal_update(
                status="complete",
                evidence="Submitted patch and focused verification passed.",
            )

            self.assertTrue(update.success)
            self.assertFalse(complete_without_evidence.success)
            self.assertEqual("missing_goal_evidence", complete_without_evidence.recovery_kind)
            self.assertTrue(complete.success)
            completed_payload = json.loads(complete.output)
            self.assertEqual("complete", completed_payload["goals"]["short_term"]["status"])
            refreshed = json.loads(registry.aci_memory_get_context("run").output)
            self.assertEqual("complete", refreshed["goals"]["short_term"]["status"])
            refreshed_runtime = json.loads(registry.aci_runtime_get_context("run").output)
            self.assertEqual("complete", refreshed_runtime["goals"]["short_term"]["status"])

    def test_aci_goal_update_third_abandon_marks_run_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
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
                memory=MemoryConfig(root=root / "memory"),
            )
            memory = MemoryService(config.memory, run_id="run-1", repo_full_name="example/repo")
            goals = GoalService(config, run_id="run-1")
            memory.set_goal_context(goals.context)
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                memory=memory,
                goals=goals,
            )

            first = _abandon_goal(registry, "Try task one.")
            second = _abandon_goal(registry, "Try task two.")
            third = _abandon_goal(registry, "Try task three.")
            fourth = registry.aci_goal_update("Try task four.", "active", "")

            self.assertIsNone(first.terminal_status)
            self.assertIsNone(second.terminal_status)
            self.assertEqual("goal_abandon_limit", third.terminal_status)
            self.assertEqual("goal_abandon_limit", fourth.terminal_status)
            self.assertEqual("goal_abandon_limit", fourth.recovery_kind)


def _abandon_goal(registry: ToolRegistry, objective: str):
    registry.aci_goal_update(objective, "active", "")
    return registry.aci_goal_update(
        "",
        "abandoned",
        f"{objective} is not suitable for this run.",
    )


if __name__ == "__main__":
    unittest.main()
