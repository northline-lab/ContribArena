from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from contribarena.agent import AgentInvocationResult
from contribarena.config.schema import RepoCandidate, RunConfig
from contribarena.engine.goals import GoalService
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.operator_events import truncate_for_operator
from contribarena.memory.redact import redact_text
from contribarena.models import (
    AgentFinalResult,
    OpportunitySummary,
    RepoSummary,
    RunState,
    SelectedTask,
    TerminalState,
)
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.trace import TraceWriter


@dataclass(frozen=True)
class AgentLoopProgress:
    repo_present: bool
    repo_inspected: bool
    edited: bool
    verified: bool
    patch_submitted: bool
    live_pr_opened: bool
    goal_status: str
    lifecycle_gaps: list[str]


@dataclass(frozen=True)
class InvocationProgressDelta:
    commands: int = 0
    aci_results: int = 0
    successful_discovery: int = 0
    successful_edits: int = 0
    successful_verifications: int = 0
    successful_submissions: int = 0
    successful_live_actions: int = 0
    goal_events: int = 0
    memory_events: int = 0
    recoveries: int = 0

    @property
    def made_progress(self) -> bool:
        return any(
            [
                self.commands,
                self.successful_discovery,
                self.successful_edits,
                self.successful_verifications,
                self.successful_submissions,
                self.successful_live_actions,
                self.goal_events,
                self.memory_events,
            ]
        )

    def flags(self) -> dict[str, int | bool]:
        return {
            "commands": self.commands,
            "aci_results": self.aci_results,
            "successful_discovery": self.successful_discovery,
            "successful_edits": self.successful_edits,
            "successful_verifications": self.successful_verifications,
            "successful_submissions": self.successful_submissions,
            "successful_live_actions": self.successful_live_actions,
            "goal_events": self.goal_events,
            "memory_events": self.memory_events,
            "recoveries": self.recoveries,
            "made_progress": self.made_progress,
        }


@dataclass
class LoopCounters:
    invocations_used: int = 0
    scout_invocations_used: int = 0
    work_invocations_used: int = 0
    review_invocations_used: int = 0
    consecutive_no_progress: int = 0
    recovery_count: int = 0


@dataclass(frozen=True)
class CaptureCursor:
    commands: int = 0
    aci_results: int = 0
    steps: int = 0
    goal_events: int = 0
    memory_events: int = 0


@dataclass
class AgentLoopReview:
    decision: str
    terminal: TerminalState | None = None
    reason: str = ""
    outcome: str = ""
    sub_reason: str = ""
    progress: AgentLoopProgress | None = None
    delta: InvocationProgressDelta | None = None


@dataclass
class AgentLoopState:
    counters: LoopCounters = field(default_factory=LoopCounters)
    last_invocation_note: str = ""
    recent_tool_summaries: list[str] = field(default_factory=list)
    recovery_warning: str = ""
    legacy_final_result: AgentFinalResult | None = None


LoopOutcome = Literal[
    "legacy_terminal",
    "patch_submitted",
    "no_continuation_path",
    "failed_to_recover_no_progress",
    "failed_to_recover_recovery_exhausted",
    "budget_exhausted",
    "goal_abandon_limit",
    "repo_switch_limit",
    "opportunity_switch_limit",
    "model_runtime",
]


def capture_cursor(capture: ArtifactCapture, goals: GoalService, memory: object | None) -> CaptureCursor:
    return CaptureCursor(
        commands=len(capture.commands),
        aci_results=len(capture.aci_results),
        steps=len(capture.steps),
        goal_events=len(goals.events),
        memory_events=len(getattr(memory, "events", []) or []),
    )


