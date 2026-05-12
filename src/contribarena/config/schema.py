from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class BudgetConfig(BaseModel):
    max_steps: int = Field(default=25, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_time_seconds: int | None = Field(default=900, ge=1)


class RunSection(BaseModel):
    id: str | None = None
    mode: Literal["shadow", "dry_run", "owned_live"] = "shadow"
    model: str = "local-stub"
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
    resources: WorkspaceResources = Field(default_factory=WorkspaceResources)


class ArtifactConfig(BaseModel):
    output_root: Path = Path("runs")


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


class ContributionClassesConfig(BaseModel):
    allowed: list[Literal["docs", "tests", "low_risk_code"]] = Field(
        default_factory=lambda: ["docs", "tests", "low_risk_code"]
    )


class KillSwitchesConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_switch: bool = Field(default=False, alias="global")
    repositories: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class GovernanceConfig(BaseModel):
    live_enabled: bool = False
    owned_repositories: list[OwnedRepositoryPolicy] = Field(default_factory=list)
    bot_identity: BotIdentityConfig = Field(default_factory=BotIdentityConfig)
    rate_limits: GovernanceRateLimits = Field(default_factory=GovernanceRateLimits)
    contribution_classes: ContributionClassesConfig = Field(
        default_factory=ContributionClassesConfig
    )
    kill_switches: KillSwitchesConfig = Field(default_factory=KillSwitchesConfig)
    state_path: Path | None = None


class ControllerConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=300, ge=1)
    max_ticks: int | None = Field(default=1, ge=1)


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
    max_tokens: int = Field(default=4096, ge=1)

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
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    controller: ControllerConfig = Field(default_factory=ControllerConfig)

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


def _owned_repo_policy(
    governance: GovernanceConfig,
    owner: str,
    repo: str,
) -> OwnedRepositoryPolicy | None:
    for policy in governance.owned_repositories:
        if policy.owner == owner and policy.repo == repo:
            return policy
    return None
