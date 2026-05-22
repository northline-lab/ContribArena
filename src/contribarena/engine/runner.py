from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from contribarena.agent import AgentInvocationContext, AgentInvocationResult
from contribarena.agent import ContributorAgent
from contribarena.agent.prompts import build_goal_prompt
from contribarena.config.schema import OwnedRepositoryPolicy, RepoCandidate, RunConfig
from contribarena.engine.agent_loop import (
    AgentLoopState,
    capture_cursor,
    derive_agent_result,
    render_continuation_context,
    review_invocation,
    trace_invocation_review,
    LoopOutcome,
)
from contribarena.engine.artifacts import ArtifactWriter
from contribarena.engine.context import ContextBuilder
from contribarena.engine.external_lifecycle import lifecycle_record_for_opened_pr
from contribarena.engine.guidance import guidance_artifact_payload, install_guidance_sidecar
from contribarena.engine.goals import GoalService
from contribarena.engine.judgement import (
    build_judge_dimension_packets,
    build_judge_packet,
    judge_run,
)
from contribarena.engine.lifecycle import (
    apply_quality_gate_to_result,
    build_ci_status,
    build_pr_draft,
    evaluate_contribution_quality,
    live_action_log_entries,
    render_postmortem,
    render_pr_description,
    render_quality_gate_section,
)
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_attempt,
    record_governance_pr,
    save_governance_state,
    upsert_lifecycle_record,
)
from contribarena.engine.operator_events import OperatorProgressWriter, truncate_for_operator
from contribarena.engine.runtime_config import apply_output_dir
from contribarena.engine.seasons import admit_run
from contribarena.engine.seasons import (
    SeasonStore,
    mark_participant_replacement_due,
    mark_participant_run_finished,
    mark_participant_run_started,
    participant_memory_root,
    repo_workspace_dir,
    workspace_key_for,
)
from contribarena.engine.surface_summary import build_run_summary
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.errors import AgentError, BudgetExhausted, InfrastructureError
from contribarena.memory import MemoryService
from contribarena.models import (
    AgentFinalResult,
    CiCheck,
    CiStatus,
    CommandResult,
    GovernanceDecision,
    PrLifecycleRecord,
    PullRequestDraft,
    QualityGateResult,
    RunState,
    TerminalState,
)
from contribarena.models.assistant_updates import AssistantUpdate, AssistantUpdateKind
from contribarena.providers import ContribArenaModelProvider, TracingModelProvider
from contribarena.providers.action_guard import visible_text_from_turn
from contribarena.providers.turns import ProviderTurn
from contribarena.trace import TraceWriter
from contribarena.tools.github_pr import (
    ForkEnsureResult,
    GitHubPullRequestClient,
    LabelOperationResult,
    PullRequestCreateResult,
)
from contribarena.tools.repo_eligibility import repo_check_eligibility
from contribarena.tools.repo_metadata import repo_get_metadata
from contribarena.tools.registry import ToolRegistry


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    status: str
    tool_calls: int
    terminal_reason: str = ""
    terminal_layer: str = ""


@dataclass
class OwnedLivePrExecutionResult:
    strategy: str
    head: str = ""
    requested_fork_owner: str = ""
    fork_result: ForkEnsureResult | None = None
    push_result: CommandResult | None = None
    pr_result: PullRequestCreateResult | None = None
    label_ensure_result: LabelOperationResult | None = None
    label_set_result: LabelOperationResult | None = None
    label_skipped_reason: str = ""


_LIVE_PR_RETRY_ATTEMPTS = 3
_LIVE_PR_RETRY_SLEEP_SECONDS = 2.0


@dataclass
class ExternalLiveReviewResult:
    passed: bool
    reasons: list[str]
    warnings: list[str]
    target: RepoCandidate


