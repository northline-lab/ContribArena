from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from contribarena.config.schema import MemoryConfig
from contribarena.memory.event_log import MemoryEventLog
from contribarena.memory.graphiti_backend import (
    GraphitiBackend,
    GraphitiSearchResult,
    GraphitiWriteResult,
    make_graphiti_backend,
)
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.redact import redact_payload, redact_text
from contribarena.memory.schema import (
    MemoryContext,
    MemoryEvent,
    MemoryHint,
    MemorySearchItem,
    MemorySearchResult,
    MemoryWriteReport,
    MemoryWriteResult,
    WorkingMemory,
    WorkingMemoryFact,
    WorkingMemoryNote,
    WorkingMemoryPlanItem,
)


class MemoryService:
    def __init__(
        self,
        config: MemoryConfig,
        *,
        run_id: str,
        repo_full_name: str = "",
        graphiti_backend: GraphitiBackend | None = None,
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.repo_full_name = repo_full_name
        self.working = WorkingMemory(run_id=run_id, repo_full_name=repo_full_name)
        self.context = MemoryContext(
            run_id=run_id,
            repo_full_name=repo_full_name,
            enabled=config.enabled,
            backend=config.backend,
        )
        self._event_log = MemoryEventLog(config.root, run_id) if config.enabled else None
        self._history = (
            HistoryIndex(config.root, config.max_history_record_chars)
            if config.enabled and config.history_index_enabled
            else None
        )
        self._graphiti = (
            graphiti_backend
            if graphiti_backend is not None
            else make_graphiti_backend(config)
        )
        self._failures: list[dict[str, str]] = []
        self._last_index_entries = 0
        self._last_indexed_sources: list[str] = []
        self._graphiti_episode_ids: list[str] = []
        self._graphiti_available = bool(self._graphiti)
        self._memory_searches = 0
        self._graphiti_searches = 0
        self._history_index_searches = 0
        repo_memory_enabled = config.enabled and config.backend == "graphiti" and config.graphiti_enabled
        self.working.memory_capabilities.repo_scope_persistent = repo_memory_enabled
        self.context.memory_capabilities.repo_scope_persistent = repo_memory_enabled
        if repo_memory_enabled:
            note = (
                "scope='repo' notes are written to Graphiti-backed L2 memory when available; "
                "scope='global' notes remain event-log-only."
            )
            self.working.memory_capabilities.note = note
            self.context.memory_capabilities.note = note

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def start_run_context(self, repo_full_name: str = "") -> MemoryContext:
        if repo_full_name:
            self.repo_full_name = repo_full_name
            self.working.repo_full_name = repo_full_name
            self.context.repo_full_name = repo_full_name
        if not self.enabled:
            self.context.enabled = False
            self.context.notes = ["memory disabled"]
            return self.context
        self.context.history_results = []
        if self._graphiti is not None and self.repo_full_name:
            graphiti_context = self._search_graphiti_repo(
                f"prior run lessons for {self.repo_full_name}: guidance conventions verification",
                5,
            )
            self.context.history_results.extend(graphiti_context.results)
        if self._history is not None:
            try:
                self._history_index_searches += 1
                self.context.history_results = self._history.search(
                    "terminal failed blocked guidance verification",
                    repo_full_name=self.repo_full_name,
                    limit=5,
                ) + self.context.history_results
            except Exception as exc:  # pragma: no cover - defensive fail-soft path
                self.context.degraded = True
                self.context.notes.append("history index unavailable")
                self._failures.append(
                    {"error_kind": "history_index_unavailable", "error_message": str(exc)}
                )
        self.context.memory_hints = _memory_hints_from_history(self.context.history_results)
        self.working.memory_hints = list(self.context.memory_hints)
        return self.context

    def set_guidance_status(
        self,
        *,
        available: bool,
        skipped_reason: str = "",
        error: str = "",
    ) -> None:
        self.working.guidance.available = available
        self.working.guidance.skipped_reason = skipped_reason
        self.working.guidance.error = error
        self.context.guidance.available = available
        self.context.guidance.skipped_reason = skipped_reason
        self.context.guidance.error = error

    def record_tool_observation(
        self,
        step_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> MemoryWriteResult:
        if not self.enabled:
            return _skipped("memory_disabled")
        facts = _derive_facts(tool_name, payload, result)
        for key, value in facts.items():
            self._set_fact(
                key,
                value,
                source="harness",
                derived_from=f"{tool_name} step={step_id}",
            )
        if facts and self.repo_full_name:
            self._write_repo_episode(
                event_type="harness_repo_fact_observed",
                payload={
                    "text": _repo_fact_text(tool_name, facts),
                    "tool": tool_name,
                    "facts": facts,
                },
                source_ref=f"{tool_name} step={step_id}",
                confidence="medium",
            )
        return MemoryWriteResult(success=True)

    def note_agent_memory(
        self,
        scope: str,
        text: str,
        tags: list[str],
        confidence: str,
        source_ref: str,
    ) -> MemoryWriteResult:
        if not self.enabled:
            return _skipped("memory_disabled")
        now = _now()
        clean_text = redact_text(text, max_chars=2048)
        clean_tags = [redact_text(str(tag), max_chars=64) for tag in tags[:8]]
        if scope == "run":
            note = WorkingMemoryNote(id=uuid.uuid4().hex[:8], text=clean_text, tags=clean_tags, created_at=now)
            if len(self.working.notes) < 32:
                self.working.notes.append(note)
            else:
                self.working.truncated = True
            key = _fact_key(clean_tags[0] if clean_tags else "agent_note")
            self._set_fact(key, clean_text, source="agent", derived_from=source_ref)
        event_id = self._append_event(
            "agent_lesson_proposed",
            payload={"scope": scope, "text": clean_text, "tags": clean_tags},
            source_ref=source_ref,
            confidence=_confidence(confidence),
        )
        if scope == "repo":
            if self._graphiti is None:
                return MemoryWriteResult(
                    success=True,
                    event_ids=[event_id] if event_id else [],
                    degraded=True,
                    skipped_reason="graphiti_disabled",
                    error_kind="graphiti_disabled",
                    error_message=(
                        "Repo memory was written to the local memory event log, but "
                        "Graphiti-backed L2 retrieval is not enabled."
                    ),
                )
            graphiti_result = self._write_graphiti_repo_episode(
                event_id=event_id,
                event_type="agent_lesson_proposed",
                payload={"text": clean_text, "tags": clean_tags, "scope": scope},
                source_ref=source_ref,
                confidence=_confidence(confidence),
            )
            return MemoryWriteResult(
                success=True,
                event_ids=[event_id] if event_id else [],
                graphiti_episode_ids=graphiti_result.episode_ids,
                degraded=graphiti_result.degraded,
                error_kind=graphiti_result.error_kind,
                error_message=graphiti_result.error_message,
            )
        if scope != "run":
            return MemoryWriteResult(
                success=True,
                event_ids=[event_id] if event_id else [],
                degraded=True,
                skipped_reason="l2_l3_not_implemented",
                error_kind="l2_l3_not_implemented",
                error_message=(
                    "M0.6.1 only updates run-local working memory; repo/global notes "
                    "are written to the memory event log but are not yet retrievable in "
                    "future runs."
                ),
            )
        return MemoryWriteResult(success=True, event_ids=[event_id] if event_id else [])

    def plan_update(
        self,
        action: str,
        item_id: str = "",
        text: str = "",
        status: str = "",
    ) -> MemoryWriteResult:
        if not self.enabled:
            return _skipped("memory_disabled")
        now = _now()
        action = action.strip().lower()
        if action == "add":
            item = WorkingMemoryPlanItem(
                id=item_id.strip() or uuid.uuid4().hex[:8],
                text=redact_text(text, max_chars=512),
                created_at=now,
                updated_at=now,
            )
            if len(self.working.plan) < 32:
                self.working.plan.append(item)
            else:
                self.working.truncated = True
        elif action == "update":
            for item in self.working.plan:
                if item.id == item_id:
                    if text.strip():
                        item.text = redact_text(text, max_chars=512)
                    if status in {"open", "doing", "done", "dropped"}:
                        item.status = status  # type: ignore[assignment]
                    item.updated_at = now
                    break
        else:
            return MemoryWriteResult(
                success=False,
                error_kind="invalid_memory_plan_action",
                error_message="action must be add or update",
            )
        return MemoryWriteResult(success=True)

    def search_for_agent(
        self,
        query: str,
        intent: str = "unknown",
        max_results: int = 5,
    ) -> MemorySearchResult:
        if not self.enabled:
            return MemorySearchResult(
                success=False,
                intent=intent,
                query=query,
                repo_full_name=self.repo_full_name,
                error_kind="memory_disabled",
            )
        self._memory_searches += 1
        results = _search_working_memory(self.working, query, max_results)
        degraded = False
        error_kind = ""
        if self._graphiti is not None and intent in {"repo_context", "guidance_check"}:
            graphiti_result = self._search_graphiti_repo(query, max_results - len(results))
            results.extend(graphiti_result.results)
            if graphiti_result.degraded:
                degraded = True
                error_kind = graphiti_result.error_kind
        if self._history is not None and len(results) < max_results:
            try:
                self._history_index_searches += 1
                results.extend(
                    self._history.search(
                        query,
                        intent=intent,
                        repo_full_name=self.repo_full_name,
                        limit=max_results - len(results),
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive fail-soft path
                self._failures.append(
                    {"error_kind": "history_index_search_failed", "error_message": str(exc)}
                )
                return MemorySearchResult(
                    success=True,
                    intent=intent,
                    query=query,
                    repo_full_name=self.repo_full_name,
                    results=results,
                    degraded=True,
                    error_kind="history_index_search_failed",
                )
        return MemorySearchResult(
            success=True,
            intent=intent,
            query=query,
            repo_full_name=self.repo_full_name,
            results=results[:max_results],
            degraded=degraded,
            error_kind=error_kind,
        )

    def finalize_run(self, terminal: Mapping[str, Any], run_dir: Path) -> MemoryWriteReport:
        if not self.enabled:
            return MemoryWriteReport(
                graphiti_enabled=False,
                graphiti_available=False,
                events_written=0,
                degraded=False,
            )
        self._append_event(
            "run_terminal",
            payload={"terminal": redact_payload(dict(terminal))},
            source_ref="terminal_state.json",
        )
        if self._history is not None:
            try:
                indexed = self._history.index_run_dir(
                    run_dir,
                    run_id=self.run_id,
                    repo_full_name=self.repo_full_name,
                )
                self._last_index_entries = indexed.entries_written
                self._last_indexed_sources = indexed.indexed_sources
            except Exception as exc:  # pragma: no cover - defensive fail-soft path
                self._failures.append(
                    {"error_kind": "history_index_write_failed", "error_message": str(exc)}
                )
        self._close_graphiti()
        return MemoryWriteReport(
            graphiti_enabled=self.config.graphiti_enabled,
            graphiti_available=self._graphiti_available,
            events_written=len(self.events_text().splitlines()),
            graphiti_episodes_written=len(self._graphiti_episode_ids),
            memory_searches=self._memory_searches,
            graphiti_searches=self._graphiti_searches,
            history_index_searches=self._history_index_searches,
            history_index_entries_written=self._last_index_entries,
            degraded=bool(self._failures),
            failures=self._failures,
            indexed_sources=self._last_indexed_sources,
        )

    def events_text(self) -> str:
        if self._event_log is None:
            return ""
        return self._event_log.read_text()

    def _set_fact(
        self,
        key: str,
        value: str,
        *,
        source: str,
        derived_from: str = "",
    ) -> None:
        clean_key = _fact_key(key)
        if clean_key in self.working.facts and self.working.facts[clean_key].source == "agent":
            return
        if clean_key not in self.working.facts and len(self.working.facts) >= 64:
            self.working.truncated = True
            return
        now = _now()
        existing = self.working.facts.get(clean_key)
        created_at = existing.created_at if existing else now
        self.working.facts[clean_key] = WorkingMemoryFact(
            key=clean_key,
            value=redact_text(str(value), max_chars=1024),
            source="agent" if source == "agent" else "harness",
            derived_from=derived_from,
            created_at=created_at,
            updated_at=now,
        )

    def _append_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        source_ref: str = "",
        confidence: str = "medium",
    ) -> str:
        if self._event_log is None:
            return ""
        event = MemoryEvent(
            event_id=uuid.uuid4().hex[:12],
            event_type=event_type,
            run_id=self.run_id,
            repo_full_name=self.repo_full_name,
            source_ref=source_ref,
            payload=redact_payload(payload),
            confidence=_confidence(confidence),
            created_at=_now(),
        )
        self._event_log.append(event)
        return event.event_id

    def _write_repo_episode(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        source_ref: str,
        confidence: str,
    ) -> MemoryWriteResult:
        event_id = self._append_event(
            event_type,
            payload=payload,
            source_ref=source_ref,
            confidence=confidence,
        )
        if self._graphiti is None:
            return MemoryWriteResult(
                success=True,
                event_ids=[event_id] if event_id else [],
                degraded=True,
                skipped_reason="graphiti_disabled",
                error_kind="graphiti_disabled",
                error_message="Repo memory event was written locally; Graphiti-backed L2 is not enabled.",
            )
        graphiti_result = self._write_graphiti_repo_episode(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            source_ref=source_ref,
            confidence=confidence,
        )
        return MemoryWriteResult(
            success=True,
            event_ids=[event_id] if event_id else [],
            graphiti_episode_ids=graphiti_result.episode_ids,
            degraded=graphiti_result.degraded,
            error_kind=graphiti_result.error_kind,
            error_message=graphiti_result.error_message,
        )

    def _write_graphiti_repo_episode(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        source_ref: str,
        confidence: str,
    ) -> GraphitiWriteResult:
        if self._graphiti is None:
            return GraphitiWriteResult(
                success=False,
                degraded=True,
                error_kind="graphiti_disabled",
                error_message="Graphiti-backed L2 retrieval is not enabled.",
            )
        graphiti_result = self._graphiti.add_repo_episode(
            repo_full_name=self.repo_full_name,
            run_id=self.run_id,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            source_ref=source_ref,
            confidence=confidence,
        )
        self._record_graphiti_write_result(graphiti_result.error_kind, graphiti_result.error_message)
        self._graphiti_episode_ids.extend(graphiti_result.episode_ids)
        return graphiti_result

    def _record_graphiti_write_result(self, error_kind: str, error_message: str) -> None:
        if not error_kind:
            self._graphiti_available = True
            return
        self._graphiti_available = False
        self._failures.append({"error_kind": error_kind, "error_message": error_message})

    def _search_graphiti_repo(self, query: str, limit: int) -> GraphitiSearchResult:
        if self._graphiti is None or limit <= 0:
            return GraphitiSearchResult(success=True, results=[])
        self._graphiti_searches += 1
        result = self._graphiti.search_repo(
            repo_full_name=self.repo_full_name,
            query=query,
            limit=limit,
        )
        if result.degraded:
            self._graphiti_available = False
            self._failures.append(
                {"error_kind": result.error_kind, "error_message": result.error_message}
            )
        else:
            self._graphiti_available = True
        return result

    def _close_graphiti(self) -> None:
        close = getattr(self._graphiti, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as exc:  # pragma: no cover - memory cleanup must fail soft
            self._failures.append(
                {"error_kind": "graphiti_close_failed", "error_message": redact_text(str(exc))}
            )


def _derive_facts(
    tool_name: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, str]:
    if not bool(result.get("success", True)):
        return {}
    facts: dict[str, str] = {}
    path = str(payload.get("path") or "")
    if tool_name == "aci_view":
        lowered = path.lower()
        if re.search(r"contributing(\.md)?$", lowered):
            facts["contributing_checked"] = path
        if "readme" in lowered:
            facts["readme_checked"] = path
        if ".github/" in lowered and (
            "pull_request_template" in lowered or "issue_template" in lowered
        ):
            facts["pr_template_seen"] = path
    if tool_name == "aci_find_files":
        pattern = str(payload.get("pattern") or "")
        if "test" in pattern.lower() or "tests/" in str(payload.get("path") or ""):
            facts["tests_dir_explored"] = pattern
    if tool_name == "aci_search":
        pattern = str(payload.get("pattern") or "")
        if pattern:
            facts["search_used"] = pattern
    if tool_name == "aci_verify":
        command = str(payload.get("command") or "")
        if command:
            facts["last_verification"] = command
    if tool_name == "aci_submit_patch":
        facts["patch_submitted"] = "true"
    if tool_name == "aci_apply_patch":
        paths = payload.get("paths")
        if isinstance(paths, list) and paths:
            facts["last_edit_paths"] = ", ".join(str(item) for item in paths[:5])
    return facts


def _repo_fact_text(tool_name: str, facts: dict[str, str]) -> str:
    pairs = "; ".join(f"{key}={value}" for key, value in sorted(facts.items()))
    return f"Harness observed repository facts via {tool_name}: {pairs}"


def _search_working_memory(
    working: WorkingMemory,
    query: str,
    max_results: int,
) -> list[MemorySearchItem]:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_/-]+", query)]
    if not terms:
        return []
    results: list[MemorySearchItem] = []
    for fact in working.facts.values():
        haystack = f"{fact.key} {fact.value}".lower()
        if any(term in haystack for term in terms):
            results.append(
                MemorySearchItem(
                    text=f"{fact.key}: {fact.value}",
                    source="working_memory",
                    source_ref=fact.derived_from,
                    created_at=fact.updated_at,
                )
            )
    for note in working.notes:
        haystack = f"{note.text} {' '.join(note.tags)}".lower()
        if any(term in haystack for term in terms):
            results.append(
                MemorySearchItem(
                    text=note.text,
                    source="working_memory",
                    source_ref="working_memory.notes",
                    created_at=note.created_at,
                )
            )
    return results[:max_results]


def _memory_hints_from_history(results: list[MemorySearchItem]) -> list[MemoryHint]:
    hints: list[MemoryHint] = []
    seen: set[str] = set()
    for item in results:
        category = _hint_category(item.record_type)
        if category is None or category in seen:
            continue
        seen.add(category)
        hints.append(
            MemoryHint(
                category=category,
                summary_line=_hint_summary(category, item),
                suggested_query=_hint_query(category),
                suggested_intent=category,
            )
        )
        if len(hints) >= 5:
            break
    return hints


def _hint_category(record_type: str) -> str | None:
    if record_type in {
        "repo_guidance",
        "working_memory",
        "memory_event",
        "memory_context",
        "repo_memory",
    }:
        return "repo_context"
    if record_type in {"verification", "quality_report", "ci_status"}:
        return "verification"
    if record_type in {"postmortem", "quality_gate", "tool_result", "trace_event"}:
        return "failure"
    if record_type in {"live_action", "review_event", "governance_decision"}:
        return "external_write"
    return None


def _hint_summary(category: str, item: MemorySearchItem) -> str:
    labels = {
        "repo_context": "Prior repository-context evidence is available",
        "verification": "Prior verification evidence is available",
        "failure": "Prior failure or recovery evidence is available",
        "external_write": "Prior external-write or lifecycle evidence is available",
    }
    source = item.source_ref or item.record_type or "history"
    return f"{labels[category]} from {source}."


def _hint_query(category: str) -> str:
    queries = {
        "repo_context": "repository guidance contribution conventions verification",
        "verification": "verification test command quality report",
        "failure": "failure blocker recovery verification error",
        "external_write": "pull request lifecycle CI review live action",
    }
    return queries[category]


def _fact_key(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return (cleaned or "memory_note")[:64]


def _confidence(value: str) -> str:
    return value if value in {"low", "medium", "high"} else "medium"


def _skipped(reason: str) -> MemoryWriteResult:
    return MemoryWriteResult(success=False, skipped_reason=reason, error_kind=reason)


def _now() -> str:
    return datetime.now(UTC).isoformat()
