from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


GoalStatus = Literal["active", "complete", "abandoned", "superseded"]
GoalScope = Literal["repo", "opportunity", "contribution"]
RunPhase = Literal["scout", "work", "review", "completed"]
ScoutSubPhase = Literal["project", "opportunity"]
SubPhase = ScoutSubPhase | None


class ShortTermGoal(BaseModel):
    schema_version: Literal["1"] = "1"
    goal_id: str
    objective: str
    status: GoalStatus = "active"
    scope: GoalScope = "repo"
    created_at: str
    updated_at: str
    evidence_summary: str = ""
    evidence_refs: list[str] = []
    next_objective: str = ""


class GoalContext(BaseModel):
    schema_version: Literal["1"] = "1"
    enabled: bool = True
    long_term_objective: str = ""
    short_term: ShortTermGoal | None = None
    current_phase: RunPhase = "scout"
    current_sub_phase: SubPhase = "project"
    update_tool: str = "aci_goal_update"
    note: str = (
        "Long-term goal is config-owned and read-only. Short-term goal is the "
        "single mutable runtime objective."
    )
    degraded: bool = False
    error: str = ""


class GoalEvent(BaseModel):
    schema_version: Literal["1"] = "1"
    event_id: str
    event_type: str
    run_id: str
    season_id: str = ""
    participant_id: str = ""
    goal_id: str = ""
    status: GoalStatus | None = None
    scope: GoalScope | None = None
    phase: RunPhase = "scout"
    sub_phase: SubPhase = "project"
    objective: str = ""
    evidence_summary: str = ""
    evidence_refs: list[str] = []
    next_objective: str = ""
    created_at: str
    redacted: bool = True


class GoalState(BaseModel):
    schema_version: Literal["1"] = "1"
    season_id: str = ""
    participant_id: str = ""
    short_term: ShortTermGoal | None = None
    current_phase: RunPhase = "scout"
    current_sub_phase: SubPhase = "project"


class GoalUpdateResult(BaseModel):
    success: bool
    goals: GoalContext
    event: GoalEvent | None = None
    error_kind: str = ""
    error_message: str = ""