class Runner:
    def __init__(
        self,
        agent: ContributorAgent | None = None,
        pr_client: object | None = None,
    ) -> None:
        self.agent = agent or ContributorAgent()
        self.pr_client = pr_client

    def run(
        self, config: RunConfig, output_dir: Path | None = None, verbose: bool = False
    ) -> RunResult:
        config = apply_output_dir(config, output_dir)
        repo_slug = (
            config.discovery.candidates[0].full_name
            if config.discovery.candidates
            else config.discovery.query or "github-discovery"
        )
        run_id = config.run.id or uuid.uuid4().hex[:12]
        try:
            admission = admit_run(config)
        except Exception as exc:
            _write_rejected_run_artifacts(
                config=config,
                run_id=run_id,
                repo_slug=repo_slug,
                reason=str(exc),
                verbose=verbose,
            )
            raise
        if admission.ranked:
            config = config.model_copy(
                update={
                    "run": config.run.model_copy(
                        update={
                            "season_id": admission.season_id,
                            "participant_id": admission.participant_id,
                            "wake_source": admission.wake_source,
                        }
                    )
                },
                deep=True,
            )
        artifacts = ArtifactWriter(
            config.artifacts.output_root,
            run_id,
            repo_slug,
            config.run.model,
        )
        trace = TraceWriter(artifacts.run_dir / "trace.jsonl", run_id)
        operator = OperatorProgressWriter(
            artifacts.register("operator_events.jsonl", kind="jsonl"),
            run_id,
            stream=verbose,
        )
        operator.write(
            "run",
            "started",
            "started ContribArena run",
            evidence=["config.json", "trace.jsonl"],
            payload={
                "mode": config.run.mode,
                "model": config.run.model,
                "repo": repo_slug,
                "season_id": admission.season_id,
                "participant_id": admission.participant_id,
                "wake_source": admission.wake_source,
            },
        )
        trace.write(
            RunState.RUN_STARTED,
            "run.started",
            {
                "verbose": verbose,
                "season_id": admission.season_id,
                "participant_id": admission.participant_id,
                "wake_source": admission.wake_source,
            },
        )
        trace.write(RunState.CONFIG_LOADED, "config.loaded", {"model": config.run.model})

        artifacts.write_json("config.json", config.model_dump(mode="json"))
        workspace_config = config.workspace
        workspace_dir = repo_workspace_dir(config, repo_slug)
        workspace_key = workspace_key_for(config, repo_slug)
        if workspace_dir is not None and workspace_key:
            workspace_config = config.workspace.model_copy(
                update={
                    "persistent_key": workspace_key,
                    "persistent_metadata_path": workspace_dir / "container_id",
                }
            )
            config = config.model_copy(update={"workspace": workspace_config}, deep=True)
            _write_clone_state(workspace_dir, repo_slug=repo_slug, run_id=run_id)
        workspace = DockerWorkspaceManager(run_id, repo_slug, workspace_config)
        budget = BudgetTracker(config.run.budget)
        capture = ArtifactCapture()
        memory_config = config.memory
        participant_memory = participant_memory_root(config)
        if participant_memory is not None and config.memory.root != participant_memory:
            memory_config = config.memory.model_copy(update={"root": participant_memory})
        memory = MemoryService(memory_config, run_id=run_id, repo_full_name=repo_slug)
        goals = GoalService(
            config,
            run_id=run_id,
            evidence_ref_validator=lambda ref: _resolve_evidence_ref(
                ref,
                capture=capture,
                artifacts=artifacts,
                workspace=workspace,
            ),
        )
        _seed_issue_goal(config, goals)
        memory.set_goal_context(goals.context)
        if memory.enabled:
            memory_context = memory.start_run_context(
                repo_slug,
                tracked_prs=_tracked_lifecycle_records_for_runtime(config, repo_slug),
            )
            artifacts.write_json(
                "memory_context.json",
                memory_context.model_dump(mode="json"),
        )
        terminal: TerminalState | None = None
        workspace_started = False
        workspace_finalized = False

        try:
            if admission.ranked and admission.participant_id:
                mark_participant_run_started(
                    SeasonStore.from_config(config),
                    admission.season_id,
                    admission.participant_id,
                    run_id=run_id,
                    repo_slug=repo_slug,
                    wake_source=admission.wake_source,
                )
            trace.write(
                RunState.WORKSPACE_STARTING,
                "workspace.starting",
                {
                    "container": workspace.container_name,
                    "cleanup_policy": config.workspace.cleanup_policy,
                },
            )
            workspace.start()
            workspace_started = True
            sync_result = _sync_persistent_workspace_repository(workspace, config)
            if sync_result is not None:
                capture.record_command(sync_result)
            trace.write(
                RunState.WORKSPACE_READY,
                "workspace.ready",
                {"container": workspace.container_name},
            )
            operator.write(
                "workspace",
                "ready",
                "workspace container is ready",
                evidence=["trace.jsonl"],
                payload={"container": workspace.container_name},
            )
            guidance = install_guidance_sidecar(
                workspace,
                config,
                run_id=run_id,
                repo_full_name=repo_slug,
            )
            memory.set_guidance_status(
                available=guidance.installed,
                skipped_reason=guidance.skipped_reason,
                error=guidance.error,
            )
            if memory.enabled:
                # Rewrite the initial context after guidance availability is known.
                artifacts.write_json(
                    "memory_context.json",
                    memory.context.model_dump(mode="json"),
                )
            artifacts.write_json(
                "repo_guidance.json",
                guidance_artifact_payload(guidance),
                required=False,
            )
            trace.write(
                RunState.AGENT_CONTEXT_LOADED,
                "guidance.sidecar_installed",
                {
                    "installed": guidance.installed,
                    "skipped_reason": guidance.skipped_reason,
                    "error": guidance.error,
                },
            )
            registry = ToolRegistry(
                config=config,
                workspace=workspace,
                trace=trace,
                budget=budget,
                capture=capture,
                operator=operator,
                memory=memory,
                goals=goals,
                model_provider=TracingModelProvider(ContribArenaModelProvider(config.models), trace)
                if config.run.model != "local-stub"
                else None,
            )
            trace.write(RunState.AGENT_INITIALIZED, "agent.initialized", {"agent": "builtin"})
            operator.write(
                "agent",
                "started",
                "built-in contributor agent started",
                evidence=["trace.jsonl"],
                payload={"agent": "builtin"},
            )
            prompt = (
                ContextBuilder().build_system_prompt(config) + "\n\n" + build_goal_prompt(config)
            )
            trace.write(
                RunState.AGENT_CONTEXT_LOADED,
                "agent.context_loaded",
                {"prompt_bytes": len(prompt.encode("utf-8"))},
            )
            loop_state, loop_terminal, loop_outcome = self._run_agent_loop(
                config=config,
                run_id=run_id,
                registry=registry,
                initial_prompt=prompt,
                trace=trace,
                operator=operator,
                capture=capture,
                goals=goals,
                memory=memory,
            )
            result = derive_agent_result(
                config=config,
                capture=capture,
                goals=goals,
                loop_state=loop_state,
                terminal=loop_terminal,
            )
            trace.write(
                RunState.AGENT_FINAL_RESULT,
                "agent.final_result",
                {"status": result.status, "derived": True},
            )
            # Compatibility alias for M0.9 readers. Remove in M0.10 after
            # surface/judgement consumers migrate to agent.terminal.
            operator.write(
                "agent",
                result.status,
                "agent loop reached terminal review",
                evidence=["trace.jsonl"],
                payload={
                    "status": result.status,
                    "invocations": loop_state.counters.invocations_used,
                    "terminal_reason": loop_terminal.reason if loop_terminal else "",
                    "loop_outcome": loop_outcome,
                },
            )
            agent_status = result.status
            _enforce_issue_completion(config, result, capture)
            patch = _submitted_patch(capture)
            quality_gate = evaluate_contribution_quality(config, result, capture, patch)
            apply_quality_gate_to_result(result, quality_gate)
            trace.write(
                RunState.AGENT_HARNESS_REVIEWED,
                "agent.harness_reviewed",
                {
                    "agent_status": agent_status,
                    "loop_outcome": loop_outcome,
                    "harness_status": result.status,
                    "quality_gate": quality_gate.status,
                },
            )
            terminal = loop_terminal or _terminal_state_for_result(
                result, capture, agent_status, quality_gate, loop_outcome=loop_outcome
            )
            self._write_issue_artifacts(config, artifacts, result, capture)
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
            artifacts.write_text("patch.diff", patch, kind="diff")
            artifacts.write_json("quality_gate.json", quality_gate.model_dump(mode="json"))
            trace.write(
                RunState.CONTRIBUTION_REVIEWED,
                "contribution.quality_gate",
                quality_gate.model_dump(mode="json"),
            )
            operator.write(
                "quality_gate",
                quality_gate.status,
                f"contribution quality gate {quality_gate.status}",
                evidence=["quality_gate.json", "trace.jsonl"],
                payload={
                    "blockers": quality_gate.blockers,
                    "warnings": quality_gate.warnings,
                },
            )
            pr_draft, terminal = _write_pr_lifecycle_artifacts(
                config=config,
                artifacts=artifacts,
                trace=trace,
                operator=operator,
                result=result,
                workspace=workspace,
                capture=capture,
                quality_gate=quality_gate,
                terminal=terminal,
                patch=patch,
                pr_client=self.pr_client,
            )
            _write_capture_artifacts(artifacts, capture)
            artifacts.write_text("test_log.txt", _command_log(capture.commands), required=False)
            artifacts.write_json("terminal_state.json", terminal.model_dump(mode="json"))
            artifacts.write_markdown(
                "quality_report.md",
                _quality_report(config, result, capture, terminal, quality_gate, pr_draft),
                required=False,
            )
            _write_memory_artifacts(artifacts, memory, terminal)
            _write_goal_artifacts(artifacts, goals)
            trace.write(
                RunState.RUN_TERMINAL,
                "run.terminal",
                terminal.model_dump(mode="json"),
            )
            operator.write(
                "run",
                terminal.status,
                f"run reached terminal state: {terminal.reason}",
                evidence=["terminal_state.json", "trace.jsonl"],
                payload=terminal.model_dump(mode="json"),
            )
            trace.write(
                RunState.RUN_COMPLETED,
                "run.completed",
                {
                    "status": terminal.status,
                    "terminal_reason": terminal.reason,
                    "terminal_layer": terminal.layer,
                },
            )
            _record_replacement_if_due(artifacts, config, run_id, terminal)
            if workspace_started:
                _finalize_workspace(workspace, trace, terminal, config.workspace.cleanup_policy)
                workspace_finalized = True
            _write_run_summary_artifact(artifacts, config, run_id, repo_slug, terminal)
            _write_judgement_artifacts(
                artifacts,
                config,
                run_id,
                terminal=terminal,
                trace=trace,
                operator=operator,
            )
            _write_run_summary_artifact(artifacts, config, run_id, repo_slug, terminal)
            trace.write(
                RunState.ARTIFACTS_WRITTEN,
                "artifacts.written",
                {"count": len(artifacts.entries) + 1},
            )
            artifacts.finalize_manifest()
            mark_participant_run_finished(
                config,
                run_id=run_id,
                status=terminal.status,
                repo_slug=repo_slug,
                latest_goal_summary=_latest_goal_summary(goals),
            )
            return RunResult(
                run_id=run_id,
                run_dir=artifacts.run_dir,
                status=terminal.status,
                tool_calls=budget.total_steps,
                terminal_reason=terminal.reason,
                terminal_layer=terminal.layer,
            )
        except Exception as exc:
            terminal = _terminal_state_for_exception(exc)
            operator.write(
                "run",
                terminal.status,
                f"run failed before normal completion: {terminal.reason}",
                evidence=["terminal_state.json", "trace.jsonl"],
                payload={
                    "error_type": type(exc).__name__,
                    "error": truncate_for_operator(str(exc)),
                    **terminal.model_dump(mode="json"),
                },
            )
            failure_state = (
                RunState.WORKSPACE_FAILED
                if terminal.layer == "workspace"
                else RunState.BUDGET_EXHAUSTED
                if terminal.layer == "budget"
                else RunState.AGENT_ERROR
            )
            trace.write(
                failure_state,
                "run.failed",
                {
                    "error": str(exc),
                    "terminal_reason": terminal.reason,
                    "terminal_layer": terminal.layer,
                },
            )
            _write_capture_artifacts(artifacts, capture)
            artifacts.write_text("patch.diff", _submitted_patch(capture), kind="diff")
            artifacts.write_text("test_log.txt", _command_log(capture.commands), required=False)
            artifacts.write_json("terminal_state.json", terminal.model_dump(mode="json"))
            artifacts.write_markdown(
                "quality_report.md",
                _failure_quality_report(terminal, capture),
                required=False,
            )
            _write_memory_artifacts(artifacts, memory, terminal)
            _write_goal_artifacts(artifacts, goals)
            trace.write(
                RunState.RUN_TERMINAL,
                "run.terminal",
                terminal.model_dump(mode="json"),
            )
            _record_replacement_if_due(artifacts, config, run_id, terminal)
            if workspace_started:
                _finalize_workspace(workspace, trace, terminal, config.workspace.cleanup_policy)
                workspace_finalized = True
            _write_run_summary_artifact(artifacts, config, run_id, repo_slug, terminal)
            _write_judgement_artifacts(
                artifacts,
                config,
                run_id,
                terminal=terminal,
                trace=trace,
                operator=operator,
            )
            _write_run_summary_artifact(artifacts, config, run_id, repo_slug, terminal)
            artifacts.finalize_manifest()
            mark_participant_run_finished(
                config,
                run_id=run_id,
                status=terminal.status,
                repo_slug=repo_slug,
                latest_goal_summary=_latest_goal_summary(goals),
            )
            raise
        finally:
            if workspace_started and not workspace_finalized:
                _finalize_workspace(workspace, trace, terminal, config.workspace.cleanup_policy)

    def _run_agent_loop(
        self,
        *,
        config: RunConfig,
        run_id: str,
        registry: ToolRegistry,
        initial_prompt: str,
        trace: TraceWriter,
        operator: OperatorProgressWriter,
        capture: ArtifactCapture,
        goals: GoalService,
        memory: MemoryService,
    ) -> tuple[AgentLoopState, TerminalState | None, LoopOutcome]:
        loop_state = AgentLoopState()
        prompt = initial_prompt
        invocation_context = AgentInvocationContext()
        provider = TracingModelProvider(ContribArenaModelProvider(config.models), trace)
        while True:
            invocation_context.current_phase = goals.context.current_phase
            invocation_context.current_sub_phase = goals.context.current_sub_phase
            seq = loop_state.counters.invocations_used + 1
            invocation_context.invocation_seq = seq
            invocation_context.assistant_update_builder = lambda turn, tool_call: _build_assistant_update(
                config=config,
                run_id=run_id,
                goals=goals,
                invocation_context=invocation_context,
                turn=turn,
                tool_call=tool_call,
            )
            invocation_context.assistant_update_sink = capture.record_assistant_update
            continuation = seq > 1
            trace.write(
                RunState.AGENT_ACTING,
                "agent.invocation_started",
                {"invocation_seq": seq, "continuation": continuation},
            )
            operator.write(
                "agent",
                "invocation_started",
                "agent invocation started",
                evidence=["trace.jsonl"],
                payload={"seq": seq, "continuation": continuation},
            )
            registry.budget.reset_for_invocation()
            before = capture_cursor(capture, goals, memory)
            usage_before = provider.usage.snapshot()
            raw_invocation = self.agent.run(
                config,
                registry,
                prompt,
                model_provider=provider,
                invocation_context=invocation_context,
            )
            invocation = _normalize_invocation_result(raw_invocation)
            if invocation.stopped_reason == "provider_error":
                trace.write(
                    RunState.AGENT_ERROR,
                    "agent.invocation_failed",
                    {
                        "invocation_seq": seq,
                        "reason": "model_runtime",
                        "error": truncate_for_operator(invocation.error_message),
                    },
                )
            usage_payload = _invocation_usage_payload(invocation, usage_before, provider.usage.snapshot())
            trace.write(
                RunState.AGENT_ACTING,
                "agent.invocation_returned",
                {
                    "invocation_seq": seq,
                    "stopped_reason": invocation.stopped_reason,
                    "has_content": bool(invocation.content.strip()),
                    "sdk_tool_calls": invocation.tool_call_count,
                    "executed_tools": len(capture.aci_results) - before.aci_results,
                    "executed_commands": len(capture.commands) - before.commands,
                    **usage_payload,
                },
            )
            operator.write(
                "agent",
                "invocation_returned",
                "agent invocation returned",
                evidence=["trace.jsonl"],
                payload={
                    "seq": seq,
                    "sdk_tool_calls": invocation.tool_call_count,
                    "executed_tools": len(capture.aci_results) - before.aci_results,
                    "executed_commands": len(capture.commands) - before.commands,
                    **usage_payload,
                },
            )
            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=memory,
                before=before,
                state=loop_state,
                invocation=invocation,
            )
            trace_invocation_review(trace, review, loop_state.counters)
            if review.decision == "continue":
                trace.write(
                    RunState.AGENT_ACTING,
                    "agent.continued",
                    {"reason": review.reason, "invocation_seq": seq},
                )
                operator.write(
                    "agent",
                    "continued",
                    "continuing active agent goal",
                    evidence=["trace.jsonl"],
                    payload={"seq": seq, "reason": review.reason},
                )
                prompt = render_continuation_context(
                    config=config,
                    goals=goals,
                    state=loop_state,
                    progress=review.progress,
                )
                continue
            if review.terminal is not None:
                trace.write(
                    RunState.AGENT_FINAL_RESULT,
                    "agent.terminal",
                    {
                        **review.terminal.model_dump(mode="json"),
                        "sub_reason": review.sub_reason,
                    },
                )
            else:
                trace.write(
                    RunState.AGENT_FINAL_RESULT,
                    "agent.terminal",
                    {"reason": review.reason, "invocation_seq": seq},
                )
            return loop_state, review.terminal, review.outcome  # type: ignore[return-value]

    def _write_agent_artifacts(self, artifacts: ArtifactWriter, result: AgentFinalResult) -> None:
        artifacts.write_json("agent_final_result.json", result.model_dump(mode="json"))
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

    def _write_issue_artifacts(
        self,
        config: RunConfig,
        artifacts: ArtifactWriter,
        result: AgentFinalResult,
        capture: ArtifactCapture,
    ) -> None:
        if config.issue is None:
            return
        issue = config.issue
        sections = [
            f"# Problem Statement: {issue.title or 'Configured Issue'}",
            "",
        ]
        if issue.source_url:
            sections.extend([f"- Source: {issue.source_url}", ""])
        if issue.reproduction_hint:
            sections.extend(["## Reproduction Hint", "", issue.reproduction_hint, ""])
        if issue.verification_hint:
            sections.extend(["## Verification Hint", "", issue.verification_hint, ""])
        sections.extend(["## Statement", "", issue.problem_statement])
        artifacts.write_markdown("problem_statement.md", "\n".join(sections))
        artifacts.write_markdown(
            "reproduction_notes.md",
            result.reproduction_notes or "No reproduction notes were returned.",
        )
        artifacts.write_markdown(
            "verification_summary.md",
            result.verification_summary or _verification_summary_from_capture(capture),
        )


