from __future__ import annotations

import json
from typing import Any

from agents.models.interface import ModelProvider

from contribarena.config.schema import RepoCandidate, RunConfig
from contribarena.errors import AgentError
from contribarena.models import (
    AgentFinalResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.tools.registry import ToolRegistry


class ContributorAgent:
    def run(
        self,
        config: RunConfig,
        tools: ToolRegistry,
        prompt: str,
        model_provider: ModelProvider | None = None,
    ) -> AgentFinalResult:
        if config.run.model == "local-stub":
            return self._run_local_stub(config, tools, reason="model=local-stub")
        if model_provider is None:
            raise AgentError("model_provider is required for non-local-stub runs")
        return self._run_agents_sdk(config, tools, prompt, model_provider)

    def _run_agents_sdk(
        self,
        config: RunConfig,
        tools: ToolRegistry,
        prompt: str,
        model_provider: ModelProvider,
    ) -> AgentFinalResult:
        try:
            from agents import (
                Agent,
                ModelSettings,
                RunConfig as AgentsRunConfig,
                Runner,
                function_tool,
            )
        except ImportError as exc:
            raise AgentError("openai-agents is required for non-local-stub runs") from exc

        @function_tool
        def repo_search(query: str = "", filters_json: str = "{}") -> str:
            """Return configured repository candidates as JSON."""
            filters = json.loads(filters_json) if filters_json else None
            return _to_json(tools.repo_search(query=query, filters=filters))

        @function_tool
        def repo_check_eligibility(owner: str, repo: str) -> str:
            """Check whether a candidate repository is eligible for M0.0 inspection."""
            return _to_json(tools.repo_check_eligibility(_find_candidate(config, owner, repo)))

        @function_tool
        def repo_get_metadata(owner: str, repo: str) -> str:
            """Return read-only repository metadata."""
            return _to_json(tools.repo_get_metadata(_find_candidate(config, owner, repo)))

        @function_tool
        def repo_get_issues(owner: str, repo: str) -> str:
            """Return read-only candidate issues for a repository."""
            return _to_json(tools.repo_get_issues(_find_candidate(config, owner, repo)))

        @function_tool
        def workspace_run(cmd: str, timeout_seconds: int | None = None) -> str:
            """Run a shell command inside the Docker workspace."""
            return _to_json(tools.workspace_run(cmd, timeout_seconds=timeout_seconds))

        @function_tool
        def workspace_apply_patch(diff: str) -> str:
            """Apply a unified diff inside the Docker workspace."""
            return _to_json(tools.workspace_apply_patch(diff))

        agent = Agent(
            name="contribarena-contributor",
            instructions=(
                "Use the provided tools to inspect exactly one configured candidate repository. "
                "Repository code interaction must go through workspace tools. "
                "Do not inspect the empty workspace before cloning the repository. "
                "Finish with the structured ContribArena M0.0 completion result as soon as "
                "you have a repo profile, opportunities, one selected task, and a workspace summary."
            ),
            tools=[
                repo_search,
                repo_check_eligibility,
                repo_get_metadata,
                repo_get_issues,
                workspace_run,
                workspace_apply_patch,
            ],
            model=config.run.model,
            model_settings=ModelSettings(
                max_tokens=config.run.budget.max_tokens,
                parallel_tool_calls=False,
            ),
            output_type=AgentFinalResult,
        )
        try:
            run_config = AgentsRunConfig(
                model_provider=model_provider,
                workflow_name="ContribArena M0.0",
                # trace.jsonl is the M0 source of truth; SDK spans can be enabled later.
                tracing_disabled=True,
            )
            result = Runner.run_sync(
                agent,
                prompt,
                max_turns=config.run.budget.max_steps,
                run_config=run_config,
            )
            return result.final_output_as(AgentFinalResult)
        except Exception as exc:
            raise AgentError(f"agent run failed: {exc}") from exc

    def _run_local_stub(
        self,
        config: RunConfig,
        tools: ToolRegistry,
        reason: str,
    ) -> AgentFinalResult:
        candidate = tools.repo_search()[0]
        if not isinstance(candidate, RepoCandidate):
            candidate = config.discovery.candidates[0]
        eligibility = tools.repo_check_eligibility(candidate)
        metadata: dict[str, Any] = tools.repo_get_metadata(candidate)  # type: ignore[assignment]
        issues = tools.repo_get_issues(candidate)
        command = tools.workspace_run("pwd")

        opportunity = OpportunitySummary(
            title="Inspect repository and identify a low-risk follow-up",
            rationale="M0.0 local fallback creates a structured result when LLM dependencies are unavailable.",
            risk="low",
            source=str(issues[0].get("url")) if issues else "",
        )
        return AgentFinalResult(
            status="completed" if eligibility.eligible else "blocked",
            repo=RepoSummary(owner=candidate.owner, name=candidate.repo, url=str(candidate.url)),
            repo_profile=(
                f"# Repo Profile: {candidate.full_name}\n\n"
                f"- URL: {candidate.url}\n"
                f"- Branch: {candidate.branch or metadata.get('default_branch', 'main')}\n"
                f"- Notes: {candidate.notes or 'n/a'}\n"
                f"- Agent backend: local fallback ({reason})\n"
            ),
            opportunities=[opportunity],
            selected_task=SelectedTask(
                title=opportunity.title,
                rationale=opportunity.rationale,
                expected_change="No code change required for M0.0 skeleton validation.",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=False,
                notes=f"workspace_run exit_code={command.exit_code}",
            ),
            blockers=[] if eligibility.eligible else eligibility.reasons,
        )


def _find_candidate(config: RunConfig, owner: str, repo: str) -> RepoCandidate:
    for candidate in config.discovery.candidates:
        if candidate.owner == owner and candidate.repo == repo:
            return candidate
    raise AgentError(f"unknown repository candidate: {owner}/{repo}")


def _to_json(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=True)


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return value
    return value
