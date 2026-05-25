from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


DEFAULT_MEMORY_RELATIVE = Path(".contribarena/memory")
DEFAULT_READ_MODEL_RELATIVE = Path(".contribarena/read_model.sqlite")


class ScoutBudgetConfig(BaseModel):
    max_candidate_repos_considered: int = Field(default=6, ge=1)
    max_opportunities_considered: int = Field(default=8, ge=1)
    max_duplicate_checks: int = Field(default=12, ge=1)
    max_probe_seconds_per_repo: int = Field(default=60, ge=1)
    max_scout_invocations: int = Field(default=6, ge=1)


class WorkBudgetConfig(BaseModel):
    max_repo_switches: int = Field(default=2, ge=0)
    max_opportunity_switches: int = Field(default=3, ge=0)
    max_strategy_switches_per_opportunity: int = Field(default=2, ge=0)
    max_invocations: int = Field(default=4, ge=1)


class ReviewBudgetConfig(BaseModel):
    max_review_rounds: int = Field(default=2, ge=0)
    max_invocations: int = Field(default=3, ge=1)


class BudgetConfig(BaseModel):
    max_steps: int = Field(default=80, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_time_seconds: int | None = Field(default=3600, ge=1)
    max_invocations: int = Field(default=5, ge=1)
    max_consecutive_no_progress: int = Field(default=2, ge=1)
    max_recoveries: int = Field(default=8, ge=1)
    scout: ScoutBudgetConfig = Field(default_factory=ScoutBudgetConfig)
    work: WorkBudgetConfig = Field(default_factory=WorkBudgetConfig)
    review: ReviewBudgetConfig = Field(default_factory=ReviewBudgetConfig)


class RunSection(BaseModel):
    id: str | None = None
    mode: Literal["shadow", "dry_run", "owned_live", "external_live"] = "shadow"
    model: str = "local-stub"
    season_id: str | None = None
    participant_id: str | None = None
    wake_source: Literal["manual", "auto", "unranked"] = "unranked"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class RepoCandidate(BaseModel):
    owner: str
    repo: str
    url: HttpUrl
    branch: str | None = None
    notes: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class RepoSearchFilters(BaseModel):
    language: str | None = None
    stars_min: int | None = Field(default=None, ge=0)
    pushed_after: str | None = None
    topic: str | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.language,
                self.stars_min is not None,
                self.pushed_after,
                self.topic,
            ]
        )


class DiscoveryConfig(BaseModel):
    candidates: list[RepoCandidate] = Field(default_factory=list)
    query: str = ""
    filters: RepoSearchFilters = Field(default_factory=RepoSearchFilters)

    @model_validator(mode="after")
    def require_candidate_or_search(self) -> DiscoveryConfig:
        if not self.candidates and not self.query.strip() and self.filters.is_empty():
            raise ValueError("discovery requires at least one candidate or a search query/filter")
        return self


class IssueConfig(BaseModel):
    problem_statement: str = Field(min_length=1)
    clone_url: HttpUrl | None = None
    title: str | None = None
    source_url: HttpUrl | None = None
    reproduction_hint: str | None = None
    verification_hint: str | None = None


class WorkspaceResources(BaseModel):
    cpus: str = "2"
    memory: str = "4g"
    disk: str = "10g"


class WorkspaceConfig(BaseModel):
    backend: Literal["docker"] = "docker"
    image: str = "contribarena/workspace:latest"
    workdir: str = "/workspace"
    command_timeout_seconds: int = Field(default=300, ge=1)
    cleanup_policy: Literal["always", "retain_on_failure", "retain_always"] = "always"
    persistent_key: str | None = None
    persistent_metadata_path: Path | None = None
    resources: WorkspaceResources = Field(default_factory=WorkspaceResources)


class ArtifactConfig(BaseModel):
    output_root: Path = Path("runs")