def review_invocation(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    goals: GoalService,
    memory: object | None,
    before: CaptureCursor,
    state: AgentLoopState,
    invocation: AgentInvocationResult,
) -> AgentLoopReview:
    """Review one invocation and mutate AgentLoopState counters/notes."""

    state.counters.invocations_used += 1
    phase = goals.context.current_phase
    if phase == "scout":
        state.counters.scout_invocations_used += 1
    elif phase == "work":
        state.counters.work_invocations_used += 1
    elif phase == "review":
        state.counters.review_invocations_used += 1
    if invocation.legacy_final_result is not None:
        state.legacy_final_result = invocation.legacy_final_result
    if invocation.content.strip():
        state.last_invocation_note = redact_text(
            truncate_for_operator(invocation.content.strip(), 1200),
            max_chars=1200,
        )
    delta = invocation_delta(capture, goals, memory, before)
    state.counters.recovery_count += delta.recoveries
    progress = agent_loop_progress(capture, goals)
    state.recent_tool_summaries = _recent_successful_tool_summaries(capture)

    terminal_recovery = _terminal_recovery(capture)
    if invocation.legacy_final_result is not None:
        # legacy_final_result is only a local-stub / old fake-agent transport
        # channel. New provider invocations use plain content and never rely on it.
        return AgentLoopReview(
            decision="terminal",
            reason="legacy_final_result",
            outcome="legacy_terminal",
            progress=progress,
            delta=delta,
        )
    if invocation.stopped_reason == "provider_error":
        return AgentLoopReview(
            decision="terminal",
            reason="model_runtime",
            outcome="model_runtime",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="failed",
                reason="model_runtime",
                layer="model_runtime",
                message=invocation.error_message or invocation.content,
                agent_status="failed",
                harness_status="failed",
            ),
        )
    if config.run.mode in {"owned_live", "external_live"} and _has_successful_live_pr(capture):
        return AgentLoopReview(
            decision="terminal",
            reason="live_pr_opened",
            outcome="opened_pr",
            progress=progress,
            delta=delta,
        )
    if terminal_recovery is not None and terminal_recovery.terminal_status == "goal_abandon_limit":
        return AgentLoopReview(
            decision="terminal",
            reason="goal_abandon_limit",
            outcome="goal_abandon_limit",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="blocked",
                reason="goal_abandon_limit",
                layer="agent",
                message=terminal_recovery.error or terminal_recovery.output,
                agent_status="blocked",
                harness_status="blocked",
            ),
        )
    if terminal_recovery is not None and terminal_recovery.terminal_status in {
        "repo_switch_limit",
        "opportunity_switch_limit",
    }:
        return AgentLoopReview(
            decision="terminal",
            reason=terminal_recovery.terminal_status,
            outcome=terminal_recovery.terminal_status,
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="blocked",
                reason=terminal_recovery.terminal_status,
                layer="budget",
                message=terminal_recovery.error or terminal_recovery.output,
                agent_status="blocked",
                harness_status="blocked",
            ),
        )
    if state.counters.recovery_count >= config.run.budget.max_recoveries:
        return AgentLoopReview(
            decision="terminal",
            reason="failed_to_recover",
            outcome="failed_to_recover_recovery_exhausted",
            sub_reason="failed_to_recover.recovery_exhausted",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="failed",
                reason="failed_to_recover",
                layer="agent",
                message="model action recovery budget exhausted",
                agent_status="failed",
                harness_status="failed",
            ),
        )

    if state.counters.invocations_used >= config.run.budget.max_invocations:
        return AgentLoopReview(
            decision="terminal",
            reason="budget_exhausted",
            outcome="budget_exhausted",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="blocked",
                reason="budget_exhausted",
                layer="budget",
                message=f"global max_invocations exceeded: {config.run.budget.max_invocations}",
                agent_status="blocked",
                harness_status="blocked",
            ),
        )

    phase_budget = _phase_invocation_budget(config, goals.context.current_phase)
    phase_used = _phase_invocations_used(state, goals.context.current_phase)
    if phase_used >= phase_budget and goals.context.current_phase != "scout":
        reason = (
            "scout_budget_exhausted"
            if goals.context.current_phase == "scout"
            else "budget_exhausted"
        )
        return AgentLoopReview(
            decision="terminal",
            reason=reason,
            outcome="budget_exhausted",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="blocked",
                reason=reason,
                layer="budget",
                message=f"{goals.context.current_phase} max_invocations exceeded: {phase_budget}",
                agent_status="blocked",
                harness_status="blocked",
            ),
        )

    if delta.made_progress:
        state.counters.consecutive_no_progress = 0
        state.recovery_warning = ""
    else:
        state.counters.consecutive_no_progress += 1
        remaining = (
            config.run.budget.max_consecutive_no_progress
            - state.counters.consecutive_no_progress
        )
        if remaining <= 1:
            state.recovery_warning = (
                "No observable progress in the previous invocation. One more invocation "
                "without tool-evidenced progress will end the run as failed_to_recover."
            )
        else:
            state.recovery_warning = (
                f"No observable progress in the previous invocation. "
                f"{remaining} more invocations remain."
            )

    if phase_used >= phase_budget and goals.context.current_phase == "scout":
        goals.record_budget_event(
            event_type="scout_budget_exhausted",
            phase="scout",
            sub_phase=goals.context.current_sub_phase,
            evidence=f"max_scout_invocations reached: {phase_budget}",
        )
        state.recovery_warning = (
            "Scout invocation budget is exhausted. Select an opportunity with "
            "aci_goal_update(scope='contribution', status='active', evidence_refs_json='[...]') "
            "or abandon the current repo/opportunity with evidence."
        )
        if progress.goal_status == "active":
            return AgentLoopReview(
                decision="continue",
                reason="scout_budget_exhausted_select_or_abandon",
                outcome="scout_budget_exhausted",
                progress=progress,
                delta=delta,
            )
        return AgentLoopReview(
            decision="terminal",
            reason="scout_budget_exhausted",
            outcome="budget_exhausted",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="blocked",
                reason="scout_budget_exhausted",
                layer="budget",
                message=f"scout max_invocations exceeded: {phase_budget}",
                agent_status="blocked",
                harness_status="blocked",
            ),
        )

    if config.run.mode in {"owned_live", "external_live"} and _has_successful_submit(capture):
        state.recovery_warning = (
            "Live mode still requires a governed PR opened/existing record. Continue from "
            "the current workspace and follow the live Review guidance."
        )
        return AgentLoopReview(
            decision="continue",
            reason="live_pr_required_after_patch",
            outcome="live_pr_required_after_patch",
            progress=progress,
            delta=delta,
        )
    if _has_successful_submit(capture):
        return AgentLoopReview(
            decision="terminal",
            reason="patch_submitted",
            outcome="patch_submitted",
            progress=progress,
            delta=delta,
        )
    if state.counters.consecutive_no_progress >= config.run.budget.max_consecutive_no_progress:
        return AgentLoopReview(
            decision="terminal",
            reason="failed_to_recover",
            outcome="failed_to_recover_no_progress",
            sub_reason="failed_to_recover.no_progress",
            progress=progress,
            delta=delta,
            terminal=TerminalState(
                status="failed",
                reason="failed_to_recover",
                layer="agent",
                message="consecutive invocations made no observable progress",
                agent_status="failed",
                harness_status="failed",
            ),
        )
    if progress.goal_status == "active" or progress.lifecycle_gaps:
        return AgentLoopReview(
            decision="continue",
            reason="active_goal_or_lifecycle_gaps",
            progress=progress,
            delta=delta,
        )
    return AgentLoopReview(
        decision="terminal",
        reason="no_continuation_path",
        outcome="no_continuation_path",
        progress=progress,
        delta=delta,
    )


