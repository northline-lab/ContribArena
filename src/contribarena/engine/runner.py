from __future__ import annotations

import json
import os
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path

from contribarena.agent import ContributorAgent
from contribarena.agent.prompts import build_goal_prompt
from contribarena.config.schema import OwnedRepositoryPolicy, RunConfig
from contribarena.engine.artifacts import ArtifactWriter
from contribarena.engine.context import ContextBuilder
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
)
from contribarena.engine.runtime_config import apply_output_dir
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.errors import AgentError, BudgetExhausted, InfrastructureError
from contribarena.models import (
    AgentFinalResult,
    CommandResult,
    GovernanceDecision,
    PullRequestDraft,
    QualityGateResult,
    CiStatus,
    RunState,
    TerminalState,
)
from contribarena.providers import ContribArenaModelProvider, TracingModelProvider
from contribarena.trace import TraceWriter
from contribarena.tools.github_pr import (
    ForkEnsureResult,
    GitHubPullRequestClient,
    PullRequestCreateResult,
)
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
        artifacts = ArtifactWriter(
            config.artifacts.output_root,
            run_id,
            repo_slug,
            config.run.model,
        )
        trace = TraceWriter(artifacts.run_dir / "trace.jsonl", run_id)
        trace.write(RunState.RUN_STARTED, "run.started", {"verbose": verbose})
        trace.write(RunState.CONFIG_LOADED, "config.loaded", {"model": config.run.model})

        artifacts.write_json("config.json", config.model_dump(mode="json"))
        workspace = DockerWorkspaceManager(run_id, repo_slug, config.workspace)
        budget = BudgetTracker(config.run.budget)
        capture = ArtifactCapture()
        terminal: TerminalState | None = None
        workspace_started = False

        try:
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
            trace.write(RunState.AGENT_INITIALIZED, "agent.initialized", {"agent": "builtin"})
            prompt = (
                ContextBuilder().build_system_prompt(config) + "\n\n" + build_goal_prompt(config)
            )
            trace.write(
                RunState.AGENT_CONTEXT_LOADED,
                "agent.context_loaded",
                {"prompt_bytes": len(prompt.encode("utf-8"))},
            )
            result = self.agent.run(
                config,
                registry,
                prompt,
                model_provider=TracingModelProvider(
                    ContribArenaModelProvider(config.models),
                    trace,
                ),
            )
            trace.write(
                RunState.AGENT_FINAL_RESULT,
                "agent.final_result",
                {"status": result.status},
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
                    "harness_status": result.status,
                    "quality_gate": quality_gate.status,
                },
            )
            terminal = _terminal_state_for_result(result, capture, agent_status, quality_gate)
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
            pr_draft, terminal = _write_pr_lifecycle_artifacts(
                config=config,
                artifacts=artifacts,
                trace=trace,
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
                _quality_report(result, capture, terminal, quality_gate, pr_draft),
                required=False,
            )
            trace.write(
                RunState.ARTIFACTS_WRITTEN,
                "artifacts.written",
                {"count": len(artifacts.entries) + 1},
            )
            trace.write(
                RunState.RUN_TERMINAL,
                "run.terminal",
                terminal.model_dump(mode="json"),
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
            artifacts.finalize_manifest()
            return RunResult(
                run_id=run_id,
                run_dir=artifacts.run_dir,
                status=terminal.status,
                tool_calls=budget.steps,
                terminal_reason=terminal.reason,
                terminal_layer=terminal.layer,
            )
        except Exception as exc:
            terminal = _terminal_state_for_exception(exc)
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
            trace.write(
                RunState.RUN_TERMINAL,
                "run.terminal",
                terminal.model_dump(mode="json"),
            )
            artifacts.finalize_manifest()
            raise
        finally:
            if workspace_started:
                _finalize_workspace(workspace, trace, terminal, config.workspace.cleanup_policy)

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


def _submitted_patch(capture: ArtifactCapture) -> str:
    for result in reversed(capture.aci_results):
        if result.tool == "aci_submit_patch" and result.success:
            return result.output or ""
    return ""


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


def _write_pr_lifecycle_artifacts(
    config: RunConfig,
    artifacts: ArtifactWriter,
    trace: TraceWriter,
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
        if config.run.mode == "owned_live":
            governance_decision = _evaluate_owned_live_pr(
                config=config,
                trace=trace,
                result=result,
                quality_gate=quality_gate,
                draft=pr_draft,
                pr_client=pr_client,
            )
            artifacts.write_json(
                "governance_decision.json",
                governance_decision.model_dump(mode="json"),
                required=False,
            )
            if not governance_decision.passed:
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
                live_pr_result = _execute_owned_live_pr(
                    config=config,
                    workspace=workspace,
                    capture=capture,
                    draft=pr_draft,
                    pr_client=pr_client,
                )
                if (
                    live_pr_result.fork_result is not None
                    and not live_pr_result.fork_result.ok
                ):
                    result.status = "failed"
                    result.blockers.append("owned_live fork preparation failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_fork_prepare_failed",
                        layer="pr",
                        message=live_pr_result.fork_result.error,
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                elif (
                    live_pr_result.push_result is None
                    or live_pr_result.push_result.exit_code != 0
                ):
                    result.status = "failed"
                    result.blockers.append("owned_live branch push failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_branch_push_failed",
                        layer="pr",
                        message=(
                            live_pr_result.push_result.stderr
                            or live_pr_result.push_result.stdout
                            if live_pr_result.push_result is not None
                            else ""
                        ),
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                elif live_pr_result.pr_result is None or not live_pr_result.pr_result.ok:
                    result.status = "failed"
                    result.blockers.append("owned_live PR creation failed")
                    terminal = TerminalState(
                        status="failed",
                        reason="pr_open_failed",
                        layer="pr",
                        message=(
                            live_pr_result.pr_result.error
                            if live_pr_result.pr_result
                            else ""
                        ),
                        agent_status=terminal.agent_status,
                        harness_status="failed",
                    )
                else:
                    _record_opened_live_pr(
                        config,
                        governance_decision,
                        pr_draft,
                        live_pr_result.pr_result,
                    )
                    live_ci_status = _observe_live_ci(
                        config=config,
                        pr_client=pr_client,
                        pr_result=live_pr_result.pr_result,
                    )

    ci_status = live_ci_status or build_ci_status(capture, quality_gate)
    artifacts.write_json("ci_status.json", ci_status.model_dump(mode="json"))
    trace.write(RunState.CI_OBSERVED, "ci.observed", ci_status.model_dump(mode="json"))
    artifacts.write_text(
        "live_action_log.jsonl",
        "\n".join(
            json.dumps(entry, ensure_ascii=True)
            for entry in _live_action_log_entries(
                pr_draft,
                governance_decision,
                live_pr_result,
                ci_status,
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
    return pr_draft, terminal


def _execute_owned_live_pr(
    *,
    config: RunConfig,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    draft: PullRequestDraft,
    pr_client: object | None,
) -> OwnedLivePrExecutionResult:
    if not config.discovery.candidates:
        raise ValueError("owned_live PR execution requires a configured repository")
    candidate = config.discovery.candidates[0]
    policy = _owned_repo_policy(config)
    if policy is None:
        raise ValueError("owned_live PR execution requires an owned repository policy")
    strategy = policy.pr_submission.strategy
    actor = config.governance.bot_identity.actor or "contribarena-bot"
    token_env = config.governance.bot_identity.token_env
    token = os.environ.get(token_env, "")
    client = pr_client or GitHubPullRequestClient(token_env=token_env)
    fork_result: ForkEnsureResult | None = None
    push_owner = candidate.owner
    head = draft.branch
    if strategy == "fork":
        fork_owner = policy.pr_submission.fork_owner or actor
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
            owner=candidate.owner,
            repo=candidate.repo,
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
        repo=candidate.repo,
        branch=draft.branch,
        title=draft.title,
        actor=actor,
        token_env=token_env,
    )
    push_result = workspace.run_with_env(command, {token_env: token})
    capture.record_command(push_result)
    if push_result.exit_code != 0:
        return OwnedLivePrExecutionResult(
            strategy=strategy,
            head=head,
            requested_fork_owner=fork_owner if strategy == "fork" else "",
            fork_result=fork_result,
            push_result=push_result,
        )

    open_pr = getattr(client, "open_pr")
    pr_result = open_pr(
        owner=candidate.owner,
        repo=candidate.repo,
        title=draft.title,
        body=draft.body,
        head=head,
        base=candidate.branch or _owned_default_branch(config),
    )
    return OwnedLivePrExecutionResult(
        strategy=strategy,
        head=head,
        requested_fork_owner=fork_owner if strategy == "fork" else "",
        fork_result=fork_result,
        push_result=push_result,
        pr_result=pr_result,
    )


def _observe_live_ci(
    *,
    config: RunConfig,
    pr_client: object | None,
    pr_result: PullRequestCreateResult,
) -> CiStatus:
    if not config.discovery.candidates:
        return CiStatus(status="not_run", source="github")
    candidate = config.discovery.candidates[0]
    client = pr_client or GitHubPullRequestClient(token_env=config.governance.bot_identity.token_env)
    get_check_runs = getattr(client, "get_check_runs", None)
    if get_check_runs is None:
        return CiStatus(status="not_run", source="github")
    ref = pr_result.head_sha or ""
    if not ref:
        return CiStatus(status="not_run", source="github")
    return get_check_runs(owner=candidate.owner, repo=candidate.repo, ref=ref)


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
    email = f"{actor}@users.noreply.github.com"
    return " && ".join(
        [
            f"git -C repo checkout -B {shlex.quote(branch)}",
            f"git -C repo config user.name {shlex.quote(actor)}",
            f"git -C repo config user.email {shlex.quote(email)}",
            "git -C repo add -A",
            f"git -C repo commit -m {shlex.quote(title)}",
            "git -C repo status --short",
            "git -C repo push "
            f"{remote_url} "
            f"{shlex.quote('HEAD:refs/heads/' + branch)} --force-with-lease",
        ]
    )


def _record_opened_live_pr(
    config: RunConfig,
    decision: GovernanceDecision,
    draft: PullRequestDraft,
    pr_result: PullRequestCreateResult,
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
        )
    save_governance_state(config, state)


def _evaluate_owned_live_pr(
    *,
    config: RunConfig,
    trace: TraceWriter,
    result: AgentFinalResult,
    quality_gate: QualityGateResult,
    draft: PullRequestDraft,
    pr_client: object | None = None,
) -> GovernanceDecision:
    candidate = config.discovery.candidates[0]
    state = load_governance_state(config)
    actor = _authenticated_actor(config, pr_client)
    decision = GovernanceMiddleware().evaluate_pr_open(
        config=config,
        quality_gate=quality_gate,
        target_owner=candidate.owner,
        target_repo=candidate.repo,
        base_branch=candidate.branch or _owned_default_branch(config),
        contribution_class=_contribution_class(result),
        state=state,
        actor=actor,
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


def _owned_repo_policy(config: RunConfig) -> OwnedRepositoryPolicy | None:
    if not config.discovery.candidates:
        return None
    candidate = config.discovery.candidates[0]
    for policy in config.governance.owned_repositories:
        if policy.owner == candidate.owner and policy.repo == candidate.repo:
            return policy
    return None


def _contribution_class(result: AgentFinalResult) -> str:
    if result.selected_task.risk == "low":
        return "low_risk_code"
    return result.selected_task.risk or "low_risk_code"


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
        "mode": "owned_live",
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
            "github.create_fork"
            if live_pr_result.fork_result.created
            else "github.ensure_fork"
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


def _terminal_state_for_result(
    result: AgentFinalResult,
    capture: ArtifactCapture,
    agent_status: str,
    quality_gate: QualityGateResult | None = None,
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
            reason=terminal_recovery.recovery_kind
            or terminal_recovery.terminal_status
            or "tool_terminal",
            layer="agent",
            message=terminal_recovery.error or terminal_recovery.output or "",
            agent_status=agent_status,
            harness_status=result.status,
        )
    if result.status == "completed":
        reason = "run_completed"
    elif result.status == "blocked":
        reason = "agent_blocked"
    else:
        reason = "agent_failed"
    return TerminalState(
        status=result.status,
        reason=reason,
        layer="run" if result.status == "completed" else "agent",
        message="; ".join(result.blockers),
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
        if item.tool in {"aci_replace", "aci_insert", "aci_create", "aci_undo"} and item.success:
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
    result: AgentFinalResult,
    capture: ArtifactCapture,
    terminal: TerminalState,
    quality_gate: QualityGateResult,
    pr_draft: PullRequestDraft | None,
) -> str:
    submitted = _has_submitted_patch(capture)
    failed_commands = [command for command in capture.commands if command.exit_code != 0]
    review_results = [
        item.review_notes for item in capture.aci_results if item.tool == "aci_submit_patch"
    ]
    recovery_results = [
        item
        for item in capture.aci_results
        if item.recovery_kind or item.terminal_status
    ]
    sections = [
        "# Quality Report",
        "",
        f"- Agent status: {result.status}",
        f"- Terminal status: {terminal.status}",
        f"- Terminal reason: {terminal.reason}",
        f"- Terminal layer: {terminal.layer}",
        f"- Patch submitted in shadow mode: {submitted}",
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
    if review_results:
        sections.extend(["", "## Submit-Time Review", "", *review_results])
    if recovery_results:
        sections.extend(
            [
                "",
                "## Recovery Evidence",
                "",
                *[
                    f"- {item.tool}: {item.recovery_kind or 'n/a'}"
                    + (
                        f" retry={item.retry_count}"
                        if item.retry_count
                        else ""
                    )
                    + (
                        f" -> {item.terminal_status}"
                        if item.terminal_status
                        else ""
                    )
                    + (
                        " terminal_after_retries"
                        if item.terminal_after_retries
                        else ""
                    )
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