def _normalize_invocation_result(raw: object) -> AgentInvocationResult:
    if isinstance(raw, AgentInvocationResult):
        return raw
    if isinstance(raw, AgentFinalResult):
        return AgentInvocationResult(
            content=raw.workspace_summary.notes,
            stopped_reason="legacy_final_result",
            legacy_final_result=raw,
        )
    return AgentInvocationResult(content=str(raw or ""))


def _write_clone_state(workspace_dir: Path, *, repo_slug: str, run_id: str) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_slug": repo_slug,
        "run_id": run_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    (workspace_dir / "clone_state.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sync_persistent_workspace_repository(
    workspace: DockerWorkspaceManager,
    config: RunConfig,
) -> CommandResult | None:
    if not config.workspace.persistent_key and workspace.config.persistent_key is None:
        return None
    if not config.discovery.candidates:
        return None
    target = config.discovery.candidates[0]
    return workspace.sync_repository(str(target.url), target.branch or None)


def _write_rejected_run_artifacts(
    *,
    config: RunConfig,
    run_id: str,
    repo_slug: str,
    reason: str,
    verbose: bool,
) -> None:
    artifacts = ArtifactWriter(
        config.artifacts.output_root,
        run_id,
        repo_slug,
        config.run.model,
    )
    operator = OperatorProgressWriter(
        artifacts.register("operator_events.jsonl", kind="jsonl"),
        run_id,
        stream=verbose,
    )
    operator.write(
        "run",
        "rejected",
        "run rejected before execution",
        evidence=["config.json", "terminal_state.json"],
        payload={
            "reason": reason,
            "season_id": config.run.season_id or "",
            "participant_id": config.run.participant_id or "",
            "wake_source": config.run.wake_source,
        },
    )
    artifacts.write_json("config.json", config.model_dump(mode="json"))
    terminal = TerminalState(
        status="blocked",
        reason=reason,
        layer="run",
        message=reason,
    )
    artifacts.write_json("terminal_state.json", terminal.model_dump(mode="json"))
    artifacts.finalize_manifest()


def _invocation_usage_payload(
    invocation: AgentInvocationResult,
    before: dict[str, int] | None = None,
    after: dict[str, int] | None = None,
) -> dict[str, int | None]:
    usage = invocation.usage
    if usage is None and before is not None and after is not None:
        return {
            "requests": max(0, after["requests"] - before["requests"]),
            "input_tokens": max(0, after["input_tokens"] - before["input_tokens"]),
            "output_tokens": max(0, after["output_tokens"] - before["output_tokens"]),
            "total_tokens": max(0, after["total_tokens"] - before["total_tokens"]),
        }
    return {
        "requests": getattr(usage, "requests", None),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _submitted_patch(capture: ArtifactCapture) -> str:
    for result in reversed(capture.aci_results):
        if result.tool in {"aci_submit_patch_finalize", "aci_submit_patch"} and result.success:
            return result.output or ""
    return ""


def _latest_goal_summary(goals: GoalService) -> str:
    goal = goals.context.short_term
    if goal is None:
        return ""
    if goal.evidence_summary:
        return goal.evidence_summary
    return goal.objective


def _write_capture_artifacts(artifacts: ArtifactWriter, capture: ArtifactCapture) -> None:
    workspace_command = {
        "commands": [command.model_dump(mode="json") for command in capture.commands],
        "patches": [patch.model_dump(mode="json") for patch in capture.patches],
        "aci_results": [result.model_dump(mode="json") for result in capture.aci_results],
    }
    artifacts.write_json("workspace_command.json", workspace_command)
    artifacts.write_json(
        "trajectory.json",
        [step.model_dump(mode="json") for step in capture.steps],
    )
    _write_jsonl_artifact(
        artifacts,
        "assistant_updates.jsonl",
        [update.model_dump(mode="json") for update in capture.assistant_updates],
    )
    artifacts.write_text(
        "tool_violation_log.jsonl",
        "\n".join(json.dumps(row, ensure_ascii=True) for row in capture.tool_violations)
        + ("\n" if capture.tool_violations else ""),
        kind="jsonl",
        required=False,
    )
    _write_jsonl_artifact(
        artifacts,
        "phase_scout_project_comparison.jsonl",
        capture.phase_scout_project_rows,
    )
    _write_jsonl_artifact(
        artifacts,
        "phase_scout_opportunity_comparison.jsonl",
        capture.phase_scout_opportunity_rows,
    )
    _write_jsonl_artifact(
        artifacts,
        "phase_scout_duplicate_check.jsonl",
        capture.phase_scout_duplicate_rows,
    )
    _write_jsonl_artifact(
        artifacts,
        "phase_review_maintainer_review.jsonl",
        capture.phase_review_maintainer_rows,
    )
    _write_jsonl_artifact(
        artifacts,
        "phase_review_response.jsonl",
        capture.phase_review_response_rows,
    )
    _write_jsonl_artifact(
        artifacts,
        "discovery_log.jsonl",
        capture.discovery_rows,
    )


def _build_assistant_update(
    *,
    config: RunConfig,
    run_id: str,
    goals: GoalService,
    invocation_context: AgentInvocationContext,
    turn: ProviderTurn,
    tool_call: object | None,
) -> AssistantUpdate | None:
    text, redacted, truncated = visible_text_from_turn(turn)
    if not text:
        return None
    tool_name = str(getattr(tool_call, "name", "") or "")
    tool_call_id = str(getattr(tool_call, "call_id", "") or getattr(tool_call, "id", "") or "")
    phase = goals.context.current_phase
    sub_phase = goals.context.current_sub_phase or ""
    return AssistantUpdate(
        run_id=run_id,
        season_id=config.run.season_id or "",
        participant_id=config.run.participant_id or "",
        invocation_seq=invocation_context.invocation_seq,
        turn_id=turn.provider_response_id or "",
        phase=phase,
        sub_phase=sub_phase,
        kind=_assistant_update_kind(tool_name, phase),
        text=text,
        position="before_tool" if tool_name else "commentary_only",
        tool_name=tool_name,
        evidence_refs=[f"tool_call:{tool_call_id}"] if tool_call_id else [],
        truncated=truncated,
        redacted=redacted,
        hidden_dropped_count=turn.hidden_dropped_count,
    )


def _assistant_update_kind(tool_name: str, phase: str) -> AssistantUpdateKind:
    if tool_name in {"aci_verify", "aci_suggest_verification"}:
        return "verification"
    if tool_name in {"aci_submit_patch", "aci_submit_patch_finalize", "aci_dispute_review"}:
        return "review_response"
    if tool_name == "aci_goal_update" or tool_name.startswith("repo_"):
        return "decision" if phase == "scout" else "intent"
    if tool_name == "workspace_run":
        return "blocker"
    return "intent"


def _write_jsonl_artifact(
    artifacts: ArtifactWriter,
    name: str,
    rows: list[dict[str, object]],
) -> None:
    artifacts.write_text(
        name,
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows)
        + ("\n" if rows else ""),
        kind="jsonl",
        required=False,
    )


def _write_memory_artifacts(
    artifacts: ArtifactWriter,
    memory: MemoryService,
    terminal: TerminalState,
) -> None:
    if not memory.enabled:
        return
    artifacts.write_json(
        "working_memory.json",
        memory.working.model_dump(mode="json"),
        required=False,
    )
    report = memory.finalize_run(terminal.model_dump(mode="json"), artifacts.run_dir)
    artifacts.write_text(
        "memory_events.jsonl",
        memory.events_text(),
        kind="jsonl",
        required=False,
    )
    artifacts.write_json(
        "memory_write_report.json",
        report.model_dump(mode="json"),
        required=False,
    )


def _write_goal_artifacts(artifacts: ArtifactWriter, goals: GoalService) -> None:
    if not goals.enabled:
        return
    artifacts.write_json("goal_context.json", goals.context.model_dump(mode="json"), required=False)
    artifacts.write_text(
        "goal_events.jsonl",
        goals.events_text(),
        kind="jsonl",
        required=False,
    )
    artifacts.write_text(
        "phase_transition.jsonl",
        goals.phase_transition_text(),
        kind="jsonl",
        required=False,
    )


def _seed_issue_goal(config: RunConfig, goals: GoalService) -> None:
    if config.issue is None or goals.state.short_term is not None:
        return
    title = config.issue.title.strip() or "configured issue"
    goals.update(
        objective=f"Resolve the configured issue: {title}",
        status="active",
        scope="contribution",
        evidence="Harness seeded issue-solving contribution goal.",
    )


def _resolve_evidence_ref(
    ref: str,
    *,
    capture: ArtifactCapture,
    artifacts: ArtifactWriter,
    workspace: DockerWorkspaceManager,
) -> bool:
    if ref.startswith("tool_call:"):
        return _resolve_tool_call_ref(ref.removeprefix("tool_call:"), capture)
    if ref.startswith("artifact:"):
        return _resolve_artifact_ref(ref.removeprefix("artifact:"), capture, artifacts)
    if ref.startswith("workspace:"):
        path = ref.removeprefix("workspace:")
        result = workspace.run(f"test -e {shlex.quote(path)}", timeout_seconds=5)
        return result.exit_code == 0 and not result.timed_out
    if ref.startswith("git:"):
        sha = ref.removeprefix("git:")
        result = workspace.run(
            f"cd repo && git cat-file -e {shlex.quote(sha)}^{{commit}}",
            timeout_seconds=5,
        )
        return result.exit_code == 0 and not result.timed_out
    return False


