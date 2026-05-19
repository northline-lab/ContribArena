from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from contribarena.config.schema import DEFAULT_MEMORY_RELATIVE, RunConfig
from contribarena.engine.seasons import participant_goal_state_path
from contribarena.memory.redact import redact_text
from contribarena.models import (
    GoalContext,
    GoalEvent,
    GoalScope,
    GoalState,
    GoalStatus,
    GoalUpdateResult,
    RunPhase,
    ShortTermGoal,
    SubPhase,
)


EvidenceRefValidator = Callable[[str], bool]


EVIDENCE_REF_PATTERN = re.compile(
    r"^(tool_call:[A-Za-z0-9_.:-]+|artifact:[^#\s]+#L[1-9][0-9]*|workspace:[^\s]+|git:[0-9a-fA-F]{7,40})$"
)


def goal_state_path(config: RunConfig) -> Path:
    if config.goal.state_path is not None:
        return config.goal.state_path
    participant_path = participant_goal_state_path(config)
    if participant_path is not None:
        return participant_path
    if (
        config.artifacts.output_root == Path("runs")
        and config.memory.root != DEFAULT_MEMORY_RELATIVE
    ):
        return config.memory.root / "goal_state.json"
    return config.artifacts.output_root / "goal_state.json"


class GoalService:
    def __init__(
        self,
        config: RunConfig,
        *,
        run_id: str,
        evidence_ref_validator: EvidenceRefValidator | None = None,
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.evidence_ref_validator = evidence_ref_validator
        self.path = goal_state_path(config)
        self.state = GoalState(
            season_id=config.run.season_id or "",
            participant_id=config.run.participant_id or "",
        )
        self.events: list[GoalEvent] = []
        self.degraded = False
        self.error = ""
        if config.goal.enabled:
            self._load()

    @property
    def enabled(self) -> bool:
        return self.config.goal.enabled

    @property
    def context(self) -> GoalContext:
        short_term = self._current_run_short_term()
        fallback_phase = self.state.current_phase
        if short_term is None and self.state.short_term is not None:
            fallback_phase = "scout"
        phase, sub_phase = phase_for_goal(
            short_term,
            fallback_phase,
            draft_submitted=_has_active_draft(self.events),
        )
        return GoalContext(
            enabled=self.enabled,
            long_term_objective=self.config.goal.long_term_objective.strip(),
            short_term=self.state.short_term,
            current_phase=phase,
            current_sub_phase=sub_phase,
            degraded=self.degraded,
            error=self.error,
        )

    @property
    def abandoned_count(self) -> int:
        return sum(1 for event in self.events if event.event_type == "goal_abandoned")

    def abandoned_count_for_scope(self, scope: GoalScope) -> int:
        return sum(
            1
            for event in self.events
            if event.event_type == "goal_abandoned" and event.scope == scope
        )

    def update(
        self,
        *,
        objective: str = "",
        status: str = "active",
        evidence: str = "",
        scope: GoalScope | str | None = None,
        evidence_refs: list[str] | None = None,
        next_objective: str = "",
    ) -> GoalUpdateResult:
        if not self.enabled:
            return self._error("goal_disabled", "Goal tracking is disabled for this run.")
        if status not in {"active", "complete", "abandoned", "superseded"}:
            return self._error(
                "invalid_goal_status",
                "status must be active, complete, abandoned, or superseded.",
            )
        normalized_scope = _normalize_scope(scope, self.state.short_term)
        if normalized_scope is None:
            return self._error(
                "invalid_goal_scope",
                "scope must be repo, opportunity, or contribution.",
            )

        objective = redact_text(objective.strip(), max_chars=1000)
        evidence = redact_text(evidence.strip(), max_chars=1000)
        next_objective = redact_text(next_objective.strip(), max_chars=1000)
        refs = [redact_text(ref.strip(), max_chars=240) for ref in evidence_refs or [] if ref.strip()]
        ref_error = self._validate_evidence_refs(refs)
        if ref_error is not None:
            return ref_error
        now = _now()

        if status == "active":
            if not objective:
                return self._error(
                    "missing_goal_objective",
                    "active goal updates require a non-empty objective.",
                )
            current = self.state.short_term
            if current is None or current.status in {"complete", "abandoned"}:
                goal = ShortTermGoal(
                    goal_id=uuid.uuid4().hex[:12],
                    objective=objective,
                    status="active",
                    scope=normalized_scope,
                    created_at=now,
                    updated_at=now,
                    evidence_summary=evidence,
                    evidence_refs=refs,
                    next_objective=next_objective,
                )
                self.state.short_term = goal
                event_type = "goal_created"
            else:
                goal = current.model_copy(
                    update={
                        "objective": objective,
                        "scope": normalized_scope,
                        "updated_at": now,
                        "evidence_summary": evidence or current.evidence_summary,
                        "evidence_refs": refs,
                        "next_objective": next_objective,
                    }
                )
                self.state.short_term = goal
                event_type = "goal_updated"
            return self._record_success(event_type, goal, evidence)

        current = self.state.short_term
        if current is None or current.status != "active":
            return self._error(
                "no_active_goal",
                "complete, abandoned, or superseded requires an active short-term goal.",
            )
        if not evidence:
            return self._error(
                "missing_goal_evidence",
                "complete, abandoned, or superseded requires an evidence summary.",
            )
        if status in {"abandoned", "superseded", "complete"} and not refs:
            return self._error(
                "missing_evidence_refs",
                "abandoned, superseded, and complete goal updates require evidence_refs.",
            )
        if status == "superseded" and not next_objective:
            return self._error(
                "missing_next_objective",
                "superseded goal updates require next_objective.",
            )
        if status == "superseded":
            goal = current.model_copy(
                update={
                    "scope": normalized_scope,
                    "updated_at": now,
                    "objective": next_objective,
                    "evidence_summary": evidence,
                    "evidence_refs": refs,
                    "next_objective": next_objective,
                }
            )
            self.state.short_term = goal
            return self._record_success(
                "goal_superseded",
                goal,
                evidence,
                event_status="superseded",
            )

        goal = current.model_copy(
            update={
                "status": status,
                "scope": normalized_scope,
                "updated_at": now,
                "evidence_summary": evidence,
                "evidence_refs": refs,
                "next_objective": next_objective,
            }
        )
        self.state.short_term = goal
        event_type = _event_type_for_status(status)
        return self._record_success(event_type, goal, evidence)

    def events_text(self) -> str:
        if not self.events:
            return ""
        return "\n".join(event.model_dump_json() for event in self.events) + "\n"

    def phase_transition_text(self) -> str:
        rows = []
        previous: tuple[RunPhase, SubPhase] | None = None
        for event in self.events:
            current = (event.phase, event.sub_phase)
            if previous is None or current != previous:
                rows.append(
                    json.dumps(
                        {
                            "schema_version": "1",
                            "event_id": event.event_id,
                            "source": "goal_events.jsonl",
                            "run_id": event.run_id,
                            "season_id": event.season_id,
                            "participant_id": event.participant_id,
                            "goal_id": event.goal_id,
                            "event_type": event.event_type,
                            "scope": event.scope,
                            "status": event.status,
                            "phase": event.phase,
                            "sub_phase": event.sub_phase,
                            "created_at": event.created_at,
                        },
                        ensure_ascii=True,
                    )
                )
            previous = current
        return "\n".join(rows) + ("\n" if rows else "")

    def record_draft_submitted(self, *, evidence: str = "Draft patch submitted for review.") -> None:
        goal = self.state.short_term
        if goal is None or goal.scope != "contribution" or goal.status != "active":
            return
        self.events.append(
            GoalEvent(
                event_id=uuid.uuid4().hex[:12],
                event_type="draft_submitted",
                run_id=self.run_id,
                season_id=self.config.run.season_id or "",
                participant_id=self.config.run.participant_id or "",
                goal_id=goal.goal_id,
                status=goal.status,
                scope=goal.scope,
                phase="review",
                sub_phase=None,
                objective=goal.objective,
                evidence_summary=redact_text(evidence, max_chars=1000),
                evidence_refs=goal.evidence_refs,
                next_objective=goal.next_objective,
                created_at=_now(),
            )
        )

    def record_budget_event(
        self,
        *,
        event_type: str,
        phase: RunPhase,
        sub_phase: SubPhase,
        evidence: str,
    ) -> None:
        goal = self._current_run_short_term()
        self.events.append(
            GoalEvent(
                event_id=uuid.uuid4().hex[:12],
                event_type=event_type,
                run_id=self.run_id,
                season_id=self.config.run.season_id or "",
                participant_id=self.config.run.participant_id or "",
                goal_id=goal.goal_id if goal is not None else "",
                status=goal.status if goal is not None else None,
                scope=goal.scope if goal is not None else None,
                phase=phase,
                sub_phase=sub_phase,
                objective=goal.objective if goal is not None else "",
                evidence_summary=redact_text(evidence, max_chars=1000),
                evidence_refs=goal.evidence_refs if goal is not None else [],
                next_objective=goal.next_objective if goal is not None else "",
                created_at=_now(),
            )
        )

    def validate_evidence_refs(self, refs: list[str]) -> GoalUpdateResult | None:
        return self._validate_evidence_refs(refs)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.state = GoalState.model_validate_json(self.path.read_text(encoding="utf-8"))
            self._attach_identity_to_state()
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            self.degraded = True
            self.error = redact_text(str(exc), max_chars=256)
            self.state = GoalState(
                season_id=self.config.run.season_id or "",
                participant_id=self.config.run.participant_id or "",
            )

    def _save(self) -> None:
        self._attach_identity_to_state()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.state.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    def _record_success(
        self,
        event_type: str,
        goal: ShortTermGoal,
        evidence: str,
        *,
        event_status: GoalStatus | None = None,
    ) -> GoalUpdateResult:
        phase, sub_phase = phase_for_goal(goal, self.state.current_phase)
        self.state.current_phase = phase
        self.state.current_sub_phase = sub_phase
        try:
            self._save()
        except OSError as exc:
            return self._error("goal_state_write_failed", redact_text(str(exc), max_chars=256))
        event = GoalEvent(
            event_id=uuid.uuid4().hex[:12],
            event_type=event_type,
            run_id=self.run_id,
            season_id=self.config.run.season_id or "",
            participant_id=self.config.run.participant_id or "",
            goal_id=goal.goal_id,
            status=event_status or goal.status,
            scope=goal.scope,
            phase=self.state.current_phase,
            sub_phase=self.state.current_sub_phase,
            objective=goal.objective,
            evidence_summary=evidence,
            evidence_refs=goal.evidence_refs,
            next_objective=goal.next_objective,
            created_at=_now(),
        )
        self.events.append(event)
        return GoalUpdateResult(success=True, goals=self.context, event=event)

    def _attach_identity_to_state(self) -> None:
        season_id = self.config.run.season_id or self.state.season_id
        participant_id = self.config.run.participant_id or self.state.participant_id
        if season_id != self.state.season_id or participant_id != self.state.participant_id:
            self.state = self.state.model_copy(
                update={
                    "season_id": season_id,
                    "participant_id": participant_id,
                }
            )

    def _validate_evidence_refs(self, refs: list[str]) -> GoalUpdateResult | None:
        for ref in refs:
            if not EVIDENCE_REF_PATTERN.match(ref):
                return self._error(
                    "invalid_evidence_ref",
                    (
                        "evidence_refs must use tool_call:<id>, artifact:<path>#L<line>, "
                        "workspace:<path>, or git:<sha>."
                    ),
                )
            if self.evidence_ref_validator is not None and not self.evidence_ref_validator(ref):
                return self._error(
                    "evidence_invalid",
                    f"evidence_ref could not be resolved: {ref}",
                )
        return None

    def _current_run_short_term(self) -> ShortTermGoal | None:
        goal = self.state.short_term
        if goal is None:
            return None
        if goal.status in {"complete", "abandoned"} and not _has_current_run_goal_event(self.events):
            return None
        return goal

    def _error(self, kind: str, message: str) -> GoalUpdateResult:
        return GoalUpdateResult(
            success=False,
            goals=self.context,
            error_kind=kind,
            error_message=message,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def phase_for_goal(
    goal: ShortTermGoal | None,
    fallback_phase: RunPhase = "scout",
    *,
    draft_submitted: bool = False,
) -> tuple[RunPhase, SubPhase]:
    if goal is None:
        return fallback_phase, "project" if fallback_phase == "scout" else None
    if goal.status == "complete":
        return "completed", None
    if goal.scope == "contribution" and draft_submitted:
        return "review", None
    if goal.scope == "repo":
        return "scout", "project"
    if goal.scope == "opportunity":
        return "scout", "opportunity"
    return "work", None


def _normalize_scope(
    scope: GoalScope | str | None,
    current: ShortTermGoal | None,
) -> GoalScope | None:
    if scope is None or scope == "":
        return current.scope if current is not None else "contribution"
    if scope in {"repo", "opportunity", "contribution"}:
        return scope  # type: ignore[return-value]
    return None


def _has_active_draft(events: list[GoalEvent]) -> bool:
    if not events:
        return False
    last_goal_event = ""
    for event in events:
        if event.event_type == "draft_submitted":
            last_goal_event = "draft_submitted"
        elif event.event_type in {
            "goal_created",
            "goal_updated",
            "goal_superseded",
            "goal_abandoned",
            "goal_completed",
        }:
            last_goal_event = event.event_type
    return last_goal_event == "draft_submitted"


def _has_current_run_goal_event(events: list[GoalEvent]) -> bool:
    return any(
        event.event_type
        in {
            "goal_created",
            "goal_updated",
            "goal_superseded",
            "goal_abandoned",
            "goal_completed",
            "draft_submitted",
        }
        for event in events
    )


def _event_type_for_status(status: GoalStatus | str) -> str:
    if status == "complete":
        return "goal_completed"
    if status == "abandoned":
        return "goal_abandoned"
    if status == "superseded":
        return "goal_superseded"
    return "goal_updated"
