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
            memory.start_run_context("example/repo")
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                memory=memory,
            )

            result = registry.aci_memory_get_context("run")
            payload = json.loads(result.output)

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


if __name__ == "__main__":
    unittest.main()
