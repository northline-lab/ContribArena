from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from contribarena.memory.redact import redact_text
from contribarena.memory.schema import MemorySearchItem


TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".log",
    ".diff",
    ".patch",
}

TRACE_RECORD_TYPES = {"trace_event"}

INTENT_ALIASES = {
    "ci": "verification",
    "test": "verification",
    "tests": "verification",
    "verify": "verification",
    "verification": "verification",
    "guidance": "repo_context",
    "repo": "repo_context",
    "repo_context": "repo_context",
    "failure": "failure",
    "error": "failure",
    "tool_error": "failure",
    "provider_error": "failure",
    "external": "external_write",
    "external_write": "external_write",
    "lifecycle": "external_write",
    "pr": "external_write",
    "unknown": "unknown",
}

DEFAULT_PRIORITIES = {
    "quality_report": 95,
    "postmortem": 90,
    "memory_event": 85,
    "working_memory": 80,
    "repo_guidance": 75,
    "memory_context": 70,
    "verification": 65,
    "quality_gate": 60,
    "governance_decision": 55,
    "ci_status": 50,
    "live_action": 45,
    "review_event": 45,
    "tool_result": 35,
    "artifact": 25,
    "trace_event": 0,
}

INTENT_PRIORITIES = {
    "verification": {
        "verification": 100,
        "quality_report": 95,
        "ci_status": 90,
        "quality_gate": 80,
        "postmortem": 60,
        "artifact": 35,
    },
    "repo_context": {
        "repo_guidance": 100,
        "working_memory": 95,
        "memory_event": 90,
        "memory_context": 80,
        "quality_report": 55,
        "artifact": 35,
    },
    "failure": {
        "postmortem": 100,
        "quality_gate": 95,
        "quality_report": 85,
        "tool_result": 75,
        "verification": 70,
        "trace_event": 20,
        "artifact": 30,
    },
    "external_write": {
        "live_action": 100,
        "review_event": 95,
        "governance_decision": 90,
        "ci_status": 80,
        "repo_guidance": 45,
        "quality_report": 40,
        "artifact": 25,
    },
}


@dataclass
class HistoryIndexResult:
    entries_written: int = 0
    indexed_sources: list[str] = field(default_factory=list)


class HistoryIndex:
    def __init__(self, root: Path, max_record_chars: int = 16_000) -> None:
        self.db_path = root / "history" / "history.sqlite"
        self.max_record_chars = max_record_chars
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def index_run_dir(
        self,
        run_dir: Path,
        *,
        run_id: str,
        repo_full_name: str = "",
    ) -> HistoryIndexResult:
        result = HistoryIndexResult()
        if not run_dir.exists():
            return result
        with closing(sqlite3.connect(self.db_path)) as conn:
            for path in sorted(run_dir.iterdir()):
                if not path.is_file() or not _is_text_artifact(path):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                entries = _split_text(redact_text(text), self.max_record_chars)
                for index, chunk in enumerate(entries, start=1):
                    if not chunk.strip():
                        continue
                    record_id = _record_id(run_id, path.name, index)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO history_records
                        (record_id, run_id, repo_full_name, record_type, source_path,
                         source_line, text, tags_json, created_at, redacted)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record_id,
                            run_id,
                            repo_full_name,
                            _record_type(path),
                            path.name,
                            None if len(entries) == 1 else index,
                            chunk,
                            json.dumps([], ensure_ascii=True),
                            datetime.now(UTC).isoformat(),
                            1,
                        ),
                    )
                    conn.execute("DELETE FROM history_fts WHERE record_id = ?", (record_id,))
                    conn.execute(
                        """
                        INSERT INTO history_fts(record_id, text)
                        VALUES (?, ?)
                        """,
                        (record_id, chunk),
                    )
                    result.entries_written += 1
                if entries:
                    result.indexed_sources.append(path.name)
            conn.commit()
        return result

    def search(
        self,
        query: str,
        *,
        intent: str = "unknown",
        repo_full_name: str = "",
        limit: int = 5,
    ) -> list[MemorySearchItem]:
        terms = _query_terms(query)
        if not terms:
            return []
        match = " OR ".join(terms)
        resolved_intent = _resolve_intent(intent, query)
        params: list[object] = [match]
        repo_clause = ""
        if repo_full_name:
            repo_clause = "AND (h.repo_full_name = ? OR h.repo_full_name = '')"
            params.append(repo_full_name)
        params.append(max(limit * 6, limit))
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    f"""
                    SELECT h.text, h.source_path, h.record_type, h.created_at,
                           bm25(history_fts) AS score
                    FROM history_fts
                    JOIN history_records h ON h.record_id = history_fts.record_id
                    WHERE history_fts MATCH ? {repo_clause}
                    ORDER BY score
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        ranked_rows = sorted(
            (
                row
                for row in rows
                if _include_record_type(str(row["record_type"]), resolved_intent)
            ),
            key=lambda row: (
                -_record_priority(str(row["record_type"]), resolved_intent),
                float(row["score"] or 0.0),
                str(row["source_path"]),
            ),
        )
        return [
            MemorySearchItem(
                text=_snippet(str(row["text"])),
                source="history_index",
                source_ref=str(row["source_path"]),
                title=_title_for_record(str(row["source_path"]), str(row["record_type"])),
                record_type=str(row["record_type"]),
                reason=_reason_for_record(str(row["record_type"]), resolved_intent),
                score=float(row["score"] or 0.0),
                created_at=str(row["created_at"] or ""),
            )
            for row in ranked_rows[:limit]
        ]

    def _ensure_schema(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_records (
                    record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL DEFAULT '',
                    pr_number INTEGER,
                    record_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_line INTEGER,
                    text TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    redacted INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts
                USING fts5(record_id UNINDEXED, text)
                """
            )
            conn.commit()