def invocation_delta(
    capture: ArtifactCapture,
    goals: GoalService,
    memory: object | None,
    before: CaptureCursor,
) -> InvocationProgressDelta:
    aci_slice = capture.aci_results[before.aci_results :]
    command_slice = capture.commands[before.commands :]
    goal_events = max(0, len(goals.events) - before.goal_events)
    memory_events = max(0, len(getattr(memory, "events", []) or []) - before.memory_events)
    discovery_tools = {
        "repo_search",
        "repo_check_eligibility",
        "repo_get_metadata",
        "repo_get_issues",
        "aci_runtime_get_context",
        "aci_view",
        "aci_search",
        "aci_find_files",
    }
    edit_tools = {"aci_apply_patch", "aci_replace", "aci_insert", "aci_create", "aci_undo"}
    return InvocationProgressDelta(
        commands=sum(1 for command in command_slice if command.exit_code == 0),
        aci_results=len(aci_slice),
        successful_discovery=sum(
            1 for item in aci_slice if item.success and item.tool in discovery_tools
        ),
        successful_edits=sum(1 for item in aci_slice if item.success and item.tool in edit_tools),
        successful_verifications=sum(
            1 for item in aci_slice if item.success and item.tool == "aci_verify"
        ),
        successful_submissions=sum(
            1 for item in aci_slice if item.success and item.tool == "aci_submit_patch"
        ),
        successful_live_actions=sum(
            1 for item in aci_slice if item.success and item.tool.startswith("github_")
        ),
        goal_events=goal_events,
        memory_events=memory_events,
        recoveries=sum(1 for item in aci_slice if item.tool == "aci_recover_invalid_action"),
    )


def _phase_invocation_budget(config: RunConfig, phase: str) -> int:
    if phase == "scout":
        return config.run.budget.scout.max_scout_invocations
    if phase == "work":
        return config.run.budget.work.max_invocations
    if phase == "review":
        return config.run.budget.review.max_invocations
    return config.run.budget.max_invocations


