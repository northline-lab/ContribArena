from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import MemoryConfig
from contribarena.memory.service import MemoryService


def _require_enabled() -> None:
    if os.environ.get("RUN_GRAPHITI_INTEGRATION") != "1":
        raise unittest.SkipTest("set RUN_GRAPHITI_INTEGRATION=1 to run real Graphiti tests")
    missing = [
        name
        for name in (
            "CONTRIBARENA_GRAPHITI_LLM_API_KEY",
            "CONTRIBARENA_GRAPHITI_LLM_BASE_URL",
            "CONTRIBARENA_GRAPHITI_EMBEDDING_API_KEY",
            "CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL",
        )
        if not os.environ.get(name)
    ]
    if missing:
        raise unittest.SkipTest(f"missing required Graphiti env vars: {', '.join(missing)}")


class RealGraphitiBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        _require_enabled()

    def test_repo_note_round_trips_through_graphiti_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            service = MemoryService(
                MemoryConfig(
                    root=root / "memory",
                    backend="graphiti",
                    graphiti_enabled=True,
                    falkordb_host=os.environ.get("FALKORDB_HOST", "localhost"),
                    falkordb_port=int(os.environ.get("FALKORDB_PORT", "6379")),
                    graphiti_llm_model=os.environ.get("CONTRIBARENA_GRAPHITI_LLM_MODEL", "gpt-4.1"),
                    graphiti_llm_small_model=os.environ.get(
                        "CONTRIBARENA_GRAPHITI_LLM_SMALL_MODEL", ""
                    ),
                    graphiti_embedding_model=os.environ.get(
                        "CONTRIBARENA_GRAPHITI_EMBEDDING_MODEL",
                        "qwen3-vl-embed",
                    ),
                    graphiti_embedding_dim=int(
                        os.environ.get("CONTRIBARENA_GRAPHITI_EMBEDDING_DIM", "4096")
                    ),
                    graphiti_timeout_seconds=int(
                        os.environ.get("CONTRIBARENA_GRAPHITI_TIMEOUT_SECONDS", "240")
                    ),
                ),
                run_id="integration-graphiti",
                repo_full_name="qwait/graphiti-integration-fixture",
            )
            text = (
                "qwait/graphiti-integration-fixture requires agents to inspect "
                "CONTRIBUTING before editing and run compileall before submit."
            )

            write = service.note_agent_memory(
                "repo",
                text,
                ["repo_context", "verification"],
                "high",
                "integration-test",
            )
            search = service.search_for_agent(
                "inspect CONTRIBUTING compileall before submit",
                intent="repo_context",
                max_results=5,
            )
            report = service.finalize_run({"terminal": "completed"}, run_dir)

            self.assertTrue(write.success, write.error_message)
            self.assertFalse(write.degraded, write.error_message)
            self.assertTrue(search.success, search.error_kind)
            self.assertFalse(search.degraded, search.error_kind)
            graphiti_text = "\n".join(
                item.text for item in search.results if item.source == "graphiti"
            )
            self.assertIn("CONTRIBUTING", graphiti_text)
            self.assertIn("compileall", graphiti_text)
            self.assertTrue(report.graphiti_enabled)
            self.assertTrue(report.graphiti_available)
            self.assertGreaterEqual(report.graphiti_episodes_written, 1)


if __name__ == "__main__":
    unittest.main()
