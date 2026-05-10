from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class BudgetConfig(BaseModel):
    max_steps: int = Field(default=25, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_wall_time_seconds: int | None = Field(default=900, ge=1)


class RunSection(BaseModel):
    id: str | None = None
    mode: Literal["shadow"] = "shadow"
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


class DiscoveryConfig(BaseModel):
    candidates: list[RepoCandidate]

    @field_validator("candidates")
    @classmethod
    def require_candidate(cls, value: list[RepoCandidate]) -> list[RepoCandidate]:
        if not value:
            raise ValueError("at least one discovery candidate is required")
        return value


class WorkspaceResources(BaseModel):
    cpus: str = "2"
    memory: str = "4g"
    disk: str = "10g"


class WorkspaceConfig(BaseModel):
    backend: Literal["docker"] = "docker"
    image: str = "contribarena/workspace:latest"
    workdir: str = "/workspace"
    command_timeout_seconds: int = Field(default=300, ge=1)
    resources: WorkspaceResources = Field(default_factory=WorkspaceResources)


class ArtifactConfig(BaseModel):
    output_root: Path = Path("runs")


class CompatibleModelConfig(BaseModel):
    base_url: str
    model: str | None = None
    api_key_env: str = "OPENAI_API_KEY"


class AnthropicModelConfig(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = Field(default=4096, ge=1)


class GeminiModelConfig(BaseModel):
    endpoint: str
    model: str | None = None
    api_key_env: str = "GEMINI_API_KEY"
    api_key_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    max_tokens: int = Field(default=4096, ge=1)


class ModelProvidersConfig(BaseModel):
    compatible: dict[str, CompatibleModelConfig] = Field(default_factory=dict)
    anthropic: dict[str, AnthropicModelConfig] = Field(default_factory=dict)
    gemini: dict[str, GeminiModelConfig] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    providers: ModelProvidersConfig = Field(default_factory=ModelProvidersConfig)


class RunConfig(BaseModel):
    run: RunSection
    discovery: DiscoveryConfig
    workspace: WorkspaceConfig
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
