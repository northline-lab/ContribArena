from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from contribarena.config.schema import RunConfig
from contribarena.engine.external_lifecycle import (
    lifecycle_record_due,
    mark_lifecycle_observation_failed,
    observe_lifecycle_record,
)
from contribarena.engine.goals import GoalService
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_attempt,
    save_governance_state,
    update_governance_pr_state,
    upsert_lifecycle_record,
)
from contribarena.engine.runtime_config import apply_output_dir
from contribarena.engine.runner import RunResult, Runner
from contribarena.memory import MemoryService
from contribarena.models import (
    GovernanceDecision,
    GovernanceState,
    MaintainerSignal,
    PrLifecycleRecord,
)
from contribarena.tools.github_pr import GitHubPullRequestClient


class RunLauncher(Protocol):
    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> RunResult:
        ...


@dataclass
class ControllerTickResult:
    status: str
    decision: GovernanceDecision | None = None
    run_result: RunResult | None = None


@dataclass
class ControllerResult:
    ticks: list[ControllerTickResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.ticks:
            return "skipped"
        if any(tick.status == "run_failed" for tick in self.ticks):
            return "failed"
        if any(tick.status == "run_completed" for tick in self.ticks):
            return "completed"
        return self.ticks[-1].status


class LocalController:
    def __init__(
        self,
        launcher: RunLauncher | None = None,
        governance: GovernanceMiddleware | None = None,
        pr_client: object | None = None,
    ) -> None:
        self.launcher = launcher or Runner()
        self.governance = governance or GovernanceMiddleware()
        self.pr_client = pr_client

    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> ControllerResult:
        config = apply_output_dir(config, output_dir)
        if not config.controller.enabled:
            return ControllerResult(ticks=[ControllerTickResult(status="disabled")])
        result = ControllerResult()
        max_ticks = config.controller.max_ticks or 1
        for tick in range(max_ticks):
            tick_result = self.run_once(config, output_dir=output_dir, verbose=verbose)
            result.ticks.append(tick_result)
            if tick_result.status in {"blocked", "run_failed"}:
                break
            if tick + 1 < max_ticks:
                time.sleep(config.controller.interval_seconds)
        return result

    def run_once(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> ControllerTickResult:
        if config.run.mode == "external_live":
            lifecycle_tick = self._run_external_lifecycle_tick(config)
            if lifecycle_tick is not None and not _active_short_term_goal(config):
                return lifecycle_tick
            state = load_governance_state(config)
            decision = self.governance.evaluate_run_start(
                config=config,
                target_owner="external-live",
                target_repo="discovery",
                state=state,
                actor=_authenticated_actor(config, self.pr_client),
            )
            if not decision.passed:
                record_governance_attempt(
                    state,
                    repository=decision.target_repository,
                    status="skipped",
                    decision_id=decision.id,
                    action=decision.action,
                )
                save_governance_state(config, state)
                return ControllerTickResult(status="blocked", decision=decision)
            run_result = self.launcher.run(config, output_dir=output_dir, verbose=verbose)
            status = "run_completed" if run_result.status == "completed" else "run_failed"
            return ControllerTickResult(status=status, decision=decision, run_result=run_result)

        if not config.discovery.candidates:
            raise ValueError("controller requires a configured target repository")
        candidate = config.discovery.candidates[0]
        state = load_governance_state(config)
        decision = self.governance.evaluate_run_start(
            config=config,
            target_owner=candidate.owner,
            target_repo=candidate.repo,
            state=state,
        )
        if not decision.passed:
            record_governance_attempt(
                state,
                repository=decision.target_repository,
                status="skipped",
                decision_id=decision.id,
                action=decision.action,
            )
            save_governance_state(config, state)
            return ControllerTickResult(status="blocked", decision=decision)

        run_result = self.launcher.run(config, output_dir=output_dir, verbose=verbose)
        state = load_governance_state(config)
        record_governance_attempt(
            state,
            repository=decision.target_repository,
            status="prepared",
            decision_id=decision.id,
            action=decision.action,
        )
        save_governance_state(config, state)
        status = "run_completed" if run_result.status == "completed" else "run_failed"
        return ControllerTickResult(
            status=status,
            decision=decision,
            run_result=run_result,
        )

    def _run_external_lifecycle_tick(
        self,
        config: RunConfig,
    ) -> ControllerTickResult | None:
        state = load_governance_state(config)
        due_records = [
            record for record in state.lifecycle_records if lifecycle_record_due(record)
        ]
        if not due_records:
            return None
        client = self.pr_client or GitHubPullRequestClient(
            token_env=config.governance.bot_identity.token_env
        )
        terminal_seen = False
        for record in due_records:
            owner, repo = record.repository.split("/", 1)
            get_pr = getattr(client, "get_pr", None)
            get_check_runs = getattr(client, "get_check_runs", None)
            list_reviews = getattr(client, "list_reviews", None)
            try:
                pr_status = get_pr(owner=owner, repo=repo, number=record.number) if get_pr else None
                ref = (
                    pr_status.head_sha
                    if pr_status is not None and getattr(pr_status, "head_sha", "")
                    else record.head_sha
                )
                ci_status = (
                    get_check_runs(owner=owner, repo=repo, ref=ref)
                    if get_check_runs and ref
                    else None
                )
                reviews = (
                    list_reviews(owner=owner, repo=repo, number=record.number)
                    if list_reviews
                    else []
                )
            except Exception as exc:
                safe_error = _safe_log_error(str(exc))
                updated = mark_lifecycle_observation_failed(
                    record=record,
                    error=safe_error,
                    poll_interval_seconds=(
                        config.governance.external_live.poll_interval_seconds
                    ),
                )
                upsert_lifecycle_record(state, updated)
                _append_external_lifecycle_log(
                    config,
                    updated.originating_run_dir,
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "event": "lifecycle_observe_failed",
                        "repository": updated.repository,
                        "number": updated.number,
                        "url": updated.url,
                        "state": updated.state,
                        "lifecycle_status": updated.lifecycle_status,
                        "retry_count": updated.lifecycle_retry_count,
                        "next_poll_at": updated.next_poll_at,
                        "error": safe_error,
                    },
                )
                continue
            observation = observe_lifecycle_record(
                record=record,
                pr_status=pr_status,
                ci_status=ci_status,
                reviews=reviews,
                poll_interval_seconds=config.governance.external_live.poll_interval_seconds,
            )
            _append_maintainer_signals(state, observation.record.maintainer_signals)
            upsert_lifecycle_record(state, observation.record)
            update_governance_pr_state(
                state,
                repository=observation.record.repository,
                number=observation.record.number,
                pr_state=observation.record.state,
            )
            _append_external_lifecycle_log(
                config,
                observation.record.originating_run_dir,
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "lifecycle_observed",
                    "repository": observation.record.repository,
                    "number": observation.record.number,
                    "url": observation.record.url,
                    "state": observation.record.state,
                    "lifecycle_status": observation.record.lifecycle_status,
                    "ci_status": observation.record.ci_status,
                    "review_count": len(reviews),
                    "next_poll_at": observation.record.next_poll_at,
                },
            )
            _record_lifecycle_memory_artifacts(
                config,
                observation.record,
                review_count=len(reviews),
            )
            terminal_seen = terminal_seen or observation.action == "terminal"
        save_governance_state(config, state)
        return ControllerTickResult(
            status="lifecycle_terminal" if terminal_seen else "lifecycle_tracked"
        )


