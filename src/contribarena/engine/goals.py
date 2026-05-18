from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from contribarena.config.schema import DEFAULT_MEMORY_RELATIVE, RunConfig
from contribarena.memory.redact import redact_text
from contribarena.models import GoalContext, GoalEvent, GoalState, GoalUpdateResult, ShortTermGoal


def goal_state_path(config: RunConfig) -> Path:
    if config.goal.state_path is not None:
        return config.goal.state_path
    if (
        config.artifacts.output_root == Path("runs")
        and config.memory.root != DEFAULT_MEMORY_RELATIVE
    ):
        return config.memory.root / "goal_state.json"
    return config.artifacts.output_root / "goal_state.json"


class GoalService:
    def __init__(self, config: RunConfig, *, run_id: str) -> None:
        self.config = config
        self.run_id = run_id
        self.path = goal_state_path(config)
        self.state = GoalState()
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
        return GoalContext(
            enabled=self.enabled,
            long_term_objective=self.config.goal.long_term_objective.strip(),
            short_term=self.state.short_term,
            degraded=self.degraded,
            error=self.error,
        )

    @property
    def abandoned_count(self) -> int:
        return sum(1 for event in self.events if event.event_type == "goal_abandoned")

    def update(
        self,
        *,
        objective: str = "",
        status: str = "active",
        evidence: str = "",
    ) -> GoalUpdateResult:
        if not self.enabled:
            return self._error("goal_disabled", "Goal tracking is disabled for this run.")
        if status not in {"active", "complete", "abandoned"}:
            return self._error("invalid_goal_status", "status must be active, complete, or abandoned.")

        objective = redact_text(objective.strip(), max_chars=1000)
        evidence = redact_text(evidence.strip(), max_chars=1000)
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
                    created_at=now,
                    updated_at=now,
                    evidence_summary=evidence,
                )
                self.state.short_term = goal
                event_type = "goal_created"
            else:
                goal = current.model_copy(update={"objective": objective, "updated_at": now})
                self.state.short_term = goal
                event_type = "goal_updated"
            return self._record_success(event_type, goal, evidence)

        current = self.state.short_term
        if current is None or current.status != "active":
            return self._error("no_active_goal", "complete or abandoned requires an active short-term goal.")
        if not evidence:
            return self._error(
                "missing_goal_evidence",
                "complete or abandoned requires an evidence summary.",
            )
        goal = current.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "evidence_summary": evidence,
            }
        )
        self.state.short_term = goal
        event_type = "goal_completed" if status == "complete" else "goal_abandoned"
        return self._record_success(event_type, goal, evidence)

    def events_text(self) -> str:
        if not self.events:
            return ""
        return "\n".join(event.model_dump_json() for event in self.events) + "\n"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.state = GoalState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            self.degraded = True
            self.error = redact_text(str(exc), max_chars=256)
            self.state = GoalState()

    def _save(self) -> None:
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
    ) -> GoalUpdateResult:
        try:
            self._save()
        except OSError as exc:
            return self._error("goal_state_write_failed", redact_text(str(exc), max_chars=256))
        event = GoalEvent(
            event_id=uuid.uuid4().hex[:12],
            event_type=event_type,
            run_id=self.run_id,
            goal_id=goal.goal_id,
            status=goal.status,
            objective=goal.objective,
            evidence_summary=evidence,
            created_at=_now(),
        )
        self.events.append(event)
        return GoalUpdateResult(success=True, goals=self.context, event=event)

    def _error(self, kind: str, message: str) -> GoalUpdateResult:
        return GoalUpdateResult(
            success=False,
            goals=self.context,
            error_kind=kind,
            error_message=message,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()
