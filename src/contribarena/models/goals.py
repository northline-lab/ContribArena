from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


GoalStatus = Literal["active", "complete", "abandoned"]


class ShortTermGoal(BaseModel):
    schema_version: Literal["1"] = "1"
    goal_id: str
    objective: str
    status: GoalStatus = "active"
    created_at: str
    updated_at: str
    evidence_summary: str = ""


class GoalContext(BaseModel):
    schema_version: Literal["1"] = "1"
    enabled: bool = True
    long_term_objective: str = ""
    short_term: ShortTermGoal | None = None
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
    goal_id: str = ""
    status: GoalStatus | None = None
    objective: str = ""
    evidence_summary: str = ""
    created_at: str
    redacted: bool = True


class GoalState(BaseModel):
    schema_version: Literal["1"] = "1"
    short_term: ShortTermGoal | None = None


class GoalUpdateResult(BaseModel):
    success: bool
    goals: GoalContext
    event: GoalEvent | None = None
    error_kind: str = ""
    error_message: str = ""
