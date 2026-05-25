from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contribarena.config.schema import GovernanceConfig, OwnedRepositoryPolicy, RunConfig
from contribarena.engine.persistence import atomic_write_json
from contribarena.engine.seasons import participant_governance_state_path
from contribarena.models import (
    GovernanceAttempt,
    GovernanceDecision,
    GovernancePrRef,
    GovernanceState,
    MaintainerSignal,
    PrLifecycleRecord,
    QualityGateResult,
)


class GovernanceMiddleware:
    def evaluate_run_start(
        self,
        *,
        config: RunConfig,
        target_owner: str,
        target_repo: str,
        state: GovernanceState | None = None,
        agent_id: str = "builtin",
        actor: str = "",
    ) -> GovernanceDecision:
        repository = f"{target_owner}/{target_repo}"
        state = state or GovernanceState()
        policy = _owned_repo_policy(config.governance, target_owner, target_repo)
        reasons: list[str] = []
        if config.run.mode not in {"owned_live", "external_live"}:
            reasons.append("run mode is not live")
        if not config.governance.live_enabled:
            reasons.append("governance live_enabled is false")
        if config.run.mode == "owned_live" and policy is None:
            reasons.append(f"target repository is not owned: {repository}")
        if config.governance.kill_switches.global_switch:
            reasons.append("global kill switch is active")
        if repository in config.governance.kill_switches.repositories:
            reasons.append(f"repository kill switch is active: {repository}")
        if target_owner in config.governance.kill_switches.organizations:
            reasons.append(f"organization kill switch is active: {target_owner}")
        if agent_id in config.governance.kill_switches.agents:
            reasons.append(f"agent kill switch is active: {agent_id}")
        reasons.extend(_bot_identity_reasons(config.governance, actor))
        reasons.extend(_rate_limit_reasons(config.governance, state, repository))

        status = "block" if reasons else "pass"
        return GovernanceDecision(
            status=status,
            reasons=reasons,
            target_repository=repository,
            action="controller.start_run",
            external_write=False,
            actor=actor or config.governance.bot_identity.actor,
        )

    def evaluate_pr_open(
        self,
        *,
        config: RunConfig,
        quality_gate: QualityGateResult,
        target_owner: str,
        target_repo: str,
        base_branch: str,
        contribution_class: str,
        state: GovernanceState | None = None,
        agent_id: str = "builtin",
        actor: str = "",
        external_review_passed: bool = True,
        external_review_reasons: list[str] | None = None,
    ) -> GovernanceDecision:
        repository = f"{target_owner}/{target_repo}"
        state = state or GovernanceState()
        policy = _owned_repo_policy(config.governance, target_owner, target_repo)
        reasons: list[str] = []

        if config.run.mode not in {"owned_live", "external_live"}:
            reasons.append("run mode is not live")
        if not config.governance.live_enabled:
            reasons.append("governance live_enabled is false")
        if config.run.mode == "owned_live" and policy is None:
            reasons.append(f"target repository is not owned: {repository}")
        if config.run.mode == "external_live" and policy is not None:
            reasons.append(f"external_live target is configured as owned repository: {repository}")
        if config.governance.kill_switches.global_switch:
            reasons.append("global kill switch is active")
        if repository in config.governance.kill_switches.repositories:
            reasons.append(f"repository kill switch is active: {repository}")
        if target_owner in config.governance.kill_switches.organizations:
            reasons.append(f"organization kill switch is active: {target_owner}")
        if agent_id in config.governance.kill_switches.agents:
            reasons.append(f"agent kill switch is active: {agent_id}")
        if quality_gate.status != "pass":
            reasons.append(f"contribution quality gate is {quality_gate.status}")
        if contribution_class not in config.governance.contribution_classes.allowed:
            reasons.append(f"contribution class is not allowed: {contribution_class}")
        if config.run.mode == "owned_live" and policy is not None and base_branch != policy.default_branch:
            reasons.append(
                f"target branch {base_branch} is not default branch {policy.default_branch}"
            )
        if config.run.mode == "external_live" and not external_review_passed:
            reasons.extend(external_review_reasons or ["external live review did not pass"])
        reasons.extend(_maintainer_signal_reasons(state, repository, target_owner))
        reasons.extend(_bot_identity_reasons(config.governance, actor))
        reasons.extend(_rate_limit_reasons(config.governance, state, repository))

        status = "block" if reasons else "pass"
        return GovernanceDecision(
            status=status,
            reasons=reasons,
            target_repository=repository,
            action=(
                "github.external_open_pr"
                if config.run.mode == "external_live"
                else "github.open_pr"
            ),
            contribution_class=contribution_class,
            external_write=status == "pass",
            actor=actor or config.governance.bot_identity.actor,
        )