def _is_text_artifact(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or "." not in path.name


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current and current_len + len(line) > max_chars:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line[:max_chars])
        current_len += min(len(line), max_chars)
    if current:
        chunks.append("".join(current))
    return chunks


def _record_type(path: Path) -> str:
    name = path.name
    if name == "trace.jsonl":
        return "trace_event"
    if name == "workspace_command.json":
        return "tool_result"
    if name == "quality_gate.json":
        return "quality_gate"
    if name == "quality_report.md":
        return "quality_report"
    if name == "test_log.txt":
        return "verification"
    if name == "ci_status.json":
        return "ci_status"
    if name == "live_action_log.jsonl":
        return "live_action"
    if name == "pr_review_log.jsonl":
        return "review_event"
    if name == "postmortem.md":
        return "postmortem"
    if name == "memory_events.jsonl":
        return "memory_event"
    if name == "working_memory.json":
        return "working_memory"
    if name == "memory_context.json":
        return "memory_context"
    if name == "repo_guidance.json":
        return "repo_guidance"
    if name == "governance_decision.json":
        return "governance_decision"
    return "artifact"


def _record_id(run_id: str, source: str, index: int) -> str:
    raw = f"{run_id}:{source}:{index}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _query_terms(query: str) -> list[str]:
    terms = []
    for raw in query.replace('"', " ").split():
        cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})
        if cleaned:
            terms.append(f'"{cleaned}"')
    return terms[:8]


def _resolve_intent(intent: str, query: str) -> str:
    normalized = INTENT_ALIASES.get(intent.strip().lower(), "unknown")
    if normalized != "unknown":
        return normalized
    lowered = query.lower()
    if any(term in lowered for term in ("test", "pytest", "verify", "ci", "compileall")):
        return "verification"
    if any(term in lowered for term in ("guidance", "contributing", "agents.md", "template")):
        return "repo_context"
    if any(term in lowered for term in ("fail", "error", "blocked", "exception", "warning")):
        return "failure"
    if any(term in lowered for term in ("pr", "push", "fork", "label", "lifecycle", "review")):
        return "external_write"
    return "unknown"


def _include_record_type(record_type: str, intent: str) -> bool:
    if record_type not in TRACE_RECORD_TYPES:
        return True
    return intent == "failure"


def _record_priority(record_type: str, intent: str) -> int:
    return INTENT_PRIORITIES.get(intent, {}).get(
        record_type,
        DEFAULT_PRIORITIES.get(record_type, DEFAULT_PRIORITIES["artifact"]),
    )


def _title_for_record(source_path: str, record_type: str) -> str:
    labels = {
        "quality_report": "Quality report",
        "postmortem": "Postmortem",
        "memory_event": "Memory event",
        "working_memory": "Working memory",
        "repo_guidance": "Repository guidance artifact",
        "memory_context": "Memory context",
        "verification": "Verification log",
        "quality_gate": "Quality gate",
        "governance_decision": "Governance decision",
        "ci_status": "CI status",
        "live_action": "Live action log",
        "review_event": "PR review log",
        "tool_result": "Tool result",
        "trace_event": "Trace event",
        "artifact": "Run artifact",
    }
    return f"{labels.get(record_type, 'Run artifact')}: {source_path}"


def _reason_for_record(record_type: str, intent: str) -> str:
    if intent == "verification":
        return f"matched verification query; {record_type} is a relevant evidence source"
    if intent == "repo_context":
        return f"matched repository-context query; {record_type} can carry guidance or memory facts"
    if intent == "failure":
        return f"matched failure query; {record_type} can explain prior failures or recovery"
    if intent == "external_write":
        return f"matched external-write query; {record_type} can describe PR, CI, or lifecycle state"
    return f"matched query; {record_type} is indexed run evidence"


def _snippet(text: str, limit: int = 800) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 24] + "\n[history result truncated]"