def _phase_invocations_used(state: AgentLoopState, phase: str) -> int:
    if phase == "scout":
        return state.counters.scout_invocations_used
    if phase == "work":
        return state.counters.work_invocations_used
    if phase == "review":
        return state.counters.review_invocations_used
    return state.counters.invocations_used


def agent_loop_progress(capture: ArtifactCapture, goals: GoalService) -> AgentLoopProgress:
    repo_present = any(_is_git_clone_command(command.command) for command in capture.commands)
    repo_inspected = any(
        item.success and item.tool in {"aci_view", "aci_search", "aci_find_files"}
        for item in capture.aci_results
    )
    edited = any(
        item.success
        and item.tool in {"aci_apply_patch", "aci_replace", "aci_insert", "aci_create", "aci_undo"}
        for item in capture.aci_results
    )
    verified = any(item.success and item.tool == "aci_verify" for item in capture.aci_results)
    patch_submitted = _has_successful_submit(capture)
    live_pr_opened = _has_successful_live_pr(capture)
    goal_status = _goal_status(goals)
    gaps: list[str] = []
    if not repo_present:
        gaps.append("repository not cloned")
    if not repo_inspected:
        gaps.append("repository not inspected")
    if not edited:
        gaps.append("no workspace edit captured")
    if edited and not verified:
        gaps.append("edit captured without successful verification")
    if edited and verified and not patch_submitted:
        gaps.append("verified edit without submitted patch")
    if patch_submitted and not live_pr_opened:
        gaps.append("submitted patch without live PR")
    return AgentLoopProgress(
        repo_present=repo_present,
        repo_inspected=repo_inspected,
        edited=edited,
        verified=verified,
        patch_submitted=patch_submitted,
        live_pr_opened=live_pr_opened,
        goal_status=goal_status,
        lifecycle_gaps=gaps,
    )


def render_continuation_context(
    *,
    config: RunConfig,
    goals: GoalService,
    state: AgentLoopState,
    progress: AgentLoopProgress,
    max_bytes: int = 4096,
) -> str:
    goal = goals.context.short_term
    goal_objective = goal.objective if goal is not None else "not set"
    goal_status = goal.status if goal is not None else "none"
    goal_evidence = goal.evidence_summary if goal is not None else "none"
    sections: list[tuple[str, str, bool]] = [
        (
            "Current Goal",
            "\n".join(
                [
                    f"- objective: {goal_objective}",
                    f"- status: {goal_status}",
                    f"- evidence: {goal_evidence}",
                ]
            ),
            True,
        ),
        (
            "Lifecycle Gaps",
            "\n".join(f"- {gap}" for gap in progress.lifecycle_gaps) or "- none",
            True,
        ),
        (
            "Recent Useful Tool Summaries",
            "\n".join(f"- {item}" for item in state.recent_tool_summaries[-3:]) or "- none",
            False,
        ),
        ("Recovery Warning", state.recovery_warning or "none", False),
        ("Last Invocation Note", state.last_invocation_note or "none", False),
    ]
    prefix = (
        "Continue the same ContribArena run. Do not restart from scratch if workspace "
        "evidence already exists. Use tools for the next concrete step; the harness "
        "will derive the final run result from evidence.\n"
    )
    if (
        config.run.mode in {"owned_live", "external_live"}
        and progress.patch_submitted
        and not progress.live_pr_opened
    ):
        prefix += (
            "Runtime fact: the patch is finalized or submitted, but no governed live PR "
            "action has been recorded. Continue from the current workspace; do not restart "
            "Scout or Work. Follow the live Review guidance and use the governed GitHub "
            "submission tools available in this phase.\n"
        )
    return _render_sections_within_budget(prefix, sections, max_bytes=max_bytes)