def governance_state_path(config: RunConfig) -> Path:
    participant_path = participant_governance_state_path(config)
    if participant_path is not None:
        return participant_path
    return config.governance.state_path or config.artifacts.output_root / "governance_state.json"


def load_governance_state(config: RunConfig) -> GovernanceState:
    path = governance_state_path(config)
    if not path.exists():
        return GovernanceState()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return GovernanceState.model_validate(loaded)


def save_governance_state(config: RunConfig, state: GovernanceState) -> Path:
    path = governance_state_path(config)
    atomic_write_json(path, state.model_dump(mode="json"), ensure_ascii=True)
    return path


def record_governance_attempt(
    state: GovernanceState,
    *,
    repository: str,
    status: str,
    decision_id: str = "",
    action: str = "github.open_pr",
) -> None:
    state.attempts.append(
        GovernanceAttempt(
            repository=repository,
            action=action,
            status=status,  # type: ignore[arg-type]
            decision_id=decision_id,
        )
    )


def record_governance_pr(
    state: GovernanceState,
    *,
    repository: str,
    number: int,
    url: str = "",
    branch: str = "",
    season_id: str = "",
    participant_id: str = "",
) -> None:
    state.pull_requests.append(
        GovernancePrRef(
            season_id=season_id,
            participant_id=participant_id,
            repository=repository,
            number=number,
            url=url,
            branch=branch,
        )
    )


def update_governance_pr_state(
    state: GovernanceState,
    *,
    repository: str,
    number: int,
    pr_state: str,
) -> None:
    normalized = "merged" if pr_state == "merged" else "closed" if pr_state == "closed" else "open"
    for index, pr in enumerate(state.pull_requests):
        if pr.repository == repository and pr.number == number:
            state.pull_requests[index] = pr.model_copy(update={"state": normalized})
            return


def upsert_lifecycle_record(state: GovernanceState, record: PrLifecycleRecord) -> None:
    for index, existing in enumerate(state.lifecycle_records):
        if existing.repository == record.repository and existing.number == record.number:
            if not record.season_id and existing.season_id:
                record = record.model_copy(update={"season_id": existing.season_id})
            if not record.participant_id and existing.participant_id:
                record = record.model_copy(update={"participant_id": existing.participant_id})
            state.lifecycle_records[index] = record
            return
    state.lifecycle_records.append(record)


def record_maintainer_signal(
    state: GovernanceState,
    signal: MaintainerSignal,
) -> None:
    state.maintainer_signals.append(signal)
    for index, record in enumerate(state.lifecycle_records):
        if record.repository == signal.repository:
            state.lifecycle_records[index] = record.model_copy(
                update={"maintainer_signals": [*record.maintainer_signals, signal]}
            )


def _owned_repo_policy(
    governance: GovernanceConfig,
    owner: str,
    repo: str,
) -> OwnedRepositoryPolicy | None:
    for policy in governance.owned_repositories:
        if policy.owner == owner and policy.repo == repo:
            return policy
    return None


def _bot_identity_reasons(governance: GovernanceConfig, actor: str) -> list[str]:
    reasons: list[str] = []
    expected_actor = governance.bot_identity.actor.strip()
    if not expected_actor:
        reasons.append("bot identity actor is missing")
    if not os.environ.get(governance.bot_identity.token_env):
        reasons.append(f"bot token env is missing: {governance.bot_identity.token_env}")
    if actor and expected_actor and actor != expected_actor:
        reasons.append(f"authenticated actor {actor} does not match expected {expected_actor}")
    return reasons


