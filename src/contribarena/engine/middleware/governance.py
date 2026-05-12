from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contribarena.config.schema import GovernanceConfig, OwnedRepositoryPolicy, RunConfig
from contribarena.models import (
    GovernanceAttempt,
    GovernanceDecision,
    GovernancePrRef,
    GovernanceState,
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
        if config.run.mode != "owned_live":
            reasons.append("run mode is not owned_live")
        if not config.governance.live_enabled:
            reasons.append("governance live_enabled is false")
        if policy is None:
            reasons.append(f"target repository is not owned: {repository}")
        if config.governance.kill_switches.global_switch:
            reasons.append("global kill switch is active")
        if repository in config.governance.kill_switches.repositories:
            reasons.append(f"repository kill switch is active: {repository}")
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
    ) -> GovernanceDecision:
        repository = f"{target_owner}/{target_repo}"
        state = state or GovernanceState()
        policy = _owned_repo_policy(config.governance, target_owner, target_repo)
        reasons: list[str] = []

        if config.run.mode != "owned_live":
            reasons.append("run mode is not owned_live")
        if not config.governance.live_enabled:
            reasons.append("governance live_enabled is false")
        if policy is None:
            reasons.append(f"target repository is not owned: {repository}")
        if config.governance.kill_switches.global_switch:
            reasons.append("global kill switch is active")
        if repository in config.governance.kill_switches.repositories:
            reasons.append(f"repository kill switch is active: {repository}")
        if agent_id in config.governance.kill_switches.agents:
            reasons.append(f"agent kill switch is active: {agent_id}")
        if quality_gate.status != "pass":
            reasons.append(f"contribution quality gate is {quality_gate.status}")
        if contribution_class not in config.governance.contribution_classes.allowed:
            reasons.append(f"contribution class is not allowed: {contribution_class}")
        if policy is not None and base_branch != policy.default_branch:
            reasons.append(
                f"target branch {base_branch} is not default branch {policy.default_branch}"
            )
        reasons.extend(_bot_identity_reasons(config.governance, actor))
        reasons.extend(_rate_limit_reasons(config.governance, state, repository))

        status = "block" if reasons else "pass"
        return GovernanceDecision(
            status=status,
            reasons=reasons,
            target_repository=repository,
            action="github.open_pr",
            contribution_class=contribution_class,
            external_write=status == "pass",
            actor=actor or config.governance.bot_identity.actor,
        )


def governance_state_path(config: RunConfig) -> Path:
    return config.governance.state_path or config.artifacts.output_root / "governance_state.json"


def load_governance_state(config: RunConfig) -> GovernanceState:
    path = governance_state_path(config)
    if not path.exists():
        return GovernanceState()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return GovernanceState.model_validate(loaded)


def save_governance_state(config: RunConfig, state: GovernanceState) -> Path:
    path = governance_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
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
) -> None:
    state.pull_requests.append(
        GovernancePrRef(repository=repository, number=number, url=url, branch=branch)
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
    open_prs = [
        pr for pr in state.pull_requests if pr.repository == repository and pr.state == "open"
    ]
    if len(open_prs) >= limits.max_open_prs_per_repo:
        reasons.append(
            f"open PR limit reached for {repository}: {len(open_prs)}/{limits.max_open_prs_per_repo}"
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
    return reasons


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
