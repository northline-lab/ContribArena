from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContributionClass = Literal["low_risk_code", "tests", "docs", "mixed", "unknown"]
PipelineStatus = Literal[
    "not_started",
    "running",
    "passed",
    "failed",
    "blocked",
    "skipped",
    "pending",
    "unknown",
]
ArtifactVisibility = Literal["public", "operator", "internal"]
MaintainerOutcomeStatus = Literal[
    "pending",
    "reviewed",
    "changes_requested",
    "merged",
    "closed",
    "stale",
    "unknown",
]


class SurfaceAgent(BaseModel):
    name: str = "builtin"
    handle: str = ""
    participant_id: str = ""


class RuntimeStatus(BaseModel):
    status: str = ""
    reason: str = ""
    message: str = ""
    attempts: int = 0


class SurfaceRepository(BaseModel):
    full_name: str = ""
    url: str = ""


class SurfacePipelineStage(BaseModel):
    stage_id: Literal[
        "agent",
        "repo_discovery",
        "workspace",
        "patch_diff",
        "quality_gate",
        "pull_request",
        "maintainer_outcome",
    ]
    status: PipelineStatus = "unknown"
    started_at: str = ""
    completed_at: str = ""
    summary: str = ""
    source_artifacts: list[str] = Field(default_factory=list)


class SurfaceQualityGate(BaseModel):
    status: Literal["pass", "block", "fail", "unknown"] = "unknown"
    warnings: list[str] = Field(default_factory=list)


class SurfacePullRequest(BaseModel):
    url: str = ""
    number: int | None = None
    state: Literal["none", "open", "closed", "merged", "unknown"] = "none"


class SurfaceMaintainerOutcome(BaseModel):
    status: MaintainerOutcomeStatus = "pending"
    observed_at: str = ""
    source: Literal[
        "github_pr_state",
        "github_review",
        "github_comment",
        "ci_status",
        "manual_adjudication",
        "none",
    ] = "none"


class SurfaceSeason(BaseModel):
    id: str = ""
    name: str = ""
    phase: Literal["owned_repo_calibration", "external_live", "archived", "unknown"] = (
        "unknown"
    )


class SurfaceRubricScore(BaseModel):
    dimension: str
    score: float = 0
    max_score: int = 5
    weight: float = 0


class SurfaceJudgement(BaseModel):
    status: Literal[
        "not_judged",
        "judged",
        "partial_fallback",
        "fallback",
        "deferred",
        "failed",
        "unknown",
    ] = "not_judged"
    judge_score: float | None = None
    real_world_adjustment: int = 0
    arena_score: float | None = None
    rubric_summary: list[SurfaceRubricScore] = Field(default_factory=list)
    source_artifacts: list[str] = Field(default_factory=list)


class SurfaceArtifact(BaseModel):
    name: str
    kind: str
    visibility: ArtifactVisibility = "internal"
    url: str = ""
    size_bytes: int = 0
    redacted: bool = False


class RunSummary(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    run_mode: str
    model: str
    wake_source: Literal["manual", "auto", "unranked"] = "unranked"
    agent: SurfaceAgent = Field(default_factory=SurfaceAgent)
    repository: SurfaceRepository = Field(default_factory=SurfaceRepository)
    season: SurfaceSeason = Field(default_factory=SurfaceSeason)
    opportunity_source: Literal["issue_url", "discovery_event_id", "none"] = "none"
    opportunity_source_ref: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float | None = None
    run_status: str = "unknown"
    terminal_reason: str = ""
    terminal_layer: str = ""
    contribution_class: ContributionClass = "unknown"
    pipeline: list[SurfacePipelineStage] = Field(default_factory=list)
    quality_gate: SurfaceQualityGate = Field(default_factory=SurfaceQualityGate)
    pull_request: SurfacePullRequest = Field(default_factory=SurfacePullRequest)
    maintainer_outcome: SurfaceMaintainerOutcome = Field(
        default_factory=SurfaceMaintainerOutcome
    )
    judgement: SurfaceJudgement = Field(default_factory=SurfaceJudgement)
    artifacts: list[SurfaceArtifact] = Field(default_factory=list)
    workspace: dict[str, object] = Field(default_factory=dict)
    replacement: dict[str, object] = Field(default_factory=dict)
    judgement_retry: dict[str, object] = Field(default_factory=dict)
    live_submission_retry: dict[str, object] = Field(default_factory=dict)
    submission_outcome: str = ""
    score_status: Literal["scored", "diagnostic_only", "not_judged", "deferred", "failed"] = (
        "not_judged"
    )
    ranking_eligible: bool = True
    ranking_exclusion_reason: str = ""
    contribution_thread_id: str = ""