def _rate_limit_reasons(
    governance: GovernanceConfig,
    state: GovernanceState,
    repository: str,
) -> list[str]:
    limits = governance.rate_limits
    reasons: list[str] = []
    organization = repository.split("/", 1)[0]
    open_prs = [
        pr for pr in state.pull_requests if pr.repository == repository and pr.state == "open"
    ]
    if len(open_prs) >= limits.max_open_prs_per_repo:
        reasons.append(
            f"open PR limit reached for {repository}: {len(open_prs)}/{limits.max_open_prs_per_repo}"
        )
    open_org_prs = [
        pr
        for pr in state.pull_requests
        if pr.repository.startswith(f"{organization}/") and pr.state == "open"
    ]
    if len(open_org_prs) >= limits.max_open_prs_per_org:
        reasons.append(
            "open PR limit reached for organization "
            f"{organization}: {len(open_org_prs)}/{limits.max_open_prs_per_org}"
        )
    open_global_prs = [pr for pr in state.pull_requests if pr.state == "open"]
    if len(open_global_prs) >= limits.max_open_prs_global:
        reasons.append(
            f"global open PR limit reached: {len(open_global_prs)}/{limits.max_open_prs_global}"
        )

    now = datetime.now(UTC)
    opened_today = [
        attempt
        for attempt in state.attempts
        if attempt.repository == repository
        and attempt.status == "opened"
        and _parse_timestamp(attempt.created_at).date() == now.date()
    ]
    if len(opened_today) >= limits.max_prs_per_repo_per_day:
        reasons.append(
            "daily PR limit reached for "
            f"{repository}: {len(opened_today)}/{limits.max_prs_per_repo_per_day}"
        )
    opened_org_today = [
        attempt
        for attempt in state.attempts
        if attempt.repository.startswith(f"{organization}/")
        and attempt.status == "opened"
        and _parse_timestamp(attempt.created_at).date() == now.date()
    ]
    if len(opened_org_today) >= limits.max_prs_per_org_per_day:
        reasons.append(
            "daily PR limit reached for organization "
            f"{organization}: {len(opened_org_today)}/{limits.max_prs_per_org_per_day}"
        )
    opened_global_today = [
        attempt
        for attempt in state.attempts
        if attempt.status == "opened"
        and _parse_timestamp(attempt.created_at).date() == now.date()
    ]
    if len(opened_global_today) >= limits.max_prs_global_per_day:
        reasons.append(
            f"global daily PR limit reached: {len(opened_global_today)}/{limits.max_prs_global_per_day}"
        )

    opened_attempts = [
        attempt
        for attempt in state.attempts
        if attempt.repository == repository and attempt.status == "opened"
    ]
    if opened_attempts and limits.min_minutes_between_prs_per_repo > 0:
        latest = max(_parse_timestamp(attempt.created_at) for attempt in opened_attempts)
        cooldown_until = latest + timedelta(minutes=limits.min_minutes_between_prs_per_repo)
        if now < cooldown_until:
            reasons.append(
                "PR cooldown active for "
                f"{repository} until {cooldown_until.isoformat()}"
            )
    org_attempts = [
        attempt
        for attempt in state.attempts
        if attempt.repository.startswith(f"{organization}/") and attempt.status == "opened"
    ]
    if org_attempts and limits.min_minutes_between_prs_per_org > 0:
        latest = max(_parse_timestamp(attempt.created_at) for attempt in org_attempts)
        cooldown_until = latest + timedelta(minutes=limits.min_minutes_between_prs_per_org)
        if now < cooldown_until:
            reasons.append(
                "organization PR cooldown active for "
                f"{organization} until {cooldown_until.isoformat()}"
            )
    global_attempts = [
        attempt for attempt in state.attempts if attempt.status == "opened"
    ]
    if global_attempts and limits.min_minutes_between_prs_global > 0:
        latest = max(_parse_timestamp(attempt.created_at) for attempt in global_attempts)
        cooldown_until = latest + timedelta(minutes=limits.min_minutes_between_prs_global)
        if now < cooldown_until:
            reasons.append(f"global PR cooldown active until {cooldown_until.isoformat()}")
    return reasons


def _maintainer_signal_reasons(
    state: GovernanceState,
    repository: str,
    organization: str,
) -> list[str]:
    reasons: list[str] = []
    for signal in state.maintainer_signals:
        applies = signal.repository == repository or (
            signal.organization == organization and signal.severity == "high"
        )
        if not applies:
            continue
        if signal.kind in {"opt_out", "anti_ai_or_bot"} and signal.severity == "high":
            reasons.append(
                "high-severity maintainer signal requires operator review: "
                f"{signal.kind} for {signal.repository or signal.organization}"
            )
    return reasons


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
