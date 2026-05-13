from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class WorkingMemory(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    repo_full_name: str = ""
    facts: dict[str, WorkingMemoryFact] = Field(default_factory=dict)
    plan: list[WorkingMemoryPlanItem] = Field(default_factory=list)
    notes: list[WorkingMemoryNote] = Field(default_factory=list)
    truncated: bool = False


class MemoryContext(BaseModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    repo_full_name: str = ""
    enabled: bool = True
    degraded: bool = False
    backend: str = "noop"
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
    skipped_reason: str = ""
    error_kind: str = ""
    error_message: str = ""


class MemorySearchItem(BaseModel):
    text: str
    source: Literal["graphiti", "history_index", "working_memory"]
    source_ref: str = ""
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


class MemoryWriteReport(BaseModel):
    graphiti_enabled: bool
    graphiti_available: bool
    events_written: int
    graphiti_episodes_written: int = 0
    history_index_entries_written: int = 0
    degraded: bool = False
    failures: list[dict[str, str]] = Field(default_factory=list)
    indexed_sources: list[str] = Field(default_factory=list)