class MemoryConfig(BaseModel):
    enabled: bool = True
    root: Path = DEFAULT_MEMORY_RELATIVE
    backend: Literal["noop", "graphiti"] = "noop"
    graphiti_enabled: bool = False
    graphiti_graph_backend: Literal["falkordb"] = "falkordb"
    falkordb_host: str = "localhost"
    falkordb_port: int = Field(default=6379, ge=1)
    falkordb_username: str = ""
    falkordb_password_env: str = "FALKORDB_PASSWORD"
    graphiti_group_prefix: str = "contribarena"
    graphiti_llm_api_key_env: str = "CONTRIBARENA_GRAPHITI_LLM_API_KEY"
    graphiti_llm_base_url: str | None = None
    graphiti_llm_base_url_env: str | None = "CONTRIBARENA_GRAPHITI_LLM_BASE_URL"
    graphiti_llm_model: str = "gpt-4.1"
    graphiti_llm_small_model: str = ""
    graphiti_llm_temperature: float = Field(default=0.1, ge=0)
    graphiti_embedding_api_key_env: str = "CONTRIBARENA_GRAPHITI_EMBEDDING_API_KEY"
    graphiti_embedding_base_url: str | None = None
    graphiti_embedding_base_url_env: str | None = "CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL"
    graphiti_embedding_model: str = "text-embedding-3-small"
    graphiti_embedding_dim: int = Field(default=1024, ge=1)
    graphiti_timeout_seconds: int = Field(default=30, ge=1)
    graphiti_max_episode_chars: int = Field(default=12_000, ge=1)
    graphiti_update_communities: bool = False
    graphiti_telemetry_enabled: bool = False
    history_index_enabled: bool = True
    max_history_record_chars: int = Field(default=16_000, ge=1)
    schema_version: Literal["1"] = "1"


class GuidanceConfig(BaseModel):
    enabled: bool = True


class GoalConfig(BaseModel):
    enabled: bool = True
    max_abandoned_goals_per_run: int = Field(default=3, ge=1)
    long_term_objective: str = (
        "Own this GitHub identity as an autonomous open-source developer. "
        "Build a public track record of meaningful engineering contributions, "
        "discover projects where small focused code changes can create real "
        "value, earn maintainer trust through respectful collaboration, and "
        "steadily help open-source software improve."
    )
    state_path: Path | None = None


class JudgementJudgeConfig(BaseModel):
    id: str
    model: str


class JudgementOutcomeAdjustments(BaseModel):
    opened: int = 2
    reviewed: int = 8
    merged: int = 30
    changes_requested: int = -8
    closed: int = -20
    spam_or_opt_out: int = -50


def _default_judgement_dimension_weights() -> dict[str, float]:
    return {
        "project_fit": 0.08,
        "opportunity_quality": 0.12,
        "duplicate_avoidance": 0.10,
        "repository_understanding": 0.08,
        "execution_correctness": 0.25,
        "verification_quality": 0.12,
        "submission_discipline": 0.10,
        "review_readiness": 0.10,
        "agentic_judgment": 0.05,
    }


class JudgementConfig(BaseModel):
    enabled: bool = True
    season_id: str = "season_0"
    season_name: str = "Season 0"
    season_phase: Literal["owned_repo_calibration", "external_live", "archived", "unknown"] = (
        "owned_repo_calibration"
    )
    rubric_version: str = "m0.10"
    panel_id: str = "m0_10_default"
    judges: list[JudgementJudgeConfig] = Field(default_factory=list)
    dimension_weights: dict[str, float] = Field(
        default_factory=_default_judgement_dimension_weights
    )
    outcome_adjustments: JudgementOutcomeAdjustments = Field(
        default_factory=JudgementOutcomeAdjustments
    )


class PrSubmissionConfig(BaseModel):
    strategy: Literal["fork", "upstream_branch"] = "fork"
    fork_owner: str | None = None


class OwnedRepositoryPolicy(BaseModel):
    owner: str
    repo: str
    default_branch: str = "main"
    pr_submission: PrSubmissionConfig = Field(default_factory=PrSubmissionConfig)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class BotIdentityConfig(BaseModel):
    kind: Literal["pat"] = "pat"
    actor: str = ""
    token_env: str = "GITHUB_TOKEN"