def _authenticated_actor(config: RunConfig, pr_client: object | None) -> str:
    client = pr_client or GitHubPullRequestClient(token_env=config.governance.bot_identity.token_env)
    authenticated_actor = getattr(client, "authenticated_actor", None)
    if authenticated_actor is None:
        return ""
    return str(authenticated_actor() or "")


def _active_short_term_goal(config: RunConfig) -> bool:
    if not config.goal.enabled:
        return False
    goal = GoalService(config, run_id="controller").context.short_term
    return goal is not None and goal.status == "active"


def _append_external_lifecycle_log(
    config: RunConfig,
    originating_run_dir: str,
    entry: dict[str, object],
) -> None:
    path = (
        Path(originating_run_dir) / "pr_review_log.jsonl"
        if originating_run_dir
        else config.artifacts.output_root / "pr_review_log.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Phase 0 controller ticks are single-process; durable concurrent writers belong
    # with the later SQLite or hosted state backend.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _record_lifecycle_memory_artifacts(
    config: RunConfig,
    record: PrLifecycleRecord,
    *,
    review_count: int,
) -> None:
    if not config.memory.enabled:
        return
    run_dir = (
        Path(getattr(record, "originating_run_dir", ""))
        if getattr(record, "originating_run_dir", "")
        else config.artifacts.output_root
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    memory: MemoryService | None = None
    try:
        memory = MemoryService(
            config.memory,
            run_id=_lifecycle_memory_run_id(record),
            repo_full_name=getattr(record, "repository", ""),
        )
        memory.record_lifecycle_observation(
            record,
            review_count=review_count,
            source_ref=f"pr#{getattr(record, 'number', '')} lifecycle tick",
        )
        events = memory.events_text()
        if events:
            with (run_dir / "memory_events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(events)
    except Exception as exc:
        _append_external_lifecycle_log(
            config,
            record.originating_run_dir,
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": "lifecycle_memory_record_failed",
                "repository": record.repository,
                "number": record.number,
                "error": _safe_log_error(str(exc)),
            },
        )
    finally:
        if memory is not None:
            close = getattr(memory, "_close_graphiti", None)
        else:
            close = None
        if close is not None:
            close()


def _lifecycle_memory_run_id(record: object) -> str:
    repo = str(getattr(record, "repository", "repo")).replace("/", "_")
    number = str(getattr(record, "number", "unknown"))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"lifecycle-{repo}-{number}-{timestamp}"


def _safe_log_error(message: str) -> str:
    redacted = re.sub(r"https://x-access-token:[^@\s]+@", "https://x-access-token:***@", message)
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_[REDACTED]", redacted)
    redacted = re.sub(r"ghp_[A-Za-z0-9_]+", "ghp_[REDACTED]", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[REDACTED]", redacted)
    return redacted[:500]


def _append_maintainer_signals(
    state: GovernanceState,
    signals: list[MaintainerSignal],
) -> None:
    existing = {
        (
            signal.repository,
            signal.kind,
            signal.source,
        )
        for signal in state.maintainer_signals
    }
    for signal in signals:
        key = (
            signal.repository,
            signal.kind,
            signal.source,
        )
        if key in existing:
            continue
        state.maintainer_signals.append(signal)
        existing.add(key)
