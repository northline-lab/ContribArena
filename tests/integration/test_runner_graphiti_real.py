from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import MemoryConfig
from contribarena.models import (
    AgentFinalResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.agent_result import WorkspaceSummary
from tests.unit.test_runner_m02 import _issue_config, _run_with_fake_docker


def _require_enabled() -> None:
    if os.environ.get("RUN_GRAPHITI_RUN_INTEGRATION") != "1":
        raise unittest.SkipTest(
            "set RUN_GRAPHITI_RUN_INTEGRATION=1 to run real Graphiti runner tests"
        )
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


class FakeGraphitiRunAgent:
    def run(
        self,
        config: object,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        self.prompt = prompt
        self.memory_context = json.loads(tools.aci_memory_get_context("run").output)  # type: ignore[attr-defined]
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/CONTRIBUTING.md")  # type: ignore[attr-defined]
        self.note_result = json.loads(
            tools.aci_memory_note(  # type: ignore[attr-defined]
                "repo",
                (
                    "qwait/graphiti-run-fixture requires agents to inspect "
                    "CONTRIBUTING before editing and run compileall before submit."
                ),
                '["repo_context", "verification"]',
                "high",
            ).output
        )
        self.search_result = json.loads(
            tools.aci_memory_search(  # type: ignore[attr-defined]
                "inspect CONTRIBUTING compileall before submit",
                "repo_context",
                5,
            ).output
        )
        tools.aci_replace("repo/app.py", "old", "new")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]
        return AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall Graphiti run fixture.",
            opportunities=[
                OpportunitySummary(
                    title="Replace old marker",
                    rationale="Exercise M0.6.5 Graphiti-backed runner path.",
                    risk="low",
                    source="integration-test",
                )
            ],
            selected_task=SelectedTask(
                title="Replace old marker",
                rationale="Exercise M0.6.5 Graphiti-backed runner path.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=True,
                notes="M0.6.5 Graphiti-backed runner path submitted.",
            ),
            problem_statement_summary="Configured issue asks for old marker to become new.",
            reproduction_notes="Inspected repo/app.py and found the old marker.",
            verification_summary="python3 -m compileall . passed.",
        )


class RealGraphitiRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        _require_enabled()

    def test_runner_artifacts_include_real_graphiti_memory_write_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(
                root=tmp_path / "memory",
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
            )
            agent = FakeGraphitiRunAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            self.assertEqual("completed", result.status)
            self.assertFalse(agent.note_result["degraded"], agent.note_result)
            self.assertFalse(agent.search_result["degraded"], agent.search_result)
            self.assertIn(
                "graphiti",
                {item["source"] for item in agent.search_result["results"]},
            )
            report = json.loads((result.run_dir / "memory_write_report.json").read_text())
            self.assertTrue(report["graphiti_enabled"])
            self.assertTrue(report["graphiti_available"])
            self.assertGreaterEqual(report["graphiti_episodes_written"], 1)
            self.assertGreaterEqual(report["memory_searches"], 1)
            self.assertGreaterEqual(report["graphiti_searches"], 1)
            self.assertGreaterEqual(report["history_index_searches"], 1)
            events_text = (result.run_dir / "memory_events.jsonl").read_text()
            self.assertIn("agent_lesson_proposed", events_text)
            self.assertIn("harness_repo_fact_observed", events_text)
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            self.assertIn("aci_memory_search", {step["tool"] for step in trajectory})


if __name__ == "__main__":
    unittest.main()