def derive_agent_result(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    goals: GoalService,
    loop_state: AgentLoopState,
    terminal: TerminalState | None = None,
) -> AgentFinalResult:
    legacy = loop_state.legacy_final_result
    candidate = _result_candidate(config, legacy)
    status = _derived_status(capture, terminal, legacy)
    repo_profile = _repo_profile(config, candidate, legacy, loop_state, terminal)
    selected = _selected_task(config, goals, legacy)
    opportunity = OpportunitySummary(
        title=selected.title,
        rationale=selected.rationale,
        risk=selected.risk,
        source=_opportunity_source(config, legacy),
    )
    notes = _workspace_notes(capture, loop_state, terminal)
    result = AgentFinalResult(
        status=status,
        repo=RepoSummary(
            owner=candidate.owner,
            name=candidate.repo,
            url=str(candidate.url),
            default_branch=candidate.branch or "",
        ),
        repo_profile=repo_profile,
        opportunities=legacy.opportunities if legacy and legacy.opportunities else [opportunity],
        selected_task=selected,
        workspace_summary=WorkspaceSummary(
            commands_run=capture.commands,
            patch_applied=_has_successful_submit(capture),
            notes=notes,
        ),
        blockers=_derived_blockers(capture, terminal, legacy),
        problem_statement_summary=(
            legacy.problem_statement_summary
            if legacy and legacy.problem_statement_summary
            else ""
        ),
        reproduction_notes=(
            legacy.reproduction_notes
            if legacy and legacy.reproduction_notes
            else "" if config.issue is not None else _reproduction_notes(capture)
        ),
        verification_summary=(
            legacy.verification_summary
            if legacy and legacy.verification_summary
            else "" if config.issue is not None else _verification_summary(capture)
        ),
    )
    return result


def trace_invocation_review(
    trace: TraceWriter,
    review: AgentLoopReview,
    counters: LoopCounters,
) -> None:
    trace.write(
        RunState.AGENT_HARNESS_REVIEWED,
        "agent.invocation_reviewed",
        {
            "decision": review.decision,
            "reason": review.reason,
            "outcome": review.outcome,
            "sub_reason": review.sub_reason,
            "progress": review.progress.__dict__ if review.progress else {},
            "delta": review.delta.flags() if review.delta else {},
            "counters": counters.__dict__,
        },
    )


def _render_sections_within_budget(
    prefix: str,
    sections: list[tuple[str, str, bool]],
    max_bytes: int,
) -> str:
    rendered_sections = list(sections)
    while True:
        text = prefix + "\n".join(
            f"\n## {title}\n{body.strip()}" for title, body, _required in rendered_sections
        )
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        removable = next(
            (
                index
                for index in range(len(rendered_sections) - 1, -1, -1)
                if not rendered_sections[index][2]
            ),
            None,
        )
        if removable is None:
            return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        title, body, required = rendered_sections[removable]
        if title == "Recent Useful Tool Summaries" and "\n- " in body:
            lines = body.splitlines()
            rendered_sections[removable] = (title, "\n".join(lines[1:]) or "- none", required)
        else:
            rendered_sections.pop(removable)


def _recent_successful_tool_summaries(capture: ArtifactCapture) -> list[str]:
    summaries: list[str] = []
    for step in capture.steps:
        if step.accepted and step.tool != "aci_recover_invalid_action":
            summaries.append(redact_text(f"{step.tool}: {step.result_summary}", max_chars=180))
    return summaries[-3:]


def _terminal_recovery(capture: ArtifactCapture):
    return next(
        (
            item
            for item in reversed(capture.aci_results)
            if item.terminal_status or item.terminal_after_retries
        ),
        None,
    )


def _has_successful_submit(capture: ArtifactCapture) -> bool:
    if any(item.tool == "aci_submit_patch_finalize" and item.success for item in capture.aci_results):
        return True
    if _draft_submit_requires_review(capture):
        return False
    return any(item.tool == "aci_submit_patch" and item.success for item in capture.aci_results)


def _has_successful_live_pr(capture: ArtifactCapture) -> bool:
    for row in capture.live_action_rows:
        if row.get("action") == "github.open_pr" and row.get("status") in {"opened", "existing"}:
            return True
    for item in capture.aci_results:
        if item.tool != "github_open_pr" or not item.success or not item.output:
            continue
        try:
            payload = json.loads(item.output)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("number"):
            return True
    return False


def _draft_submit_requires_review(capture: ArtifactCapture) -> bool:
    return any(
        item.tool == "aci_goal_update"
        and item.success
        and '"scope":"contribution"' in (item.output or "")
        for item in capture.aci_results
    )


def _result_candidate(config: RunConfig, legacy: AgentFinalResult | None) -> RepoCandidate:
    if legacy is not None:
        return RepoCandidate(
            owner=legacy.repo.owner,
            repo=legacy.repo.name,
            url=legacy.repo.url,
            branch=legacy.repo.default_branch or None,
        )
    if config.discovery.candidates:
        return config.discovery.candidates[0]
    return RepoCandidate(
        owner="unknown",
        repo="unknown",
        url="https://github.com/unknown/unknown",
    )