class GovernanceRateLimits(BaseModel):
    max_open_prs_per_repo: int = Field(default=1, ge=0)
    max_prs_per_repo_per_day: int = Field(default=3, ge=0)
    min_minutes_between_prs_per_repo: int = Field(default=30, ge=0)
    max_open_prs_per_org: int = Field(default=3, ge=0)
    max_prs_per_org_per_day: int = Field(default=5, ge=0)
    min_minutes_between_prs_per_org: int = Field(default=60, ge=0)
    max_open_prs_global: int = Field(default=10, ge=0)
    max_prs_global_per_day: int = Field(default=10, ge=0)
    min_minutes_between_prs_global: int = Field(default=15, ge=0)


class ContributionClassesConfig(BaseModel):
    allowed: list[Literal["docs", "tests", "low_risk_code"]] = Field(
        default_factory=lambda: ["docs", "tests", "low_risk_code"]
    )


class KillSwitchesConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_switch: bool = Field(default=False, alias="global")
    repositories: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class ExternalLiveConfig(BaseModel):
    poll_interval_seconds: int = Field(default=21_600, ge=60)
    initial_poll_delay_seconds: int = Field(default=60, ge=0)
    require_maintainer_fit: bool = True
    require_spam_risk_review: bool = True
    attempt_upstream_labels: bool = False
    allow_public_comments: bool = True
    allowed_comment_events: list[str] = Field(
        default_factory=lambda: [
            "pr_opened",
            "ci_fixed",
            "review_addressed",
            "requested_change_completed",
        ]
    )


class GovernanceConfig(BaseModel):
    live_enabled: bool = False
    owned_repositories: list[OwnedRepositoryPolicy] = Field(default_factory=list)
    bot_identity: BotIdentityConfig = Field(default_factory=BotIdentityConfig)
    rate_limits: GovernanceRateLimits = Field(default_factory=GovernanceRateLimits)
    contribution_classes: ContributionClassesConfig = Field(
        default_factory=ContributionClassesConfig
    )
    kill_switches: KillSwitchesConfig = Field(default_factory=KillSwitchesConfig)
    external_live: ExternalLiveConfig = Field(default_factory=ExternalLiveConfig)
    state_path: Path | None = None


class SeasonDefaultsConfig(BaseModel):
    wake_interval: str = "6h"
    max_concurrent_runs: int = Field(default=1, ge=1)


class SeasonParticipantConfig(BaseModel):
    id: str | None = None
    model: str
    role: list[Literal["agent", "judge"]] = Field(default_factory=lambda: ["agent", "judge"])
    wake_interval: str | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1)
    repo_policy: dict[str, object] = Field(default_factory=dict)


class SeasonDiscoveryProfileConfig(BaseModel):
    scope: Literal["owned", "external"] = "owned"
    seed_queries: list[str] = Field(default_factory=list)
    language_filter: list[str] = Field(default_factory=list)
    topic_filter: list[str] = Field(default_factory=list)
    min_stars: int | None = Field(default=None, ge=0)
    activity_window_days: int | None = Field(default=None, ge=1)
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)


class SeasonConfig(BaseModel):
    id: str = "season_0"
    name: str = "Season 0"
    status: Literal["draft", "active", "observing", "completed"] = "draft"
    defaults: SeasonDefaultsConfig = Field(default_factory=SeasonDefaultsConfig)
    participants: list[SeasonParticipantConfig] = Field(default_factory=list)
    discovery_profile: SeasonDiscoveryProfileConfig = Field(
        default_factory=SeasonDiscoveryProfileConfig
    )
    repo_policy: dict[str, object] = Field(default_factory=dict)
    scoring_weights: dict[str, float] = Field(default_factory=dict)
    judge_panel: dict[str, object] = Field(default_factory=dict)
    state_root: Path | None = None


class ControllerConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=300, ge=1)
    max_ticks: int | None = Field(default=1, ge=1)


class BackendConfig(BaseModel):
    read_model_path: Path = DEFAULT_READ_MODEL_RELATIVE
    api_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://contribarena.org",
            "https://www.contribarena.org",
        ]
    )
    watch_enabled: bool = True
    refresh_debounce_seconds: float = Field(default=1.0, ge=0.1)


