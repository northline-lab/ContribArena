from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from contribarena.config.schema import DEFAULT_MEMORY_RELATIVE, MemoryConfig, RunConfig, SeasonParticipantConfig
from contribarena.engine.external_lifecycle import (
    lifecycle_record_for_opened_pr,
    lifecycle_record_due,
    mark_lifecycle_observation_failed,
    observe_lifecycle_record,
)
from contribarena.engine.goals import GoalService
from contribarena.engine.judge_refresh import refresh_due_judgements
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
from contribarena.engine.seasons import (
    SeasonStore,
    append_post_completion_outcome,
    load_participant_state,
    mark_participant_replacement_consumed,
    mark_participant_run_started,
    participant_next_wake_at,
    participant_is_due,
    participant_id_for,
    participant_max_concurrent,
    season_is_completed,
)
from contribarena.memory import MemoryService
from contribarena.models import (
    GovernanceAttempt,
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
        season_tick = self._run_season_wake_tick(config, output_dir=output_dir, verbose=verbose)
        if season_tick is not None:
            return season_tick
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

    def _run_season_wake_tick(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> ControllerTickResult | None:
        if config.season is None:
            return None
        store = SeasonStore.from_config(config)
        season = store.load(config.season.id, config.season)
        if season.status != "active":
            return None
        lifecycle_tick = self._run_external_lifecycle_tick(config)
        judgement_tick = _refresh_due_season_judgements(config, season.id)
        if judgement_tick is not None:
            return judgement_tick
        due: list[tuple[str, SeasonParticipantConfig, dict[str, object]]] = []
        for participant in season.participants:
            if "agent" not in participant.role:
                continue
            participant_id = participant_id_for(season, participant)
            participant_state = load_participant_state(store, season.id, participant_id)
            if int(participant_state.get("active_runs") or 0) >= participant_max_concurrent(
                season,
                participant,
            ):
                _record_season_scheduler_attempt(
                    config,
                    participant_id=participant_id,
                    status="skipped",
                    detail="participant_at_concurrency_limit",
                )
                continue
            if not participant_is_due(
                season=season,
                participant=participant,
                state=participant_state,
            ):
                _record_season_scheduler_attempt(
                    config,
                    participant_id=participant_id,
                    status="skipped",
                    detail="wake_interval_not_elapsed",
                )
                continue
            due.append((participant_id, participant, participant_state))
        if due:
            due.sort(
                key=lambda item: (
                    0
                    if isinstance(item[2].get("replacement"), dict)
                    and item[2]["replacement"].get("status") == "due"
                    else 1,
                    participant_next_wake_at(
                        season=season,
                        participant=item[1],
                        participant_id=item[0],
                        state=item[2],
                    ),
                    item[0],
                )
            )
            participant_id, participant, participant_state = due[0]
            run_config = config.model_copy(
                update={
                    "run": config.run.model_copy(
                        update={
                            "model": participant.model,
                            "season_id": season.id,
                            "participant_id": participant_id,
                            "wake_source": "auto",
                        }
                    )
                },
                deep=True,
            )
            repo_slug = _configured_repo_slug(run_config)
            _record_season_scheduler_attempt(
                run_config,
                participant_id=participant_id,
                status="prepared",
                detail="wake_dispatched",
            )
            replacement = participant_state.get("replacement")
            if isinstance(replacement, dict) and replacement.get("status") == "due":
                mark_participant_replacement_consumed(
                    store,
                    season.id,
                    participant_id,
                    replacement_run_id="pending",
                )
            mark_participant_run_started(
                store,
                season.id,
                participant_id,
                repo_slug=repo_slug,
                wake_source="auto",
                increment_active=False,
            )
            run_result = self.launcher.run(run_config, output_dir=output_dir, verbose=verbose)
            if isinstance(replacement, dict) and replacement.get("status") == "due":
                mark_participant_replacement_consumed(
                    store,
                    season.id,
                    participant_id,
                    replacement_run_id=run_result.run_id,
                )
            status = "run_completed" if run_result.status == "completed" else "run_failed"
            return ControllerTickResult(status=status, run_result=run_result)
        if lifecycle_tick is not None:
            return lifecycle_tick
        return ControllerTickResult(status="season_no_eligible_participant")

    def _run_external_lifecycle_tick(
        self,
        config: RunConfig,
    ) -> ControllerTickResult | None:
        if config.season is not None and not config.run.participant_id:
            store = SeasonStore.from_config(config)
            season = store.load(config.season.id, config.season)
            observed: list[ControllerTickResult] = []
            for participant in season.participants:
                if "agent" not in participant.role:
                    continue
                participant_id = participant_id_for(season, participant)
                participant_config = config.model_copy(
                    update={
                        "run": config.run.model_copy(
                            update={
                                "model": participant.model,
                                "season_id": season.id,
                                "participant_id": participant_id,
                                "wake_source": "auto",
                            }
                        )
                    },
                    deep=True,
                )
                tick = self._run_external_lifecycle_tick(participant_config)
                if tick is not None:
                    observed.append(tick)
            if not observed:
                return None
            status = (
                "lifecycle_terminal"
                if any(tick.status == "lifecycle_terminal" for tick in observed)
                else "lifecycle_tracked"
            )
            return ControllerTickResult(status=status)
        state = load_governance_state(config)
        if _backfill_lifecycle_records(config, state):
            save_governance_state(config, state)
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
            _update_originating_run_lifecycle_artifacts(config, observation.record)
            _append_post_completion_outcome_if_needed(config, observation.record)
            _refresh_participant_pr_counts(config, state)
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


def _configured_repo_slug(config: RunConfig) -> str:
    if config.discovery.candidates:
        return config.discovery.candidates[0].full_name
    return config.discovery.query or "github-discovery"


def _record_season_scheduler_attempt(
    config: RunConfig,
    *,
    participant_id: str,
    status: str,
    detail: str,
) -> None:
    state = load_governance_state(config)
    state.attempts.append(
        GovernanceAttempt(
            repository=_configured_repo_slug(config),
            status=status,  # type: ignore[arg-type]
            action="season.auto_wake",
            decision_id=f"participant={participant_id};{detail}",
        )
    )
    save_governance_state(config, state)


def _refresh_due_season_judgements(config: RunConfig, season_id: str) -> ControllerTickResult | None:
    result = refresh_due_judgements(
        config=config,
        input_dir=config.artifacts.output_root,
        season_id=season_id,
        limit=1,
    )
    if result.runs_judged:
        return ControllerTickResult(status="judgement_refreshed")
    if result.skipped:
        return ControllerTickResult(status="judgement_refresh_failed")
    return None


def _active_short_term_goal(config: RunConfig) -> bool:
    if not config.goal.enabled:
        return False
    goal = GoalService(config, run_id="controller").context.short_term
    return goal is not None and goal.status == "active"


def _backfill_lifecycle_records(config: RunConfig, state: GovernanceState) -> bool:
    existing = {(record.repository, record.number) for record in state.lifecycle_records}
    changed = False
    for pr in state.pull_requests:
        key = (pr.repository, pr.number)
        if key in existing:
            continue
        state.lifecycle_records.append(
            lifecycle_record_for_opened_pr(
                repository=pr.repository,
                number=pr.number,
                url=pr.url,
                originating_run_dir=_originating_run_dir_for_pr(config, pr),
                branch=pr.branch,
                head=pr.branch,
                base="main",
                head_sha="",
                ci_status=None,
                poll_interval_seconds=config.governance.external_live.poll_interval_seconds,
                initial_poll_delay_seconds=0,
                season_id=pr.season_id or config.run.season_id or "",
                participant_id=pr.participant_id or config.run.participant_id or "",
            )
        )
        existing.add(key)
        changed = True
    return changed


def _originating_run_dir_for_pr(config: RunConfig, pr: object) -> str:
    repository = str(getattr(pr, "repository", "") or "")
    number = getattr(pr, "number", None)
    season_id = str(getattr(pr, "season_id", "") or config.run.season_id or "")
    participant_id = str(getattr(pr, "participant_id", "") or config.run.participant_id or "")
    try:
        pr_number = int(number)
    except (TypeError, ValueError):
        return ""
    for summary_path in config.artifacts.output_root.glob("*/run_summary.json"):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        run_repo = payload.get("repository", {})
        run_pr = payload.get("pull_request", {})
        run_season = payload.get("season", {})
        run_agent = payload.get("agent", {})
        if not isinstance(run_repo, dict) or not isinstance(run_pr, dict):
            continue
        if str(run_repo.get("full_name") or "") != repository:
            continue
        try:
            run_number = int(run_pr.get("number"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if run_number != pr_number:
            continue
        if isinstance(run_season, dict) and season_id and str(run_season.get("id") or "") != season_id:
            continue
        if (
            isinstance(run_agent, dict)
            and participant_id
            and str(run_agent.get("participant_id") or "") != participant_id
        ):
            continue
        return str(summary_path.parent)
    return ""


def _update_originating_run_lifecycle_artifacts(
    config: RunConfig,
    record: PrLifecycleRecord,
) -> None:
    if season_is_completed(config):
        return
    if not record.originating_run_dir:
        return
    run_dir = Path(record.originating_run_dir)
    if not run_dir.exists():
        return
    _upsert_run_lifecycle_state(config, run_dir, record)
    _update_run_summary_outcome(config, run_dir, record)


def _upsert_run_lifecycle_state(
    config: RunConfig,
    run_dir: Path,
    record: PrLifecycleRecord,
) -> None:
    path = run_dir / "pr_lifecycle_state.json"
    payload: dict[str, object] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except json.JSONDecodeError:
            payload = {}
    records = payload.get("records", [])
    if not isinstance(records, list):
        records = []
    updated = record.model_dump(mode="json")
    replaced = False
    for index, existing in enumerate(records):
        if not isinstance(existing, dict):
            continue
        if existing.get("repository") == record.repository and existing.get("number") == record.number:
            records[index] = updated
            replaced = True
            break
    if not replaced:
        records.append(updated)
    payload.update(
        {
            "mode": config.run.mode,
            "poll_interval_seconds": config.governance.external_live.poll_interval_seconds,
            "records": records,
        }
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _update_run_summary_outcome(
    config: RunConfig,
    run_dir: Path,
    record: PrLifecycleRecord,
) -> None:
    path = run_dir / "run_summary.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        pr = {}
    pr.update(
        {
            "url": record.url or pr.get("url") or "",
            "number": record.number,
            "state": record.state,
        }
    )
    payload["pull_request"] = pr
    outcome = _maintainer_outcome_from_lifecycle(record)
    payload["maintainer_outcome"] = outcome
    judgement = payload.get("judgement")
    if isinstance(judgement, dict):
        adjustment = _real_world_adjustment_for_lifecycle(config, record, outcome)
        judgement["real_world_adjustment"] = adjustment
        try:
            judgement["arena_score"] = max(
                0.0,
                float(judgement.get("judge_score")) + adjustment,  # type: ignore[arg-type]
            )
        except (TypeError, ValueError):
            pass
        payload["judgement"] = judgement
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _maintainer_outcome_from_lifecycle(record: PrLifecycleRecord) -> dict[str, str]:
    observed_at = record.last_observed_at
    signal_kinds = {signal.kind for signal in record.maintainer_signals}
    if signal_kinds & {"opt_out", "anti_ai_or_bot"}:
        return {
            "status": "policy_violation" if "anti_ai_or_bot" in signal_kinds else "opt_out",
            "observed_at": observed_at,
            "source": "maintainer_signal",
        }
    if record.state == "merged" or record.lifecycle_status == "merged":
        return {"status": "merged", "observed_at": observed_at, "source": "github_pr_state"}
    if record.lifecycle_status == "rejected":
        return {"status": "changes_requested", "observed_at": observed_at, "source": "github_review"}
    if record.state == "closed" or record.lifecycle_status == "closed":
        return {"status": "closed", "observed_at": observed_at, "source": "github_pr_state"}
    if record.lifecycle_status == "needs_response":
        return {"status": "reviewed", "observed_at": observed_at, "source": "github_review"}
    if record.lifecycle_status == "stale":
        return {"status": "stale", "observed_at": observed_at, "source": "github_pr_state"}
    return {"status": "pending", "observed_at": observed_at, "source": "github_pr_state"}


def _real_world_adjustment_for_lifecycle(
    config: RunConfig,
    record: PrLifecycleRecord,
    outcome: dict[str, str],
) -> int:
    adjustments = config.judgement.outcome_adjustments
    status = outcome.get("status", "")
    if status in {"spam", "opt_out", "policy_violation"}:
        return adjustments.spam_or_opt_out
    if status == "merged" or record.state == "merged":
        return adjustments.merged
    if status == "changes_requested":
        return adjustments.changes_requested
    if status == "reviewed":
        return adjustments.reviewed
    if status == "closed" or record.state == "closed":
        return adjustments.closed
    if record.state == "open":
        return adjustments.opened
    return 0


def _refresh_participant_pr_counts(config: RunConfig, state: GovernanceState) -> None:
    if not config.run.season_id or not config.run.participant_id:
        return
    participant_state = load_participant_state(
        SeasonStore.from_config(config),
        config.run.season_id,
        config.run.participant_id,
    )
    refs: set[tuple[str, int]] = set()
    merged: set[tuple[str, int]] = set()
    for record in [*state.pull_requests, *state.lifecycle_records]:
        ref = (record.repository, int(record.number))
        refs.add(ref)
        if record.state == "merged" or getattr(record, "lifecycle_status", "") == "merged":
            merged.add(ref)
    participant_state.update(
        {
            "season_id": config.run.season_id,
            "participant_id": config.run.participant_id,
            "prs_opened": len(refs),
            "merged_prs": len(merged),
        }
    )
    state_path = SeasonStore.from_config(config).participant_dir(
        config.run.season_id,
        config.run.participant_id,
    ) / "participant_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(participant_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
            _controller_memory_config(config),
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


def _append_post_completion_outcome_if_needed(
    config: RunConfig,
    record: PrLifecycleRecord,
) -> None:
    if not season_is_completed(config) or not config.run.season_id:
        return
    append_post_completion_outcome(
        SeasonStore.from_config(config),
        config.run.season_id,
        {
            "ts": datetime.now(UTC).isoformat(),
            "season_id": config.run.season_id,
            "participant_id": config.run.participant_id or "",
            "repository": record.repository,
            "number": record.number,
            "url": record.url,
            "state": record.state,
            "lifecycle_status": record.lifecycle_status,
            "source": "controller.lifecycle_observe",
            "originating_run_dir": record.originating_run_dir,
        },
    )


def _lifecycle_memory_run_id(record: object) -> str:
    repo = str(getattr(record, "repository", "repo")).replace("/", "_")
    number = str(getattr(record, "number", "unknown"))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"lifecycle-{repo}-{number}-{timestamp}"


def _controller_memory_config(config: RunConfig) -> MemoryConfig:
    if config.memory.root == DEFAULT_MEMORY_RELATIVE:
        return config.memory.model_copy(
            update={"root": config.artifacts.output_root.parent / "memory"}
        )
    return config.memory


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