def _derived_status(
    capture: ArtifactCapture,
    terminal: TerminalState | None,
    legacy: AgentFinalResult | None,
) -> str:
    terminal_reasons_authoritative = {
        "failed_to_recover",
        "budget_exhausted",
        "goal_abandon_limit",
        "model_runtime",
    }
    if terminal is not None and terminal.reason in terminal_reasons_authoritative:
        return terminal.status
    if legacy is not None:
        return legacy.status
    if _has_successful_submit(capture):
        return "completed"
    return "blocked"


def _is_git_clone_command(command: str) -> bool:
    return bool(re.search(r"\bgit(?:\s+-[^\s]+(?:\s+[^\s]+)?)*\s+clone\b", command))


def _repo_profile(
    config: RunConfig,
    candidate: RepoCandidate,
    legacy: AgentFinalResult | None,
    loop_state: AgentLoopState,
    terminal: TerminalState | None,
) -> str:
    if legacy is not None and legacy.repo_profile.strip():
        return legacy.repo_profile
    sections = [
        f"# Repo Profile: {candidate.full_name}",
        "",
        f"- URL: {candidate.url}",
        f"- Branch: {candidate.branch or 'unknown'}",
        f"- Run mode: {config.run.mode}",
    ]
    if terminal is not None:
        sections.append(f"- Terminal: {terminal.status} / {terminal.reason}")
    if loop_state.last_invocation_note:
        sections.extend(["", "## Last Agent Note", "", loop_state.last_invocation_note])
    return "\n".join(sections)


def _selected_task(
    config: RunConfig,
    goals: GoalService,
    legacy: AgentFinalResult | None,
) -> SelectedTask:
    if legacy is not None and legacy.selected_task.title:
        return legacy.selected_task
    goal = goals.context.short_term
    if goal is not None and goal.objective:
        return SelectedTask(
            title=goal.objective,
            rationale=goal.evidence_summary or "Derived from active runtime goal.",
            expected_change="Submit the smallest verified contribution patch.",
            risk="low",
        )
    if config.issue is not None:
        return SelectedTask(
            title=config.issue.title or "Configured issue",
            rationale=config.issue.problem_statement,
            expected_change=config.issue.verification_hint or "Address the configured issue.",
            risk="low",
        )
    return SelectedTask(
        title="Autonomous low-risk contribution",
        rationale="Derived from configured discovery target and captured run evidence.",
        expected_change="Submit a small verified patch.",
        risk="low",
    )


def _goal_status(goals: GoalService) -> str:
    if not goals.enabled:
        return "disabled"
    goal = goals.context.short_term
    return goal.status if goal is not None else "none"


def _opportunity_source(config: RunConfig, legacy: AgentFinalResult | None) -> str:
    if legacy is not None and legacy.opportunities:
        return legacy.opportunities[0].source
    if config.issue is not None and config.issue.source_url:
        return str(config.issue.source_url)
    return "captured_evidence"


def _workspace_notes(
    capture: ArtifactCapture,
    loop_state: AgentLoopState,
    terminal: TerminalState | None,
) -> str:
    payload = {
        "commands": len(capture.commands),
        "aci_results": len(capture.aci_results),
        "patch_submitted": _has_successful_submit(capture),
        "invocations": loop_state.counters.invocations_used,
    }
    if terminal is not None:
        payload["terminal_reason"] = terminal.reason
    return json.dumps(payload, ensure_ascii=True)


def _derived_blockers(
    capture: ArtifactCapture,
    terminal: TerminalState | None,
    legacy: AgentFinalResult | None,
) -> list[str]:
    blockers = list(legacy.blockers) if legacy is not None else []
    if terminal is not None and terminal.status != "completed" and terminal.message:
        blockers.append(terminal.message)
    for item in capture.aci_results:
        if item.terminal_status and item.error:
            blockers.append(item.error)
    return list(dict.fromkeys(blockers))


def _reproduction_notes(capture: ArtifactCapture) -> str:
    inspected = [item for item in capture.aci_results if item.tool in {"aci_view", "aci_search"}]
    if not inspected:
        return ""
    return "Repository context was inspected with " + ", ".join(item.tool for item in inspected[:5])


def _verification_summary(capture: ArtifactCapture) -> str:
    verifications = [item for item in capture.aci_results if item.tool == "aci_verify"]
    if not verifications:
        return ""
    latest = verifications[-1]
    status = "passed" if latest.success else "failed"
    return f"{latest.tool} {status}: {latest.output or latest.error}"
