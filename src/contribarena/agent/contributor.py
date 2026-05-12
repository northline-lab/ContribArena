from __future__ import annotations

import json

from agents.models.interface import ModelProvider

from contribarena.agent.model_view import to_model_json
from contribarena.agent.tool_contract import ContributorTools
from contribarena.config.schema import RepoCandidate, RunConfig
from contribarena.errors import AgentError
from contribarena.models import (
    AgentFinalResult,
    OpportunitySummary,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.providers.action_guard import (
    RECOVERY_TOOL_NAME,
    ActionGuardingModelProvider,
)


class ContributorAgent:
    def run(
        self,
        config: RunConfig,
        tools: ContributorTools,
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
        tools: ContributorTools,
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
            """Search GitHub repositories or return configured candidates as JSON."""
            if config.discovery.candidates and query.strip().lower() in {"", "n/a", "none", "null"}:
                return _to_json(tools.repo_search())
            filters = json.loads(filters_json) if filters_json else None
            return _to_json(tools.repo_search(query=query, filters=filters))

        @function_tool
        def repo_check_eligibility(owner: str, repo: str) -> str:
            """Check whether a repository is eligible for M0.1 shadow-mode inspection."""
            return _to_json(tools.repo_check_eligibility(_candidate_ref(config, owner, repo)))

        @function_tool
        def repo_get_metadata(owner: str, repo: str) -> str:
            """Return read-only repository metadata."""
            return _to_json(tools.repo_get_metadata(_candidate_ref(config, owner, repo)))

        @function_tool
        def repo_get_issues(owner: str, repo: str, filters_json: str = "{}") -> str:
            """Return read-only candidate issues for a repository."""
            filters = json.loads(filters_json) if filters_json else None
            return _to_json(tools.repo_get_issues(_candidate_ref(config, owner, repo), filters))

        @function_tool
        def workspace_run(cmd: str, timeout_seconds: int | None = None) -> str:
            """Run a shell command inside the Docker workspace."""
            return _to_json(tools.workspace_run(cmd, timeout_seconds=timeout_seconds))

        @function_tool
        def workspace_apply_patch(diff: str) -> str:
            """Apply a unified diff inside the Docker workspace."""
            return _to_json(tools.workspace_apply_patch(diff))

        @function_tool
        def aci_view(path: str, start_line: int = 1, max_lines: int = 200) -> str:
            """View a workspace file with line numbers and bounded output."""
            return _to_json(tools.aci_view(path, start_line=start_line, max_lines=max_lines))

        @function_tool
        def aci_search(pattern: str, path: str = ".", max_results: int = 80) -> str:
            """Search workspace files with ripgrep and bounded output."""
            return _to_json(tools.aci_search(pattern, path=path, max_results=max_results))

        @function_tool
        def aci_find_files(pattern: str, path: str = ".", max_results: int = 80) -> str:
            """Find workspace files by glob-like filename pattern with bounded output."""
            return _to_json(tools.aci_find_files(pattern, path=path, max_results=max_results))

        @function_tool
        def aci_replace(path: str, old_str: str, new_str: str) -> str:
            """Replace exactly one matching string in a workspace file."""
            return _to_json(tools.aci_replace(path, old_str, new_str))

        @function_tool
        def aci_insert(path: str, insert_after_line: int, text: str) -> str:
            """Insert text after a specific 1-indexed line; use 0 to insert at file start."""
            return _to_json(tools.aci_insert(path, insert_after_line, text))

        @function_tool
        def aci_create(path: str, content: str) -> str:
            """Create a new workspace file without overwriting existing files."""
            return _to_json(tools.aci_create(path, content))

        @function_tool
        def aci_undo() -> str:
            """Undo the latest successful ACI edit when a patch or verification step fails."""
            return _to_json(tools.aci_undo())

        @function_tool
        def aci_verify(
            command: str,
            path: str = "repo",
            timeout_seconds: int | None = None,
        ) -> str:
            """Run a verification command inside the repository and return bounded output."""
            return _to_json(tools.aci_verify(command, path=path, timeout_seconds=timeout_seconds))

        @function_tool
        def aci_suggest_verification(path: str = "repo") -> str:
            """Suggest likely lightweight verification commands from repository files."""
            return _to_json(tools.aci_suggest_verification(path))

        @function_tool(name_override=RECOVERY_TOOL_NAME)
        def aci_recover_invalid_action(
            recovery_kind: str,
            message: str,
            attempted_tool: str = "",
        ) -> str:
            """Record a rejected malformed, unknown, or multi-tool model action."""
            return _to_json(
                tools.aci_recover_invalid_action(recovery_kind, message, attempted_tool)
            )

        @function_tool
        def aci_submit_patch(
            path: str = "repo",
            no_command_verification_rationale: str = "",
        ) -> str:
            """Return the current workspace git diff as the shadow submission patch."""
            return _to_json(
                tools.aci_submit_patch(path, no_command_verification_rationale)
            )

        agent = Agent(
            name="contribarena-contributor",
            instructions=build_agent_instructions(config),
            tools=[
                repo_search,
                repo_check_eligibility,
                repo_get_metadata,
                repo_get_issues,
                workspace_run,
                workspace_apply_patch,
                aci_view,
                aci_search,
                aci_find_files,
                aci_replace,
                aci_insert,
                aci_create,
                aci_undo,
                aci_verify,
                aci_suggest_verification,
                aci_recover_invalid_action,
                aci_submit_patch,
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
                model_provider=ActionGuardingModelProvider(model_provider),
                workflow_name="ContribArena M0.2.2" if config.issue else "ContribArena M0.2.1",
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
        tools: ContributorTools,
        reason: str,
    ) -> AgentFinalResult:
        candidate = tools.repo_search()[0]
        if not isinstance(candidate, RepoCandidate):
            candidate = _first_config_candidate(config)
        eligibility = tools.repo_check_eligibility(candidate)
        metadata = tools.repo_get_metadata(candidate)
        issues = tools.repo_get_issues(candidate)
        command = tools.workspace_run("pwd")

        opportunity = OpportunitySummary(
            title="Inspect repository and identify a low-risk follow-up",
            rationale="M0.0 local fallback creates a structured result when LLM dependencies are unavailable.",
            risk="low",
            source=_issue_source(issues[0]) if issues else "",
        )
        return AgentFinalResult(
            status="completed" if eligibility.eligible else "blocked",
            repo=RepoSummary(owner=candidate.owner, name=candidate.repo, url=str(candidate.url)),
            repo_profile=(
                f"# Repo Profile: {candidate.full_name}\n\n"
                f"- URL: {candidate.url}\n"
                f"- Branch: {candidate.branch or _metadata_default_branch(metadata)}\n"
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


def _candidate_ref(config: RunConfig, owner: str, repo: str) -> RepoCandidate:
    for candidate in config.discovery.candidates:
        if candidate.owner == owner and candidate.repo == repo:
            return candidate
    return RepoCandidate(owner=owner, repo=repo, url=f"https://github.com/{owner}/{repo}")


def build_agent_instructions(config: RunConfig) -> str:
    boundary = (
        "Owned-live mode still does not give you direct GitHub write authority; submit a "
        "minimal verified patch and the harness will handle governed branch push and PR creation. "
        if config.run.mode == "owned_live"
        else "Shadow mode means no GitHub writes. "
    )
    base = (
        "You are an autonomous open-source contributor running inside ContribArena. "
        f"{boundary}Repository code interaction must go through workspace or ACI tools. "
        "Use workspace_run for setup, cloning, and "
        "unusual shell operations; prefer ACI tools for navigation, search, edits, "
        "verification, undo, and final patch submission. Use exactly one tool call at "
        "a time. If output is too broad, narrow the search instead of repeating it. "
        "If an edit or verification fails, inspect the smallest relevant context, fix "
        "once, or use aci_undo before trying a safer edit. Ask aci_suggest_verification "
        "when unsure how to test, verify locally with aci_verify or workspace_run, call "
        "aci_submit_patch, then finish with the structured ContribArena result. Do not "
        "continue exploring after the expected shadow patch and verification summary "
        "are complete."
    )
    if config.issue is None:
        return (
            base
            + " Use the provided GitHub tools to discover and select exactly one low-risk task. "
            "Make the smallest useful reviewable change."
        )
    return (
        base
        + " This run has an explicit issue/problem statement. Do not self-select a typo, "
        "docs cleanup, or unrelated low-risk task. Address only the configured problem. "
        "A completed result must include a submitted diff, successful focused local "
        "verification, problem_statement_summary, reproduction_notes, and "
        "verification_summary. If any of those are blocked, return blocked or failed "
        "with explicit blockers."
    )


def _first_config_candidate(config: RunConfig) -> RepoCandidate:
    if not config.discovery.candidates:
        raise AgentError("local-stub requires at least one configured discovery candidate")
    return config.discovery.candidates[0]


def _to_json(value: object) -> str:
    return to_model_json(value)


def _issue_source(issue: object) -> str:
    if hasattr(issue, "url"):
        return str(issue.url)  # type: ignore[attr-defined]
    if isinstance(issue, dict):
        return str(issue.get("url") or "")
    return ""


def _metadata_default_branch(metadata: object) -> str:
    if hasattr(metadata, "default_branch"):
        return str(metadata.default_branch)  # type: ignore[attr-defined]
    if isinstance(metadata, dict):
        return str(metadata.get("default_branch") or "main")
    return "main"