class CompatibleModelConfig(BaseModel):
    base_url: str | None = None
    base_url_env: str | None = None
    model: str | None = None
    api_key_env: str = "OPENAI_API_KEY"

    @model_validator(mode="after")
    def require_base_url(self) -> CompatibleModelConfig:
        if not self.base_url and not self.base_url_env:
            raise ValueError("compatible model config requires base_url or base_url_env")
        return self


class ResponsesModelConfig(BaseModel):
    base_url: str | None = None
    base_url_env: str | None = None
    model: str | None = None
    api_key_env: str = "OPENAI_API_KEY"

    @model_validator(mode="after")
    def require_base_url(self) -> ResponsesModelConfig:
        if not self.base_url and not self.base_url_env:
            raise ValueError("responses model config requires base_url or base_url_env")
        return self


class AnthropicModelConfig(BaseModel):
    base_url: str | None = None
    base_url_env: str | None = None
    model: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = Field(default=16_384, ge=1)
    thinking_enabled: bool = True
    thinking_type: Literal["adaptive", "enabled"] = "adaptive"
    thinking_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    thinking_budget_tokens: int | None = Field(default=None, ge=1024)

    @model_validator(mode="after")
    def require_base_url(self) -> AnthropicModelConfig:
        if not self.base_url and not self.base_url_env:
            raise ValueError("anthropic model config requires base_url or base_url_env")
        return self


class GeminiModelConfig(BaseModel):
    endpoint: str | None = None
    endpoint_env: str | None = None
    model: str | None = None
    api_key_env: str = "GEMINI_API_KEY"
    api_key_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    max_tokens: int = Field(default=4096, ge=1)

    @model_validator(mode="after")
    def require_endpoint(self) -> GeminiModelConfig:
        if not self.endpoint and not self.endpoint_env:
            raise ValueError("gemini model config requires endpoint or endpoint_env")
        return self


class ModelProvidersConfig(BaseModel):
    compatible: dict[str, CompatibleModelConfig] = Field(default_factory=dict)
    responses: dict[str, ResponsesModelConfig] = Field(default_factory=dict)
    anthropic: dict[str, AnthropicModelConfig] = Field(default_factory=dict)
    gemini: dict[str, GeminiModelConfig] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    providers: ModelProvidersConfig = Field(default_factory=ModelProvidersConfig)


class RunConfig(BaseModel):
    run: RunSection
    discovery: DiscoveryConfig
    issue: IssueConfig | None = None
    workspace: WorkspaceConfig
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    guidance: GuidanceConfig = Field(default_factory=GuidanceConfig)
    goal: GoalConfig = Field(default_factory=GoalConfig)
    judgement: JudgementConfig = Field(default_factory=JudgementConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    season: SeasonConfig | None = None
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)

    @model_validator(mode="after")
    def validate_issue_solving_target(self) -> RunConfig:
        if self.issue is None:
            return self._validate_owned_live_target()
        if len(self.discovery.candidates) != 1:
            raise ValueError("issue-solving mode requires exactly one fixed discovery candidate")
        if self.issue.clone_url is None:
            raise ValueError("issue-solving mode requires issue.clone_url")
        return self._validate_owned_live_target()

    def _validate_owned_live_target(self) -> RunConfig:
        if self.run.mode == "external_live":
            return self._validate_external_live_target()
        if self.run.mode != "owned_live":
            return self
        if len(self.discovery.candidates) != 1:
            raise ValueError("owned_live mode requires exactly one configured repository")
        candidate = self.discovery.candidates[0]
        if not _owned_repo_policy(self.governance, candidate.owner, candidate.repo):
            raise ValueError(
                "owned_live mode requires the configured repository in governance.owned_repositories"
            )
        return self

    def _validate_external_live_target(self) -> RunConfig:
        if self.issue is not None:
            raise ValueError("external_live mode does not support fixed issue-solving targets")
        for policy in self.governance.owned_repositories:
            if policy.pr_submission.strategy == "upstream_branch":
                raise ValueError("external_live mode requires fork-only PR submission")
        return self


def _owned_repo_policy(
    governance: GovernanceConfig,
    owner: str,
    repo: str,
) -> OwnedRepositoryPolicy | None:
    for policy in governance.owned_repositories:
        if policy.owner == owner and policy.repo == repo:
            return policy
    return None
