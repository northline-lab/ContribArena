from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from contribarena.agent import ContributorAgent
from contribarena.agent.prompts import build_goal_prompt
from contribarena.config.schema import RunConfig
from contribarena.engine.artifacts import ArtifactWriter
from contribarena.engine.context import ContextBuilder
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import AgentFinalResult, RunState
from contribarena.providers import ContribArenaModelProvider
from contribarena.trace import TraceWriter
from contribarena.tools.registry import ToolRegistry


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    status: str
    tool_calls: int


class Runner:
    def __init__(self, agent: ContributorAgent | None = None) -> None:
        self.agent = agent or ContributorAgent()

    def run(
        self, config: RunConfig, output_dir: Path | None = None, verbose: bool = False
    ) -> RunResult:
        candidate = config.discovery.candidates[0]
        run_id = config.run.id or uuid.uuid4().hex[:12]
        output_root = output_dir or config.artifacts.output_root
        artifacts = ArtifactWriter(output_root, run_id, candidate.full_name, config.run.model)
        trace = TraceWriter(artifacts.run_dir / "trace.jsonl", run_id)
        trace.write(RunState.RUN_STARTED, "run.started", {"verbose": verbose})
        trace.write(RunState.CONFIG_LOADED, "config.loaded", {"model": config.run.model})

        artifacts.write_json("config.json", config.model_dump(mode="json"))
        workspace = DockerWorkspaceManager(run_id, candidate.full_name, config.workspace)
        budget = BudgetTracker(config.run.budget)
        capture = ArtifactCapture()

        try:
            workspace.start()
            trace.write(
                RunState.WORKSPACE_READY,
                "workspace.ready",
                {"container": workspace.container_name},
            )
            registry = ToolRegistry(
                config=config,
                workspace=workspace,
                trace=trace,
                budget=budget,
                capture=capture,
            )
            prompt = (
                ContextBuilder().build_system_prompt(config) + "\n\n" + build_goal_prompt(config)
            )
            result = self.agent.run(
                config,
                registry,
                prompt,
                model_provider=ContribArenaModelProvider(config.models),
            )
            self._write_agent_artifacts(artifacts, result)
            trace.write(
                RunState.REPO_PROFILED, "repo.profile.write", {"artifact": "repo_profile.md"}
            )
            trace.write(
                RunState.OPPORTUNITIES_RANKED,
                "opportunities.rank.write",
                {"artifact": "opportunity_rank.md", "count": len(result.opportunities)},
            )
            trace.write(
                RunState.TASK_SELECTED,
                "task.selected.write",
                {"artifact": "selected_task.md", "title": result.selected_task.title},
            )
            workspace_command = {
                "commands": [command.model_dump(mode="json") for command in capture.commands],
                "patches": [patch.model_dump(mode="json") for patch in capture.patches],
            }
            artifacts.write_json("workspace_command.json", workspace_command)
            trace.write(
                RunState.ARTIFACTS_WRITTEN,
                "artifacts.written",
                {"count": len(artifacts.entries) + 1},
            )
            trace.write(RunState.RUN_COMPLETED, "run.completed", {"status": result.status})
            artifacts.finalize_manifest()
            return RunResult(
                run_id=run_id,
                run_dir=artifacts.run_dir,
                status=result.status,
                tool_calls=budget.steps,
            )
        except Exception as exc:
            trace.write(RunState.AGENT_ERROR, "run.failed", {"error": str(exc)})
            artifacts.finalize_manifest()
            raise
        finally:
            workspace.stop()

    def _write_agent_artifacts(self, artifacts: ArtifactWriter, result: AgentFinalResult) -> None:
        artifacts.write_markdown("repo_profile.md", result.repo_profile)
        opportunities = ["# Opportunity Rank", ""]
        for index, opportunity in enumerate(result.opportunities, start=1):
            opportunities.extend(
                [
                    f"## {index}. {opportunity.title}",
                    "",
                    f"- Risk: {opportunity.risk}",
                    f"- Source: {opportunity.source or 'n/a'}",
                    f"- Rationale: {opportunity.rationale}",
                    "",
                ]
            )
        artifacts.write_markdown("opportunity_rank.md", "\n".join(opportunities))
        artifacts.write_markdown(
            "selected_task.md",
            "\n".join(
                [
                    f"# Selected Task: {result.selected_task.title}",
                    "",
                    f"- Risk: {result.selected_task.risk}",
                    f"- Expected change: {result.selected_task.expected_change}",
                    "",
                    result.selected_task.rationale,
                ]
            ),
        )