def _resolve_tool_call_ref(value: str, capture: ArtifactCapture) -> bool:
    if value.isdigit():
        index = int(value)
        return 1 <= index <= len(capture.steps)
    return any(
        str(step.step) == value
        or step.tool == value
        or f"{step.tool}:{step.step}" == value
        for step in capture.steps
    )


def _resolve_artifact_ref(
    value: str,
    capture: ArtifactCapture,
    artifacts: ArtifactWriter,
) -> bool:
    if "#L" not in value:
        return False
    name, line_text = value.rsplit("#L", 1)
    try:
        line = int(line_text)
    except ValueError:
        return False
    if line <= 0:
        return False
    rows = _phase_artifact_rows(name, capture)
    if rows is not None:
        return line <= len(rows)
    path = artifacts.run_dir / name
    if not path.exists() or not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return any(index == line for index, _ in enumerate(handle, start=1))
    except OSError:
        return False


def _phase_artifact_rows(name: str, capture: ArtifactCapture) -> list[dict[str, object]] | None:
    rows_by_name = {
        "phase_scout_project_comparison.jsonl": capture.phase_scout_project_rows,
        "phase_scout_opportunity_comparison.jsonl": capture.phase_scout_opportunity_rows,
        "phase_scout_duplicate_check.jsonl": capture.phase_scout_duplicate_rows,
        "phase_review_maintainer_review.jsonl": capture.phase_review_maintainer_rows,
        "phase_review_response.jsonl": capture.phase_review_response_rows,
        "tool_violation_log.jsonl": capture.tool_violations,
    }
    return rows_by_name.get(name)


def _write_run_summary_artifact(
    artifacts: ArtifactWriter,
    config: RunConfig,
    run_id: str,
    repo_slug: str,
    terminal: TerminalState,
) -> None:
    summary = build_run_summary(
        config=config,
        run_id=run_id,
        run_dir=artifacts.run_dir,
        artifact_entries=artifacts.entries,
        terminal=terminal,
        repo_slug=repo_slug,
    )
    artifacts.write_json("run_summary.json", summary.model_dump(mode="json"), required=False)


def _write_judgement_artifacts(
    artifacts: ArtifactWriter,
    config: RunConfig,
    run_id: str,
    *,
    terminal: TerminalState,
    trace: TraceWriter | None = None,
    operator: OperatorProgressWriter | None = None,
) -> None:
    if not config.judgement.enabled:
        return
    if not _judgement_eligible_terminal(terminal):
        return
    packet = build_judge_packet(config=config, run_id=run_id, run_dir=artifacts.run_dir)
    artifacts.write_json("judge_packet.json", packet.model_dump(mode="json"), required=False)
    artifacts.write_json(
        "judge_dimension_packets.json",
        build_judge_dimension_packets(packet),
        required=False,
    )
    judgement = judge_run(
        config=config,
        run_id=run_id,
        run_dir=artifacts.run_dir,
        packet=packet,
        progress=_judgement_progress_reporter(trace=trace, operator=operator),
    )
    artifacts.write_json("judgement.json", judgement.model_dump(mode="json"), required=False)
    from contribarena.engine.judge_refresh import mark_transient_judgement_retry_due

    mark_transient_judgement_retry_due(artifacts.run_dir)


def _judgement_eligible_terminal(terminal: TerminalState) -> bool:
    if terminal.layer in {"workspace", "budget"}:
        return False
    if _replacement_due_terminal(terminal):
        return False
    return terminal.status == "completed" or terminal.layer in {"agent", "quality", "pr", "model_runtime"}


def _record_replacement_if_due(
    artifacts: ArtifactWriter,
    config: RunConfig,
    run_id: str,
    terminal: TerminalState,
) -> None:
    if not _replacement_due_terminal(terminal):
        return
    payload = {
        "status": "due",
        "source_run_id": run_id,
        "reason": terminal.reason,
        "layer": terminal.layer,
        "message": terminal.message[:500],
    }
    artifacts.write_json("replacement_state.json", payload, required=False)
    mark_participant_replacement_due(
        config,
        run_id=run_id,
        reason=terminal.reason,
        layer=terminal.layer,
        message=terminal.message,
    )


def _replacement_due_terminal(terminal: TerminalState) -> bool:
    if terminal.layer != "model_runtime":
        return False
    return _transient_runtime_message(terminal.message)


def _transient_runtime_message(message: str) -> bool:
    text = message.lower()
    return any(
        marker in text
        for marker in (
            "apiconnectionerror",
            "connection error",
            "connection reset",
            "socket reset",
            "gnutls",
            "tls connection",
            "timeout",
            "timed out",
            "503",
            "502",
            "504",
            "500",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
        )
    )


def _judgement_progress_reporter(
    *,
    trace: TraceWriter | None,
    operator: OperatorProgressWriter | None,
) -> object | None:
    if trace is None and operator is None:
        return None

    def report(event: str, payload: dict[str, object]) -> None:
        if trace is not None:
            trace.write(RunState.CONTRIBUTION_REVIEWED, event, payload)
        if operator is not None:
            operator.write(
                "judgement",
                _operator_judgement_status(event, payload),
                _operator_judgement_summary(event, payload),
                evidence=["judgement.json", "judge_packet.json", "trace.jsonl"],
                payload=payload,
            )

    return report


def _operator_judgement_status(event: str, payload: dict[str, object]) -> str:
    if event.endswith(".failed"):
        return "needs_attention"
    if event.endswith(".retry"):
        return "retrying" if payload.get("will_retry") else "fallback"
    if event.endswith(".finished"):
        return "ok"
    return "working"


def _operator_judgement_summary(event: str, payload: dict[str, object]) -> str:
    judge = payload.get("judge_id")
    model = payload.get("model")
    dimension = payload.get("dimension")
    if event == "judgement.started":
        return f"started judgement panel with {payload.get('judge_count')} judge(s)"
    if event == "judgement.judge_started":
        return f"started judge {judge} ({model})"
    if event == "judgement.dimension_started":
        return f"judging {dimension} with {judge} ({model}), attempt {payload.get('attempt')}"
    if event == "judgement.dimension_retry":
        return f"retrying judge {judge} ({model}) for {dimension}: {payload.get('error_type')}"
    if event == "judgement.dimension_failed":
        return f"judge {judge} ({model}) used fallback for {dimension}: {payload.get('error_type')}"
    if event == "judgement.dimension_finished":
        return f"finished judge {judge} ({model}) for {dimension}: {payload.get('score')}/5"
    if event == "judgement.judge_finished":
        return f"finished judge {judge} ({model}) score {payload.get('score')}"
    if event == "judgement.finished":
        return f"finished judgement: {payload.get('status')} score {payload.get('judge_score')}"
    return event


