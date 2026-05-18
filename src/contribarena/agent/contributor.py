from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from agents.exceptions import MaxTurnsExceeded
from agents.models.interface import ModelProvider
from openai.types.responses import ResponseFunctionToolCall

from contribarena.agent.invocation import AgentInvocationContext, AgentInvocationResult
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
        invocation_context: AgentInvocationContext | None = None,
    ) -> AgentInvocationResult:
        if config.run.model == "local-stub":
            return AgentInvocationResult(
                content="local-stub completed a deterministic fallback run",
                stopped_reason="local_stub",
                legacy_final_result=self._run_local_stub(config, tools, reason="model=local-stub"),
            )
        if model_provider is None:
            raise AgentError("model_provider is required for non-local-stub runs")
        return self._run_agents_sdk(config, tools, prompt, model_provider, invocation_context)

    def _run_agents_sdk(
        self,
        config: RunConfig,
        tools: ContributorTools,
        prompt: str,
        model_provider: ModelProvider,
        invocation_context: AgentInvocationContext | None = None,
    ) -> AgentInvocationResult:
        try:
            from agents import (
                Agent,
                ModelSettings,
                RunConfig as AgentsRunConfig,
                Runner,
                function_tool,
            )
            from agents.memory import SQLiteSession
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
        def aci_apply_patch(
            operations_json: str,
            rationale: str = "",
            expected_files_json: str = "[]",
        ) -> str:
            """Apply structured edits. Example operations_json: [{"type":"update_file","path":"repo/app.py","content":"new text\n"}]."""
            try:
                operations = json.loads(operations_json)
                expected_files = json.loads(expected_files_json) if expected_files_json else []
            except JSONDecodeError as exc:
                return _to_json(
                    tools.aci_recover_invalid_action(
                        "invalid_tool_arguments",
                        f"aci_apply_patch received invalid JSON: {exc}",
                        attempted_tool="aci_apply_patch",
                    )
                )
            if not isinstance(expected_files, list):
                return _to_json(
                    tools.aci_recover_invalid_action(
                        "invalid_tool_arguments",
                        "aci_apply_patch expected_files_json must decode to a JSON list",
                        attempted_tool="aci_apply_patch",
                    )
                )
            return _to_json(tools.aci_apply_patch(operations, rationale, expected_files))

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

        @function_tool
        def aci_clean_generated(path: str = "repo") -> str:
            """Clean generated verification/cache artifacts before submitting a patch."""
            return _to_json(tools.aci_clean_generated(path))

        @function_tool
        def operator_report_progress(
            phase: str,
            status: str,
            summary: str,
            evidence_refs: str = "",
        ) -> str:
            """Report short, evidence-linked operator progress without ending the run."""
            return _to_json(tools.operator_report_progress(phase, status, summary, evidence_refs))

        @function_tool
        def aci_runtime_get_context(scope: str = "run") -> str:
            """Return the run runtime context: goals, guidance, memory handles, and tracked PR summaries."""
            return _to_json(tools.aci_runtime_get_context(scope))

        @function_tool
        def aci_memory_get_context(scope: str = "run") -> str:
            """Return bounded run/repo/global memory context for this run."""
            return _to_json(tools.aci_memory_get_context(scope))

        @function_tool
        def aci_memory_search(
            query: str,
            intent: str = "unknown",
            max_results: int = 5,
        ) -> str:
            """Search prior run evidence and current working memory."""
            return _to_json(tools.aci_memory_search(query, intent, max_results))

        @function_tool
        def aci_memory_note(
            scope: str,
            text: str,
            tags_json: str = "[]",
            confidence: str = "medium",
        ) -> str:
            """Record a concrete memory note. In M0.6.1, only scope='run' updates working memory; repo/global notes are event-log only."""
            return _to_json(tools.aci_memory_note(scope, text, tags_json, confidence))

        @function_tool
        def aci_memory_plan_update(
            action: str,
            item_id: str = "",
            text: str = "",
            status: str = "",
        ) -> str:
            """Add or update a run-local memory plan item."""
            return _to_json(tools.aci_memory_plan_update(action, item_id, text, status))

        @function_tool
        def aci_goal_update(
            objective: str = "",
            status: str = "active",
            evidence: str = "",
        ) -> str:
            """Create or update the single short-term runtime goal. status must be exactly "active", "complete", or "abandoned"; complete/abandoned require evidence."""
            return _to_json(tools.aci_goal_update(objective, status, evidence))

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
            return _to_json(tools.aci_submit_patch(path, no_command_verification_rationale))

        agent = Agent(
            name="contribarena-contributor",
            instructions=build_agent_instructions(config),
            tools=[
                repo_search,
                repo_check_eligibility,
                repo_get_metadata,
                repo_get_issues,
                workspace_run,
                aci_view,
                aci_search,
                aci_find_files,
                aci_apply_patch,
                aci_undo,
                aci_verify,
                aci_suggest_verification,
                aci_clean_generated,
                operator_report_progress,
                aci_runtime_get_context,
                aci_memory_get_context,
                aci_memory_search,
                aci_memory_note,
                aci_memory_plan_update,
                aci_goal_update,
                aci_recover_invalid_action,
                aci_submit_patch,
            ],
            model=config.run.model,
            model_settings=ModelSettings(
                max_tokens=config.run.budget.max_tokens,
                parallel_tool_calls=False,
            ),
        )
        try:
            run_config = AgentsRunConfig(
                model_provider=ActionGuardingModelProvider(model_provider),
                workflow_name="ContribArena M0.2.2" if config.issue else "ContribArena M0.2.1",
                # trace.jsonl is the M0 source of truth; SDK spans can be enabled later.
                tracing_disabled=True,
            )
            sdk_session = None
            if invocation_context is not None:
                if invocation_context.sdk_session is None:
                    invocation_context.sdk_session = SQLiteSession(config.run.id or "contribarena-run")
                sdk_session = invocation_context.sdk_session
            result = Runner.run_sync(
                agent,
                prompt,
                max_turns=config.run.budget.max_steps,
                run_config=run_config,
                session=sdk_session,
            )
            return AgentInvocationResult(
                content=_stringify_final_output(result.final_output),
                stopped_reason="content",
                usage=getattr(result, "usage", None),
                tool_call_count=_count_tool_calls(getattr(result, "new_items", [])),
            )
        except MaxTurnsExceeded as exc:
            return AgentInvocationResult(
                content=f"Invocation stopped at max turns: {exc}",
                stopped_reason="max_turns",
                error_message=str(exc),
            )
        except Exception as exc:
            return AgentInvocationResult(
                content=f"Provider invocation failed: {exc}",
                stopped_reason="provider_error",
                error_message=str(exc),
            )

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
    if config.run.mode == "owned_live":
        boundary = (
            "Owned-live mode still does not give you direct GitHub write authority; submit a "
            "minimal verified patch and the harness will handle governed branch push and PR creation. "
        )
    elif config.run.mode == "external_live":
        boundary = (
            "External-live mode still does not give you direct GitHub write authority; freely "
            "discover an eligible external repository, choose a defensibly low-risk task, and "
            "submit a minimal verified patch. The harness will handle fork-only PR creation and "
            "lifecycle governance. "
        )
    else:
        boundary = "Shadow mode means no GitHub writes. "
    base = (
        "You are an autonomous open-source contributor running inside ContribArena. "
        f"{boundary}Repository code interaction must go through workspace or ACI tools. "
        "Use workspace_run for setup, cloning, and "
        "unusual shell operations; prefer ACI tools for navigation, search, structured edits, "
        "verification, undo, and final patch submission. Use exactly one tool call at "
        "a time. If output is too broad, narrow the search instead of repeating it. "
        "Use aci_apply_patch with operations_json containing create_file, update_file, "
        "delete_file, or move_file operations as the primary edit tool; do not emit "
        "raw git diffs for source edits. Do not use workspace_run, shell redirection, "
        "sed, python scripts, or git commands to edit files; submit-time review requires "
        "unified-editor provenance for changed source files. "
        "If an edit or verification fails, inspect the smallest relevant context, fix "
        "once, or use aci_undo before trying a safer edit. Ask aci_suggest_verification "
        "when unsure how to test, verify locally with aci_verify or workspace_run, call "
        "aci_submit_patch, then finish with the structured ContribArena result. Use "
        "operator_report_progress at phase boundaries or when discovery, selection, "
        "verification, governance, or PR work would otherwise look silent; keep it short, "
        "evidence-linked, and do not expose hidden chain-of-thought. Call "
        "aci_runtime_get_context(scope='run') early; it returns guidance availability, "
        "goal context, memory hints, and tracked PR summaries. Treat "
        "the long-term goal as direction, not a replacement for this run's concrete task. "
        "Use aci_goal_update only for the single short-term goal, and mark it complete "
        "only after evidence proves the objective is done. If aci_goal_update returns "
        "terminal_status=goal_abandon_limit, end this run with a final structured "
        "blocked result. If guidance is available, "
        "read the returned path relative to the workspace root, not repo/. "
        "Then inspect repository-local guidance such as AGENTS.md, CONTRIBUTING.md, "
        "and .github templates when present. You have a working "
        "memory scratchpad for this run: use aci_memory_note(scope='run', ...) for "
        "concrete observations, and aci_memory_plan_update for multi-step plans. "
        "Memory is optional; use it when the task spans many tool calls. Do not "
        "continue exploring after the expected shadow patch and verification summary "
        "are complete."
    )
    if config.issue is None:
        return (
            base
            + " Use the provided GitHub tools to discover and select exactly one low-risk task. "
            "For external-live runs, avoid repositories with anti-AI or anti-bot contribution "
            "language and include maintainer-fit reasoning in the repo profile. Make the smallest "
            "useful reviewable change."
        )
    return (
        base + " This run has an explicit issue/problem statement. Do not self-select a typo, "
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


def _stringify_final_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if hasattr(output, "model_dump_json"):
        return str(output.model_dump_json())
    return str(output)


def _count_tool_calls(items: object) -> int:
    if not isinstance(items, list):
        return 0
    count = 0
    for item in items:
        raw_item = getattr(item, "raw_item", None)
        item_type = getattr(item, "type", None) or getattr(raw_item, "type", None)
        if isinstance(raw_item, ResponseFunctionToolCall) or item_type in {
            "function_call",
            "tool_call",
        }:
            count += 1
    return count
