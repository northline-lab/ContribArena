from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from contribarena.models.goals import GoalContext


class WorkingMemoryFact(BaseModel):
    key: str
    value: str
    source: Literal["agent", "harness"]
    derived_from: str = ""
    created_at: str
    updated_at: str


class WorkingMemoryPlanItem(BaseModel):
    id: str
    text: str
    status: Literal["open", "doing", "done", "dropped"] = "open"
    created_at: str
    updated_at: str


class WorkingMemoryNote(BaseModel):
    id: str
    text: str
    tags: list[str] = Field(default_factory=list)
    created_at: str


class GuidanceContext(BaseModel):
    available: bool = True
    entry_path: str = ".contribarena/guidance/guidance_entry.md"
    manifest_path: str = ".contribarena/guidance/guidance_manifest.json"
    path_relative_to: Literal["workspace_root"] = Field(
        default="workspace_root",
        description="Guidance paths are relative to the workspace root, not repo/.",
    )
    skipped_reason: str = ""
    error: str = ""


class MemoryHint(BaseModel):
    category: Literal["repo_context", "verification", "failure", "external_write"]
    summary_line: str = Field(max_length=200)
    suggested_query: str = Field(max_length=200)
    suggested_intent: Literal["repo_context", "verification", "failure", "external_write"]


class MemoryCapabilities(BaseModel):
    run_scope_notes: bool = True
    repo_scope_persistent: bool = False
    global_scope_persistent: bool = False
    note: str = (
        "scope='repo' and scope='global' notes are event-log-only until L2/L3 memory is enabled."
    )


class WorkingMemory(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    repo_full_name: str = ""
    guidance: GuidanceContext = Field(default_factory=GuidanceContext)
    goals: GoalContext = Field(default_factory=GoalContext)
    memory_hints: list[MemoryHint] = Field(default_factory=list)
    memory_capabilities: MemoryCapabilities = Field(default_factory=MemoryCapabilities)
    tracked_prs: list[TrackedPullRequestContext] = Field(default_factory=list)
    facts: dict[str, WorkingMemoryFact] = Field(default_factory=dict)
    plan: list[WorkingMemoryPlanItem] = Field(default_factory=list)
    notes: list[WorkingMemoryNote] = Field(default_factory=list)
    truncated: bool = False


class MemoryContext(BaseModel):
    schema_version: Literal["1"] = "1"
    snapshot_phase: Literal["run_start_context"] = "run_start_context"
    snapshot_note: str = (
        "This context is captured when run context is prepared and guidance status is known. "
        "Final goal state is recorded in goal_context.json."
    )
    run_id: str
    repo_full_name: str = ""
    enabled: bool = True
    degraded: bool = False
    backend: str = "noop"
    guidance: GuidanceContext = Field(default_factory=GuidanceContext)
    goals: GoalContext = Field(default_factory=GoalContext)
    memory_hints: list[MemoryHint] = Field(default_factory=list)
    memory_capabilities: MemoryCapabilities = Field(default_factory=MemoryCapabilities)
    tracked_prs: list[TrackedPullRequestContext] = Field(default_factory=list)
    history_results: list["MemorySearchItem"] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MemoryEvent(BaseModel):
    schema_version: Literal["1"] = "1"
    event_id: str
    event_type: str
    run_id: str
    repo_full_name: str = ""
    pr_number: int | None = None
    source_ref: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: Literal["low", "medium", "high"] = "medium"
    created_at: str
    redacted: bool = True


class MemoryWriteResult(BaseModel):
    success: bool
    event_ids: list[str] = Field(default_factory=list)
    graphiti_episode_ids: list[str] = Field(default_factory=list)
    history_index_updated: bool = False
    degraded: bool = False
    skipped_reason: str = ""
    error_kind: str = ""
    error_message: str = ""


class MemorySearchItem(BaseModel):
    text: str
    source: Literal["graphiti", "history_index", "working_memory"]
    source_ref: str = ""
    title: str = ""
    record_type: str = ""
    reason: str = ""
    score: float = 0.0
    confidence: str = "medium"
    created_at: str = ""


class MemorySearchResult(BaseModel):
    success: bool
    intent: str
    query: str
    repo_full_name: str = ""
    results: list[MemorySearchItem] = Field(default_factory=list)
    degraded: bool = False
    error_kind: str = ""


class TrackedPullRequestContext(BaseModel):
    schema_version: Literal["1"] = "1"
    repository: str
    number: int
    url: str = ""
    state: Literal["open", "closed", "merged"] = "open"
    lifecycle_status: str = "tracking"
    ci_status: str = "not_run"
    last_observed_at: str = ""
    next_poll_at: str = ""
    summary: str = ""
    detail_queries: list[MemoryHint] = Field(default_factory=list)


class MemoryWriteReport(BaseModel):
    graphiti_enabled: bool
    graphiti_available: bool
    events_written: int
    graphiti_episodes_written: int = 0
    memory_searches: int = 0
    graphiti_searches: int = 0
    history_index_searches: int = 0
    history_index_entries_written: int = 0
    degraded: bool = False
    failures: list[dict[str, str]] = Field(default_factory=list)
    indexed_sources: list[str] = Field(default_factory=list)