def _write_pr_lifecycle_artifacts(
    config: RunConfig,
    artifacts: ArtifactWriter,
    trace: TraceWriter,
    operator: OperatorProgressWriter,
    result: AgentFinalResult,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    quality_gate: QualityGateResult,
    terminal: TerminalState,
    patch: str,
    pr_client: object | None = None,
) -> tuple[PullRequestDraft | None, TerminalState]:
    pr_draft: PullRequestDraft | None = None
    governance_decision: GovernanceDecision | None = None
    live_pr_result: OwnedLivePrExecutionResult | None = None
    live_ci_status: CiStatus | None = None
    live_target: RepoCandidate | None = None
    if quality_gate.status == "pass":
        trace.write(
            RunState.PR_DRY_RUN_STARTED,
            "pr_dry_run.started",
            {"mode": "dry_run"},
        )
        pr_draft = build_pr_draft(config, result, patch)
        artifacts.write_markdown("pr_description.md", render_pr_description(pr_draft))
        trace.write(
            RunState.PR_DRAFT_CREATED,
            "pr_dry_run.draft_created",
            {"title": pr_draft.title, "branch": pr_draft.branch, "labels": pr_draft.labels},
        )
        operator.write(
            "pr_draft",
            "created",
            "created PR draft from accepted contribution",
            evidence=["pr_description.md", "trace.jsonl"],
            payload={"title": pr_draft.title, "branch": pr_draft.branch},
        )
        if config.run.mode in {"owned_live", "external_live"}:
            target = _live_target_candidate(config, result)
            live_target = target
            external_review = (
                _evaluate_external_live_review(config, result, target, patch)
                if config.run.mode == "external_live"
                else None
            )
            if external_review is not None:
                _write_external_live_review_artifacts(artifacts, external_review)
            governance_decision = _evaluate_live_pr(
                config=config,
                trace=trace,
                result=result,
                quality_gate=quality_gate,
                draft=pr_draft,
                patch=patch,
                pr_client=pr_client,
                target=target,
                external_review=external_review,
            )
            artifacts.write_json(
                "governance_decision.json",
                governance_decision.model_dump(mode="json"),
                required=False,
            )
            if not governance_decision.passed:
                operator.write(
                    "governance_gate",
                    "blocked",
                    "governance blocked live PR submission",
                    evidence=["governance_decision.json", "trace.jsonl"],
                    payload=governance_decision.model_dump(mode="json"),
                )
                result.status = "blocked"
                result.blockers.extend(
                    [
                        f"governance blocked live PR: {reason}"
                        for reason in governance_decision.reasons
                    ]
                )
                terminal = TerminalState(
                    status="blocked",
                    reason="governance_blocked",
                    layer="governance",
                    message="; ".join(governance_decision.reasons),
                    agent_status=terminal.agent_status,
                    harness_status="blocked",
                )
            else:
                operator.write(
                    "governance_gate",
                    "pass",
                    "governance allowed live PR submission",
                    evidence=["governance_decision.json", "trace.jsonl"],
                    payload=governance_decision.model_dump(mode="json"),
                )
                live_pr_result = _execute_live_pr(
                    config=config,
                    workspace=workspace,
                    capture=capture,
                    draft=pr_draft,
                    pr_client=pr_client,
                    target=target,
                )
                if live_pr_result.fork_result is not None and not live_pr_result.fork_result.ok:
                    operator.write(
                        "pr_submit",
                        "failed",
                        "fork preparation failed before PR creation",
                        evidence=["live_action_log.jsonl", "trace.jsonl"],
                        payload={"error": truncate_for_operator(live_pr_result.fork_result.error)},
                    )
                    result.status = "failed"
                    result.blockers.append(f"{config.run.mode} fork preparation failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_fork_prepare_failed",
                        layer="pr",
                        message=live_pr_result.fork_result.error,
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                elif (
                    live_pr_result.push_result is None or live_pr_result.push_result.exit_code != 0
                ):
                    operator.write(
                        "pr_submit",
                        "failed",
                        "branch push failed before PR creation",
                        evidence=["live_action_log.jsonl", "trace.jsonl"],
                        payload={
                            "exit_code": (
                                live_pr_result.push_result.exit_code
                                if live_pr_result.push_result is not None
                                else None
                            )
                        },
                    )
                    result.status = "failed"
                    result.blockers.append(f"{config.run.mode} branch push failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_branch_push_failed",
                        layer="pr",
                        message=(
                            live_pr_result.push_result.stderr or live_pr_result.push_result.stdout
                            if live_pr_result.push_result is not None
                            else ""
                        ),
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                elif live_pr_result.pr_result is None or not live_pr_result.pr_result.ok:
                    operator.write(
                        "pr_submit",
                        "failed",
                        "GitHub PR creation failed",
                        evidence=["live_action_log.jsonl", "trace.jsonl"],
                        payload={
                            "error": (
                                truncate_for_operator(live_pr_result.pr_result.error)
                                if live_pr_result.pr_result
                                else ""
                            )
                        },
                    )
                    result.status = "failed"
                    result.blockers.append(f"{config.run.mode} PR creation failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_open_failed",
                        layer="pr",
                        message=(
                            live_pr_result.pr_result.error if live_pr_result.pr_result else ""
                        ),
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                elif _live_label_failure(live_pr_result):
                    live_ci_status = _observe_live_ci(
                        config=config,
                        pr_client=pr_client,
                        pr_result=live_pr_result.pr_result,
                        target=target,
                    )
                    _record_opened_live_pr(
                        config,
                        governance_decision,
                        pr_draft,
                        live_pr_result.pr_result,
                        live_pr_result,
                        live_ci_status,
                        target,
                        artifacts.run_dir,
                    )
                    label_error = _live_label_error(live_pr_result)
                    label_permission_boundary = _live_label_permission_boundary(
                        live_pr_result
                    )
                    operator.write(
                        "pr_labels",
                        "permission_denied" if label_permission_boundary else "failed",
                        (
                            "GitHub PR label application lacked upstream permission"
                            if label_permission_boundary
                            else "GitHub PR label application failed"
                        ),
                        evidence=["live_action_log.jsonl", "trace.jsonl"],
                        payload={
                            "error": truncate_for_operator(label_error),
                            "nonfatal": True,
                            "retryable": not label_permission_boundary,
                        },
                    )
                else:
                    operator.write(
                        "pr_submit",
                        "opened",
                        "opened live pull request",
                        evidence=["live_action_log.jsonl", "pr_description.md"],
                        payload={
                            "number": live_pr_result.pr_result.number,
                            "url": live_pr_result.pr_result.url,
                            "head": live_pr_result.head,
                        },
                    )
                    live_ci_status = _observe_live_ci(
                        config=config,
                        pr_client=pr_client,
                        pr_result=live_pr_result.pr_result,
                        target=target,
                    )
                    _record_opened_live_pr(
                        config,
                        governance_decision,
                        pr_draft,
                        live_pr_result.pr_result,
                        live_pr_result,
                        live_ci_status,
                        target,
                        artifacts.run_dir,
                    )

    ci_status = live_ci_status or build_ci_status(capture, quality_gate)
    artifacts.write_json("ci_status.json", ci_status.model_dump(mode="json"))
    if config.run.mode == "external_live":
        _write_external_lifecycle_artifacts(
            config=config,
            artifacts=artifacts,
            target=live_target,
            live_pr_result=live_pr_result,
            ci_status=ci_status,
        )
    trace.write(RunState.CI_OBSERVED, "ci.observed", ci_status.model_dump(mode="json"))
    operator.write(
        "ci_observe",
        ci_status.status,
        "observed CI/check status",
        evidence=["ci_status.json", "trace.jsonl"],
        payload=ci_status.model_dump(mode="json"),
    )
    artifacts.write_text(
        "live_action_log.jsonl",
        "\n".join(
            json.dumps(entry, ensure_ascii=True)
            for entry in _live_action_log_entries(
                pr_draft,
                governance_decision,
                live_pr_result,
                live_ci_status,
            )
        ),
        kind="jsonl",
        required=False,
    )
    artifacts.write_markdown(
        "postmortem.md",
        render_postmortem(terminal, quality_gate, ci_status, pr_draft),
    )
    trace.write(
        RunState.POSTMORTEM_WRITTEN,
        "postmortem.written",
        {"artifact": "postmortem.md"},
    )
    operator.write(
        "postmortem",
        "written",
        "wrote run postmortem",
        evidence=["postmortem.md", "trace.jsonl"],
    )
    return pr_draft, terminal


def _execute_live_pr(
    *,
    config: RunConfig,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    draft: PullRequestDraft,
    pr_client: object | None,
    target: RepoCandidate,
) -> OwnedLivePrExecutionResult:
    if config.run.mode == "owned_live" and _owned_repo_policy(config) is None:
        raise ValueError("owned_live PR execution requires an owned repository policy")
    policy = _owned_repo_policy(config)
    strategy = "fork" if config.run.mode == "external_live" else policy.pr_submission.strategy
    actor = config.governance.bot_identity.actor or "contribarena-bot"
    token_env = config.governance.bot_identity.token_env
    token = os.environ.get(token_env, "")
    client = pr_client or GitHubPullRequestClient(token_env=token_env)
    fork_result: ForkEnsureResult | None = None
    push_owner = target.owner
    head = draft.branch
    if strategy == "fork":
        fork_owner = (policy.pr_submission.fork_owner if policy is not None else None) or actor
        ensure_fork = getattr(client, "ensure_fork", None)
        if ensure_fork is None:
            fork_result = ForkEnsureResult(
                ok=False,
                error="PR client does not support fork submission",
                source="harness",
            )
            return OwnedLivePrExecutionResult(
                strategy=strategy,
                requested_fork_owner=fork_owner,
                fork_result=fork_result,
            )
        fork_result = ensure_fork(
            owner=target.owner,
            repo=target.repo,
            fork_owner=fork_owner,
        )
        if not fork_result.ok:
            return OwnedLivePrExecutionResult(
                strategy=strategy,
                requested_fork_owner=fork_owner,
                fork_result=fork_result,
            )
        push_owner = fork_result.owner or fork_owner
        head = f"{push_owner}:{draft.branch}"
    command = _owned_live_push_command(
        owner=push_owner,
        repo=target.repo,
        branch=draft.branch,
        title=draft.title,
        actor=actor,
        token_env=token_env,
    )
    push_result = _run_live_push_with_retry(
        workspace=workspace,
        command=command,
        env={token_env: token},
        token=token,
        capture=capture,
    )
    if push_result.command_type != "other":
        push_result = push_result.model_copy(update={"command_type": "other"})
    if push_result.exit_code != 0:
        return OwnedLivePrExecutionResult(
            strategy=strategy,
            head=head,
            requested_fork_owner=fork_owner if strategy == "fork" else "",
            fork_result=fork_result,
            push_result=push_result,
        )

    pr_result = _open_live_pr_with_retry(
        client=client,
        owner=target.owner,
        repo=target.repo,
        title=draft.title,
        body=draft.body,
        head=head,
        base=_live_base_branch(config, target),
    )
    label_ensure_result: LabelOperationResult | None = None
    label_set_result: LabelOperationResult | None = None
    label_skipped_reason = ""
    if (
        pr_result.ok
        and draft.labels
        and config.run.mode == "external_live"
        and not config.governance.external_live.attempt_upstream_labels
    ):
        label_skipped_reason = "upstream label submission skipped by external_live policy"
    elif pr_result.ok and draft.labels:
        ensure_labels = getattr(client, "ensure_labels", None)
        set_pr_labels = getattr(client, "set_pr_labels", None)
        if pr_result.number is None:
            label_ensure_result = LabelOperationResult(
                ok=False,
                labels=draft.labels,
                error="GitHub PR response did not include a PR number for labels",
                source="harness",
            )
        elif ensure_labels is None or set_pr_labels is None:
            label_ensure_result = LabelOperationResult(
                ok=False,
                labels=draft.labels,
                error="PR client does not support GitHub label submission",
                source="harness",
            )
        else:
            label_ensure_result = ensure_labels(
                owner=target.owner,
                repo=target.repo,
                labels=draft.labels,
            )
            if label_ensure_result.ok:
                label_set_result = set_pr_labels(
                    owner=target.owner,
                    repo=target.repo,
                    issue_number=pr_result.number,
                    labels=draft.labels,
                )
    return OwnedLivePrExecutionResult(
        strategy=strategy,
        head=head,
        requested_fork_owner=fork_owner if strategy == "fork" else "",
        fork_result=fork_result,
        push_result=push_result,
        pr_result=pr_result,
        label_ensure_result=label_ensure_result,
        label_set_result=label_set_result,
        label_skipped_reason=label_skipped_reason,
    )


def _run_live_push_with_retry(
    *,
    workspace: DockerWorkspaceManager,
    command: str,
    env: dict[str, str],
    token: str,
    capture: ArtifactCapture,
) -> CommandResult:
    result: CommandResult | None = None
    for attempt in range(1, _LIVE_PR_RETRY_ATTEMPTS + 1):
        result = _redact_live_command_result(workspace.run_with_env(command, env), token)
        if result.command_type != "other":
            result = result.model_copy(update={"command_type": "other"})
        capture.record_command(result)
        if result.exit_code == 0 or not _transient_runtime_message(
            "\n".join((result.stderr, result.stdout))
        ):
            return result
        if attempt < _LIVE_PR_RETRY_ATTEMPTS:
            sleep(_LIVE_PR_RETRY_SLEEP_SECONDS)
    if result is None:
        raise RuntimeError("live PR push did not run")
    return result


def _open_live_pr_with_retry(
    *,
    client: object,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> PullRequestCreateResult:
    open_pr = getattr(client, "open_pr")
    result: PullRequestCreateResult | None = None
    for attempt in range(1, _LIVE_PR_RETRY_ATTEMPTS + 1):
        result = open_pr(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base,
        )
        if result.ok or not _transient_runtime_message(result.error):
            return result
        if attempt < _LIVE_PR_RETRY_ATTEMPTS:
            sleep(_LIVE_PR_RETRY_SLEEP_SECONDS)
    if result is None:
        raise RuntimeError("live PR open did not run")
    return result


def _observe_live_ci(
    *,
    config: RunConfig,
    pr_client: object | None,
    pr_result: PullRequestCreateResult,
    target: RepoCandidate | None = None,
) -> CiStatus:
    candidate = target or (config.discovery.candidates[0] if config.discovery.candidates else None)
    if candidate is None:
        return CiStatus(
            status="not_run",
            source="github",
            checks=[
                CiCheck(
                    name="github_check_runs",
                    status="skipped",
                    details="No configured repository was available for CI observation.",
                )
            ],
        )
    client = pr_client or GitHubPullRequestClient(
        token_env=config.governance.bot_identity.token_env
    )
    get_check_runs = getattr(client, "get_check_runs", None)
    if get_check_runs is None:
        return CiStatus(
            status="not_run",
            source="github",
            checks=[
                CiCheck(
                    name="github_check_runs",
                    status="skipped",
                    details="PR client does not support GitHub check-runs observation.",
                )
            ],
        )
    ref = pr_result.head_sha or ""
    if not ref:
        return CiStatus(
            status="not_run",
            source="github",
            checks=[
                CiCheck(
                    name="github_check_runs",
                    status="skipped",
                    details="GitHub PR response did not include a head SHA for CI observation.",
                )
            ],
        )
    return get_check_runs(owner=candidate.owner, repo=candidate.repo, ref=ref)


def _live_label_failure(result: OwnedLivePrExecutionResult) -> bool:
    return (
        result.label_ensure_result is not None
        and not result.label_ensure_result.ok
        or result.label_set_result is not None
        and not result.label_set_result.ok
    )


def _live_label_error(result: OwnedLivePrExecutionResult) -> str:
    if result.label_ensure_result is not None and not result.label_ensure_result.ok:
        return result.label_ensure_result.error
    if result.label_set_result is not None and not result.label_set_result.ok:
        return result.label_set_result.error
    return ""


def _live_label_permission_boundary(result: OwnedLivePrExecutionResult) -> bool:
    return any(
        _label_operation_status(label_result) == "permission_denied"
        for label_result in (result.label_ensure_result, result.label_set_result)
        if label_result is not None and not label_result.ok
    )


def _owned_live_push_command(
    *,
    owner: str,
    repo: str,
    branch: str,
    title: str,
    actor: str,
    token_env: str,
) -> str:
    remote_url = f'"https://x-access-token:${{{token_env}}}@github.com/{owner}/{repo}.git"'
    remote_name = "contribarena-submit"
    remote_branch_ref = f"refs/heads/{branch}"
    tracking_ref = f"refs/remotes/{remote_name}/{branch}"
    email = f"{actor}@users.noreply.github.com"
    return " && ".join(
        [
            f"git -C repo checkout -B {shlex.quote(branch)}",
            f"git -C repo config user.name {shlex.quote(actor)}",
            f"git -C repo config user.email {shlex.quote(email)}",
            "git -C repo add -A",
            f"git -C repo commit -m {shlex.quote(title)}",
            "git -C repo status --short",
            f"(git -C repo remote remove {remote_name} >/dev/null 2>&1 || true)",
            f"git -C repo remote add {remote_name} {remote_url}",
            "(git -C repo fetch --no-tags "
            f"{remote_name} "
            f"{shlex.quote('+' + remote_branch_ref + ':' + tracking_ref)} "
            ">/dev/null 2>&1 || true)",
            "if git -C repo show-ref --verify --quiet "
            f"{shlex.quote(tracking_ref)}; then "
            "lease_arg="
            f"{shlex.quote('--force-with-lease=' + remote_branch_ref + ':')}"
            "$(git -C repo rev-parse "
            f"{shlex.quote(tracking_ref)}); "
            "else "
            "lease_arg="
            f"{shlex.quote('--force-with-lease=' + remote_branch_ref + ':')}; "
            "fi; "
            f"git -C repo push {remote_name} "
            f"{shlex.quote('HEAD:' + remote_branch_ref)} "
            '"$lease_arg"',
        ]
    )


def _redact_live_command_result(result: CommandResult, token: str) -> CommandResult:
    return result.model_copy(
        update={
            "command": _redact_secret_text(result.command, token),
            "stdout": _redact_secret_text(result.stdout, token),
            "stderr": _redact_secret_text(result.stderr, token),
        }
    )


def _redact_secret_text(value: str, token: str) -> str:
    redacted = re.sub(
        r"https://x-access-token:(?!\$\{)[^@\s]+@",
        "https://x-access-token:***@",
        value,
    )
    if token:
        redacted = redacted.replace(token, "***")
    return redacted


def _record_opened_live_pr(
    config: RunConfig,
    decision: GovernanceDecision,
    draft: PullRequestDraft,
    pr_result: PullRequestCreateResult,
    live_pr_result: OwnedLivePrExecutionResult,
    ci_status: CiStatus | None,
    target: RepoCandidate,
    run_dir: Path,
) -> None:
    state = load_governance_state(config)
    record_governance_attempt(
        state,
        repository=decision.target_repository,
        status="opened",
        decision_id=decision.id,
        action=decision.action,
    )
    if pr_result.number is not None:
        record_governance_pr(
            state,
            repository=decision.target_repository,
            number=pr_result.number,
            url=pr_result.url,
            branch=draft.branch,
            season_id=config.run.season_id or "",
            participant_id=config.run.participant_id or "",
        )
        upsert_lifecycle_record(
            state,
            lifecycle_record_for_opened_pr(
                repository=decision.target_repository,
                number=pr_result.number,
                url=pr_result.url,
                originating_run_dir=str(run_dir),
                branch=draft.branch,
                head=live_pr_result.head,
                base=_live_base_branch(config, target),
                head_sha=pr_result.head_sha,
                ci_status=ci_status,
                poll_interval_seconds=config.governance.external_live.poll_interval_seconds,
                initial_poll_delay_seconds=(
                    config.governance.external_live.initial_poll_delay_seconds
                ),
                season_id=config.run.season_id or "",
                participant_id=config.run.participant_id or "",
            ),
        )
    save_governance_state(config, state)


def _evaluate_live_pr(
    *,
    config: RunConfig,
    trace: TraceWriter,
    result: AgentFinalResult,
    quality_gate: QualityGateResult,
    draft: PullRequestDraft,
    patch: str,
    target: RepoCandidate,
    external_review: ExternalLiveReviewResult | None,
    pr_client: object | None = None,
) -> GovernanceDecision:
    state = load_governance_state(config)
    actor = _authenticated_actor(config, pr_client)
    decision = GovernanceMiddleware().evaluate_pr_open(
        config=config,
        quality_gate=quality_gate,
        target_owner=target.owner,
        target_repo=target.repo,
        base_branch=_live_base_branch(config, target),
        contribution_class=_contribution_class(patch),
        state=state,
        actor=actor,
        external_review_passed=external_review.passed if external_review is not None else True,
        external_review_reasons=external_review.reasons if external_review is not None else None,
    )
    record_governance_attempt(
        state,
        repository=decision.target_repository,
        status="blocked" if not decision.passed else "prepared",
        decision_id=decision.id,
        action=decision.action,
    )
    save_governance_state(config, state)
    trace.write(
        RunState.GOVERNANCE_BLOCKED if not decision.passed else RunState.CONTRIBUTION_REVIEWED,
        "governance.blocked" if not decision.passed else "governance.passed",
        decision.model_dump(mode="json"),
    )
    return decision


def _authenticated_actor(config: RunConfig, pr_client: object | None) -> str:
    if not os.environ.get(config.governance.bot_identity.token_env):
        return ""
    client = pr_client or GitHubPullRequestClient(
        token_env=config.governance.bot_identity.token_env
    )
    authenticated_actor = getattr(client, "authenticated_actor", None)
    if authenticated_actor is None:
        return ""
    return str(authenticated_actor() or "")


def _owned_default_branch(config: RunConfig) -> str:
    policy = _owned_repo_policy(config)
    return policy.default_branch if policy is not None else "main"


def _live_base_branch(config: RunConfig, target: RepoCandidate) -> str:
    if config.run.mode == "owned_live":
        return target.branch or _owned_default_branch(config)
    return target.branch or _metadata_default_branch(target) or "main"


def _live_target_candidate(config: RunConfig, result: AgentFinalResult) -> RepoCandidate:
    if config.run.mode == "owned_live":
        if not config.discovery.candidates:
            raise ValueError("owned_live requires a configured target repository")
        return config.discovery.candidates[0]
    owner = result.repo.owner.strip()
    repo = result.repo.name.strip()
    if not owner or not repo:
        if config.discovery.candidates:
            return config.discovery.candidates[0]
        raise ValueError("external_live requires the agent result to name a target repository")
    for candidate in config.discovery.candidates:
        if candidate.owner == owner and candidate.repo == repo:
            return candidate
    target = RepoCandidate(
        owner=owner,
        repo=repo,
        url=result.repo.url or f"https://github.com/{owner}/{repo}",
    )
    default_branch = result.repo.default_branch.strip() or _metadata_default_branch(
        target
    )
    return RepoCandidate(
        owner=owner,
        repo=repo,
        url=target.url,
        branch=default_branch or "main",
        notes="selected by external_live agent result",
    )


def _tracked_lifecycle_records_for_runtime(
    config: RunConfig,
    repo_slug: str,
) -> list[PrLifecycleRecord]:
    try:
        state = load_governance_state(config)
    except Exception:
        return []
    active_statuses = {"tracking", "needs_response", "stale", "blocked"}
    records = [
        record
        for record in state.lifecycle_records
        if record.lifecycle_status in active_statuses
        and (not config.discovery.candidates or record.repository == repo_slug)
    ]
    return records[:10]


def _metadata_default_branch(target: RepoCandidate) -> str:
    try:
        metadata = repo_get_metadata(target)
    except Exception:
        return ""
    return metadata.default_branch or ""


def _evaluate_external_live_review(
    config: RunConfig,
    result: AgentFinalResult,
    target: RepoCandidate,
    patch: str,
) -> ExternalLiveReviewResult:
    reasons: list[str] = []
    warnings: list[str] = []
    lowered_profile = result.repo_profile.lower()
    lowered_task = " ".join(
        [
            result.selected_task.title,
            result.selected_task.rationale,
            result.selected_task.expected_change,
        ]
    ).lower()
    if target.full_name in {policy.full_name for policy in config.governance.owned_repositories}:
        reasons.append("external target is configured as owned repository")
    if result.selected_task.risk == "high":
        reasons.append("external_live does not allow high-risk selected tasks")
    if not patch.strip():
        reasons.append("external_live requires a submitted patch before PR submission")
    if _looks_like_ai_or_bot_prohibition(lowered_profile):
        reasons.append("repository profile indicates AI or bot contributions may be prohibited")
    try:
        eligibility = repo_check_eligibility(target)
    except Exception as exc:
        reasons.append(f"eligibility check failed: {exc}")
    else:
        if not eligibility.eligible:
            reasons.extend(f"eligibility: {reason}" for reason in eligibility.reasons)
        warnings.extend(f"eligibility: {warning}" for warning in eligibility.warnings)
    if config.governance.external_live.require_maintainer_fit and not result.repo_profile.strip():
        reasons.append("maintainer-fit evidence is missing from repo_profile")
    if config.governance.external_live.require_spam_risk_review and not lowered_task.strip():
        reasons.append("spam-risk rationale is missing from selected task")
    if "duplicate" in lowered_task:
        warnings.append("selected task mentions possible duplicate work")
    diff_paths = _diff_paths(patch)
    if "docs" not in config.governance.contribution_classes.allowed and diff_paths:
        if not _has_code_or_test_path(diff_paths):
            reasons.append("external_live code-only run requires at least one code or test path")
    if len(diff_paths) > 12:
        warnings.append("external patch touches many files; maintainer fit should be reviewed")
    return ExternalLiveReviewResult(
        passed=not reasons,
        reasons=reasons,
        warnings=warnings,
        target=target,
    )


def _write_external_live_review_artifacts(
    artifacts: ArtifactWriter,
    review: ExternalLiveReviewResult,
) -> None:
    artifacts.write_json(
        "eligibility_report.json",
        {
            "target_repository": review.target.full_name,
            "eligible": review.passed,
            "reasons": review.reasons,
            "warnings": review.warnings,
            "checks_performed": [
                "repository_not_owned",
                "task_risk",
                "patch_present",
                "ai_bot_policy_profile_scan",
                "repo_check_eligibility",
                "maintainer_fit_evidence",
                "spam_risk_evidence",
            ],
        },
        required=False,
    )
    artifacts.write_markdown(
        "maintainer_fit.md",
        "\n".join(
            [
                "# Maintainer Fit",
                "",
                f"- Target: {review.target.full_name}",
                f"- Passed: {review.passed}",
                f"- Reasons: {', '.join(review.reasons) if review.reasons else 'none'}",
                f"- Warnings: {', '.join(review.warnings) if review.warnings else 'none'}",
            ]
        ),
        required=False,
    )
    artifacts.write_markdown(
        "spam_risk.md",
        "\n".join(
            [
                "# Spam Risk",
                "",
                f"- Target: {review.target.full_name}",
                "- Decision: " + ("acceptable for governed external PR" if review.passed else "blocked"),
                "- Notes: low-frequency governance, fork-only submission, and quality gates apply.",
            ]
        ),
        required=False,
    )


def _write_external_lifecycle_artifacts(
    *,
    config: RunConfig,
    artifacts: ArtifactWriter,
    target: RepoCandidate | None,
    live_pr_result: OwnedLivePrExecutionResult | None,
    ci_status: CiStatus,
) -> None:
    state = load_governance_state(config)
    repository = target.full_name if target is not None else ""
    records = [
        record.model_dump(mode="json")
        for record in state.lifecycle_records
        if not repository or record.repository == repository
    ]
    artifacts.write_json(
        "pr_lifecycle_state.json",
        {
            "mode": "external_live",
            "poll_interval_seconds": config.governance.external_live.poll_interval_seconds,
            "records": records,
        },
        required=False,
    )
    entries: list[dict[str, object]] = []
    if live_pr_result is not None and live_pr_result.pr_result is not None:
        entries.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": "pr_opened" if live_pr_result.pr_result.ok else "pr_open_failed",
                "repository": repository,
                "number": live_pr_result.pr_result.number,
                "url": live_pr_result.pr_result.url,
                "head": live_pr_result.head,
                "ci_status": ci_status.status,
            }
        )
    artifacts.write_text(
        "pr_review_log.jsonl",
        "\n".join(json.dumps(entry, ensure_ascii=True) for entry in entries),
        kind="jsonl",
        required=False,
    )


def _looks_like_ai_or_bot_prohibition(text: str) -> bool:
    phrases = (
        "no ai generated",
        "ai-generated contributions are not accepted",
        "do not submit ai",
        "no bot contributions",
        "bot contributions are not accepted",
        "automated pull requests are not accepted",
    )
    return any(phrase in text for phrase in phrases)


def _diff_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            paths.append(parts[3].removeprefix("b/"))
    return sorted(set(paths))


def _normalized_diff_path(path: str) -> str:
    lowered = path.lower()
    return lowered.removeprefix("repo/") if lowered.startswith("repo/") else lowered


def _is_docs_only_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if _is_code_or_test_path(lowered):
        return False
    if lowered.startswith(("docs/", "doc/")):
        return True
    if lowered in {"readme.md", "readme.rst", "changelog.md", "changelog.rst"}:
        return True
    return lowered.endswith((".md", ".rst", ".txt"))


def _is_test_only_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if lowered.startswith(("tests/", "test/")):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _is_code_or_test_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if _is_test_only_path(lowered):
        return True
    if lowered.startswith(("src/", "lib/", "pkg/", "packages/", "app/")):
        return True
    return lowered.endswith(
        (
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".c",
            ".cc",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".rb",
            ".php",
            ".swift",
            ".scala",
            ".sh",
        )
    )


def _has_code_or_test_path(paths: list[str]) -> bool:
    return any(_is_code_or_test_path(path) for path in paths)


def _owned_repo_policy(config: RunConfig) -> OwnedRepositoryPolicy | None:
    if not config.discovery.candidates:
        return None
    candidate = config.discovery.candidates[0]
    for policy in config.governance.owned_repositories:
        if policy.owner == candidate.owner and policy.repo == candidate.repo:
            return policy
    return None


def _contribution_class(patch: str) -> str:
    paths = _diff_paths(patch)
    if not paths:
        return "low_risk_code"
    if all(_is_docs_only_path(path) for path in paths):
        return "docs"
    if all(_is_test_only_path(path) for path in paths):
        return "tests"
    return "low_risk_code"


def _live_action_log_entries(
    draft: PullRequestDraft | None,
    governance_decision: GovernanceDecision | None,
    live_pr_result: OwnedLivePrExecutionResult | None = None,
    ci_status: CiStatus | None = None,
) -> list[dict[str, object]]:
    if governance_decision is None:
        return live_action_log_entries(draft)

    base_entry: dict[str, object] = {
        "ts": governance_decision.created_at,
        "mode": "external_live" if _is_external_decision(governance_decision) else "owned_live",
        "target_repository": governance_decision.target_repository,
        "governance_decision_id": governance_decision.id,
        "governance_reasons": governance_decision.reasons,
        "requested_by_agent": True,
        "executed_by_harness": governance_decision.passed,
        "github_actor": governance_decision.actor,
    }
    if draft is not None:
        base_entry.update({"title": draft.title, "branch": draft.branch, "labels": draft.labels})

    if not governance_decision.passed:
        return [
            {
                **base_entry,
                "action": governance_decision.action,
                "status": "blocked",
                "external_write": False,
            }
        ]

    entries: list[dict[str, object]] = []
    if live_pr_result is not None and live_pr_result.fork_result is not None:
        fork_action = (
            "github.create_fork" if live_pr_result.fork_result.created else "github.ensure_fork"
        )
        entries.append(
            {
                **base_entry,
                "action": fork_action,
                "status": "ready" if live_pr_result.fork_result.ok else "failed",
                "external_write": live_pr_result.fork_result.created,
                "requested_fork_owner": live_pr_result.requested_fork_owner,
                "fork_owner": live_pr_result.fork_result.owner,
                "fork_repo": live_pr_result.fork_result.full_name,
                "fork_url": live_pr_result.fork_result.url,
                "fork_error": live_pr_result.fork_result.error,
            }
        )
    if live_pr_result is not None and live_pr_result.push_result is not None:
        push_action = (
            "github.push_fork_branch"
            if live_pr_result.strategy == "fork"
            else "github.push_upstream_branch"
        )
        entries.append(
            {
                **base_entry,
                "action": push_action,
                "status": "pushed" if live_pr_result.push_result.exit_code == 0 else "failed",
                "external_write": True,
                "push_exit_code": live_pr_result.push_result.exit_code,
                "head": live_pr_result.head,
            }
        )
    if live_pr_result is not None and live_pr_result.pr_result is not None:
        entries.append(
            {
                **base_entry,
                "action": "github.open_pr",
                "status": "opened" if live_pr_result.pr_result.ok else "failed",
                "external_write": True,
                "pr_number": live_pr_result.pr_result.number,
                "pr_url": live_pr_result.pr_result.url,
                "pr_error": live_pr_result.pr_result.error,
                "head": live_pr_result.head,
            }
        )
        if live_pr_result.label_ensure_result is not None:
            label_status = _label_operation_status(live_pr_result.label_ensure_result)
            entries.append(
                {
                    **base_entry,
                    "action": "github.ensure_labels",
                    "status": label_status,
                    "external_write": True,
                    "labels": live_pr_result.label_ensure_result.labels,
                    "label_error": live_pr_result.label_ensure_result.error,
                    "label_status_code": live_pr_result.label_ensure_result.status_code,
                    "nonfatal": label_status in {"permission_denied", "failed"},
                    "retryable": label_status == "failed",
                    "source": live_pr_result.label_ensure_result.source,
                    "pr_number": live_pr_result.pr_result.number,
                }
            )
        if live_pr_result.label_skipped_reason:
            entries.append(
                {
                    **base_entry,
                    "action": "github.ensure_labels",
                    "status": "skipped",
                    "external_write": False,
                    "labels": draft.labels if draft is not None else [],
                    "reason": live_pr_result.label_skipped_reason,
                    "nonfatal": True,
                    "retryable": False,
                    "source": "policy",
                    "pr_number": live_pr_result.pr_result.number,
                }
            )
        if live_pr_result.label_set_result is not None:
            label_status = _label_operation_status(live_pr_result.label_set_result)
            entries.append(
                {
                    **base_entry,
                    "action": "github.set_pr_labels",
                    "status": "set" if live_pr_result.label_set_result.ok else label_status,
                    "external_write": True,
                    "labels": live_pr_result.label_set_result.labels,
                    "label_error": live_pr_result.label_set_result.error,
                    "label_status_code": live_pr_result.label_set_result.status_code,
                    "nonfatal": label_status in {"permission_denied", "failed"},
                    "retryable": label_status == "failed",
                    "source": live_pr_result.label_set_result.source,
                    "pr_number": live_pr_result.pr_result.number,
                }
            )
        if live_pr_result.pr_result.ok and ci_status is not None:
            entries.append(
                {
                    **base_entry,
                    "action": "github.observe_checks",
                    "status": ci_status.status,
                    "external_write": False,
                    "head": live_pr_result.head,
                    "ci_source": ci_status.source,
                    "check_count": len(ci_status.checks),
                    "ci_details": [check.details for check in ci_status.checks],
                }
            )
    if entries:
        return entries
    return [
        {
            **base_entry,
            "action": governance_decision.action,
            "status": "prepared",
            "external_write": False,
        }
    ]


def _label_operation_status(result: LabelOperationResult) -> str:
    if result.ok:
        return "ready"
    if result.status_code in {401, 403, 404}:
        return "permission_denied"
    error = result.error.lower()
    if any(
        marker in error
        for marker in _LABEL_PERMISSION_ERROR_MARKERS
    ):
        return "permission_denied"
    return "failed"


def _agent_did_not_converge(capture: ArtifactCapture) -> bool:
    return any(
        item.terminal_after_retries and item.terminal_status == "failed_to_recover"
        for item in capture.aci_results
    )


def _is_external_decision(decision: GovernanceDecision) -> bool:
    return decision.action.startswith("github.external")


_LABEL_PERMISSION_ERROR_MARKERS = (
    "403",
    "404",
    "permission",
    "denied",
    "repo not found",
    "resource not accessible",
    "not found",
)


def _terminal_state_for_result(
    result: AgentFinalResult,
    capture: ArtifactCapture,
    agent_status: str,
    quality_gate: QualityGateResult | None = None,
    *,
    loop_outcome: str = "",
) -> TerminalState:
    if (
        quality_gate is not None
        and quality_gate.status != "pass"
        and agent_status == "completed"
        and result.status != "failed"
    ):
        return TerminalState(
            status=result.status,  # type: ignore[arg-type]
            reason="quality_gate_blocked",
            layer="contribution",
            message="; ".join(quality_gate.blockers),
            agent_status=agent_status,
            harness_status=result.status,
        )
    if result.status != agent_status:
        return TerminalState(
            status=result.status,  # type: ignore[arg-type]
            reason="harness_review_blocked",
            layer="run",
            message="Harness completion enforcement changed the agent final status.",
            agent_status=agent_status,
            harness_status=result.status,
        )
    terminal_recovery = next(
        (
            item
            for item in reversed(capture.aci_results)
            if item.terminal_status or item.terminal_after_retries
        ),
        None,
    )
    if terminal_recovery is not None and result.status != "completed":
        return TerminalState(
            status=result.status,  # type: ignore[arg-type]
            reason=(
                terminal_recovery.terminal_status
                if terminal_recovery.terminal_after_retries and terminal_recovery.terminal_status
                else terminal_recovery.recovery_kind
                or terminal_recovery.terminal_status
                or "tool_terminal"
            ),
            layer="agent",
            message=terminal_recovery.error or terminal_recovery.output or "",
            agent_status=agent_status,
            harness_status=result.status,
        )
    if result.status == "completed":
        reason = "run_completed"
        message = _latest_successful_verification_summary(capture)
    elif result.status == "blocked":
        reason = "agent_blocked" if loop_outcome != "no_continuation_path" else "no_continuation_path"
        message = "; ".join(result.blockers)
    else:
        reason = "agent_failed"
        message = "; ".join(result.blockers)
    return TerminalState(
        status=result.status,
        reason=reason,
        layer="run" if result.status == "completed" else "agent",
        message=message,
        agent_status=agent_status,
        harness_status=result.status,
    )


def _finalize_workspace(
    workspace: DockerWorkspaceManager,
    trace: TraceWriter,
    terminal: TerminalState | None,
    cleanup_policy: str,
) -> None:
    retain = cleanup_policy == "retain_always" or (
        cleanup_policy == "retain_on_failure"
        and terminal is not None
        and terminal.status != "completed"
    )
    if retain:
        trace.write(
            RunState.WORKSPACE_RETAINED,
            "workspace.retained",
            {"container": workspace.container_name, "cleanup_policy": cleanup_policy},
        )
        return
    trace.write(
        RunState.WORKSPACE_STOPPING,
        "workspace.stopping",
        {"container": workspace.container_name, "cleanup_policy": cleanup_policy},
    )
    result = workspace.stop()
    payload = {
        "container": workspace.container_name,
        "exit_code": result.exit_code,
        "stderr": result.stderr,
    }
    if result.exit_code == 0:
        trace.write(RunState.WORKSPACE_STOPPED, "workspace.stopped", payload)
    else:
        trace.write(RunState.WORKSPACE_CLEANUP_FAILED, "workspace.cleanup_failed", payload)


def _terminal_state_for_exception(exc: Exception) -> TerminalState:
    if isinstance(exc, InfrastructureError):
        return TerminalState(
            status="failed",
            reason="workspace_failed",
            layer="workspace",
            message=str(exc),
        )
    if isinstance(exc, BudgetExhausted):
        return TerminalState(
            status="blocked",
            reason="budget_exhausted",
            layer="budget",
            message=str(exc),
        )
    if isinstance(exc, AgentError):
        return TerminalState(
            status="failed",
            reason="agent_error",
            layer="agent",
            message=str(exc),
        )
    return TerminalState(
        status="failed",
        reason="unhandled_exception",
        layer="unknown",
        message=str(exc),
    )


def _enforce_issue_completion(
    config: RunConfig, result: AgentFinalResult, capture: ArtifactCapture
) -> None:
    if config.issue is None:
        return
    if result.status != "completed":
        if not result.blockers and not result.verification_summary.strip():
            result.blockers.append(
                "issue-solving run ended without an explicit blocker or failure reason"
            )
            result.verification_summary = result.blockers[-1]
        return

    blockers: list[str] = []
    if not _has_submitted_patch(capture):
        blockers.append("issue-solving run completed without a submitted patch")
    if not _has_successful_verification_after_last_edit(capture):
        blockers.append(
            "issue-solving run completed without a successful local verification after the last edit"
        )
    if not result.problem_statement_summary.strip():
        blockers.append("issue-solving run completed without a problem statement summary")
    if not result.reproduction_notes.strip():
        blockers.append("issue-solving run completed without reproduction notes")
    if not result.verification_summary.strip():
        blockers.append("issue-solving run completed without a verification summary")
    if blockers:
        result.status = "blocked"
        result.blockers.extend(blockers)
        if not result.verification_summary:
            result.verification_summary = "; ".join(blockers)


def _has_submitted_patch(capture: ArtifactCapture) -> bool:
    patch = _submitted_patch(capture).strip()
    return patch.startswith("diff --git ") or "\ndiff --git " in patch


def _has_successful_verification_after_last_edit(capture: ArtifactCapture) -> bool:
    last_edit_index = -1
    for index, item in enumerate(capture.aci_results):
        if (
            item.tool
            in {
                "aci_apply_patch",
                "aci_replace",
                "aci_insert",
                "aci_create",
                "aci_undo",
            }
            and item.success
        ):
            last_edit_index = index
    accepted_no_command_review = any(
        item.tool == "aci_submit_patch"
        and item.success
        and "no-command verification rationale accepted" in item.review_notes
        for item in capture.aci_results[last_edit_index + 1 :]
    )
    if accepted_no_command_review:
        return True
    return any(
        item.tool == "aci_verify" and item.success
        for item in capture.aci_results[last_edit_index + 1 :]
    )


def _verification_summary_from_capture(capture: ArtifactCapture | None) -> str:
    if capture is None:
        return "No verification summary was returned."
    verification_results = [
        item
        for item in capture.aci_results
        if item.tool in {"aci_suggest_verification", "aci_verify"}
    ]
    if not verification_results:
        return "No verification command was recorded."
    sections = ["# Verification Summary", ""]
    for item in verification_results:
        status = "passed" if item.success else "failed"
        sections.extend([f"## {item.tool}: {status}", "", item.output or item.error or "", ""])
    return "\n".join(sections)


def _latest_successful_verification_summary(capture: ArtifactCapture) -> str:
    latest = next(
        (
            item
            for item in reversed(capture.aci_results)
            if item.tool == "aci_verify" and item.success
        ),
        None,
    )
    if latest is None:
        return ""
    return f"aci_verify passed: {latest.output or latest.error or ''}"


def _command_log(commands: list[object]) -> str:
    sections: list[str] = ["# Test and Command Log", ""]
    for index, command in enumerate(commands, start=1):
        if not hasattr(command, "command"):
            continue
        sections.extend(
            [
                f"## Command {index}",
                "",
                f"```bash\n{command.command}\n```",
                "",
                f"- Exit code: {command.exit_code}",
                f"- Timed out: {command.timed_out}",
                "",
                "### stdout",
                "",
                f"```text\n{_cap(command.stdout)}\n```",
                "",
                "### stderr",
                "",
                f"```text\n{_cap(command.stderr)}\n```",
                "",
            ]
        )
    return "\n".join(sections)


def _quality_report(
    config: RunConfig,
    result: AgentFinalResult,
    capture: ArtifactCapture,
    terminal: TerminalState,
    quality_gate: QualityGateResult,
    pr_draft: PullRequestDraft | None,
) -> str:
    submitted = _has_submitted_patch(capture)
    failed_commands = [command for command in capture.commands if command.exit_code != 0]
    submit_reviews = [
        item for item in capture.aci_results if item.tool == "aci_submit_patch" and item.review_notes
    ]
    latest_review = submit_reviews[-1].review_notes if submit_reviews else ""
    prior_failed_reviews = [item.review_notes for item in submit_reviews[:-1] if not item.success]
    recovery_results = [
        item for item in capture.aci_results if item.recovery_kind or item.terminal_status
    ]
    sections = [
        "# Quality Report",
        "",
        f"- Agent status: {result.status}",
        f"- Terminal status: {terminal.status}",
        f"- Terminal reason: {terminal.reason}",
        f"- Terminal layer: {terminal.layer}",
        f"- Agent did not converge: {str(_agent_did_not_converge(capture)).lower()}",
        f"- Patch submitted: {submitted}",
        f"- Run mode: {config.run.mode}",
        f"- Commands run: {len(capture.commands)}",
        f"- Failed commands: {len(failed_commands)}",
        f"- Patch applications: {len(capture.patches)}",
        f"- Contribution gate: {quality_gate.status}",
        f"- PR draft produced: {pr_draft is not None}",
        "",
        result.workspace_summary.notes or "No additional workspace notes.",
    ]
    if result.problem_statement_summary:
        sections.extend(["", "## Problem Summary", "", result.problem_statement_summary])
    if result.verification_summary:
        sections.extend(["", "## Verification Summary", "", result.verification_summary])
    if latest_review:
        sections.extend(["", "## Submit-Time Review", "", latest_review])
    if prior_failed_reviews:
        sections.extend(["", "## Prior Submit-Time Review Failures", "", *prior_failed_reviews])
    if recovery_results:
        sections.extend(
            [
                "",
                "## Recovery Evidence",
                "",
                *[
                    f"- {item.tool}: {item.recovery_kind or 'n/a'}"
                    + (f" retry={item.retry_count}" if item.retry_count else "")
                    + (f" -> {item.terminal_status}" if item.terminal_status else "")
                    + (" terminal_after_retries" if item.terminal_after_retries else "")
                    for item in recovery_results
                ],
            ]
        )
    sections.extend(render_quality_gate_section(quality_gate))
    if result.blockers:
        sections.extend(["", "## Blockers", "", *[f"- {blocker}" for blocker in result.blockers]])
    return "\n".join(sections)


def _failure_quality_report(terminal: TerminalState, capture: ArtifactCapture) -> str:
    failed_commands = [command for command in capture.commands if command.exit_code != 0]
    sections = [
        "# Quality Report",
        "",
        "- Agent status: unavailable",
        f"- Terminal status: {terminal.status}",
        f"- Terminal reason: {terminal.reason}",
        f"- Terminal layer: {terminal.layer}",
        f"- Agent did not converge: {str(_agent_did_not_converge(capture)).lower()}",
        f"- Commands run: {len(capture.commands)}",
        f"- Failed commands: {len(failed_commands)}",
        f"- Patch applications: {len(capture.patches)}",
        "",
        "Run ended before a structured agent result was available.",
    ]
    if terminal.message:
        sections.extend(["", "## Terminal Message", "", terminal.message])
    return "\n".join(sections)


def _cap(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[output truncated]"
