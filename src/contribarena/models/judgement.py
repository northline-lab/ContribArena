from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


JudgementDimension = Literal[
    "project_fit",
    "opportunity_quality",
    "duplicate_avoidance",
    "repository_understanding",
    "execution_correctness",
    "verification_quality",
    "submission_discipline",
    "review_readiness",
    "agentic_judgment",
]

JudgementStatus = Literal[
    "not_judged",
    "judged",
    "partial_fallback",
    "fallback",
    "deferred",
    "failed",
    "unknown",
]


class JudgementSeason(BaseModel):
    id: str
    name: str = ""
    phase: Literal["owned_repo_calibration", "external_live", "archived", "unknown"] = "unknown"


class JudgementTarget(BaseModel):
    kind: Literal["run"] = "run"
    pr_url: str = ""


class JudgementPanel(BaseModel):
    panel_id: str
    aggregation: Literal["mean"] = "mean"


class JudgementRubricScore(BaseModel):
    dimension: JudgementDimension
    evidence: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=5)
    max_score: int = 5
    weight: float = Field(default=0, ge=0)
    source: Literal["llm", "fallback"] = "llm"


class JudgementJudgeResult(BaseModel):
    judge_id: str
    model: str
    rubric: list[JudgementRubricScore]
    judge_score: float = Field(ge=0)
    error: str = ""


class JudgementAggregateRubricScore(BaseModel):
    dimension: JudgementDimension
    mean_score: float = Field(ge=0, le=5)
    max_score: int = 5
    weight: float = Field(default=0, ge=0)
    disagreement: float = Field(ge=0)


class JudgementMaintainerOutcome(BaseModel):
    status: str = "pending"
    source: str = "none"


class JudgementArtifact(BaseModel):
    schema_version: Literal["1"] = "1"
    status: JudgementStatus = "judged"
    season_id: str
    run_id: str
    target: JudgementTarget = Field(default_factory=JudgementTarget)
    judge_panel: JudgementPanel
    judge_packet: str = "judge_packet.json"
    judge_dimension_packets: str = "judge_dimension_packets.json"
    judges: list[JudgementJudgeResult] = Field(default_factory=list)
    aggregate_rubric: list[JudgementAggregateRubricScore] = Field(default_factory=list)
    judge_score: float = Field(ge=0)
    real_world_adjustment: int = 0
    arena_score: float = Field(ge=0)
    maintainer_outcome: JudgementMaintainerOutcome = Field(
        default_factory=JudgementMaintainerOutcome
    )
    evidence: list[str] = Field(default_factory=list)
    created_at: str = ""


class JudgePacket(BaseModel):
    schema_version: Literal["1"] = "1"
    season: JudgementSeason
    run_id: str
    repository: dict[str, object] = Field(default_factory=dict)
    opportunity: dict[str, object] = Field(default_factory=dict)
    terminal: dict[str, object] = Field(default_factory=dict)
    pipeline: list[dict[str, object]] = Field(default_factory=list)
    quality_gate: dict[str, object] = Field(default_factory=dict)
    pull_request: dict[str, object] = Field(default_factory=dict)
    maintainer_outcome: dict[str, object] = Field(default_factory=dict)
    contribution_class: str = "unknown"
    artifacts: list[str] = Field(default_factory=list)
    selected_task_summary: str = ""
    eligibility_summary: str = ""
    maintainer_fit_summary: str = ""
    behavior_summary: dict[str, object] = Field(default_factory=dict)
    patch_excerpt: str = ""
    pr_description_excerpt: str = ""
    verification_excerpt: str = ""
    discovery_calls_summary: str = ""
    phase_scout_project_excerpt: str = ""
    phase_scout_opportunity_excerpt: str = ""
    phase_scout_duplicate_excerpt: str = ""
    phase_review_maintainer_excerpt: str = ""
    phase_review_response_excerpt: str = ""
    live_action_excerpt: str = ""
    submission_outcome: str = ""
    score_status: str = ""
    ranking_eligible: bool = True
    ranking_exclusion_reason: str = ""
    goal_events_excerpt: str = ""
    phase_transition_excerpt: str = ""
    tool_violation_excerpt: str = ""
