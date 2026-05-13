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
        repo_full_name: str = "",
        limit: int = 5,
    ) -> list[MemorySearchItem]:
        terms = _query_terms(query)
        if not terms:
            return []
        match = " OR ".join(terms)
        params: list[object] = [match]
        repo_clause = ""
        if repo_full_name:
            repo_clause = "AND (h.repo_full_name = ? OR h.repo_full_name = '')"
            params.append(repo_full_name)
        params.append(limit)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    f"""
                    SELECT h.text, h.source_path, h.created_at, bm25(history_fts) AS score
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
        return [
            MemorySearchItem(
                text=_snippet(str(row["text"])),
                source="history_index",
                source_ref=str(row["source_path"]),
                score=float(row["score"] or 0.0),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
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
    if name == "test_log.txt":
        return "verification"
    if name == "live_action_log.jsonl":
        return "live_action"
    if name == "pr_review_log.jsonl":
        return "review_event"
    if name == "postmortem.md":
        return "postmortem"
    if name == "memory_events.jsonl":
        return "memory_event"
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


def _snippet(text: str, limit: int = 800) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 24] + "\n[history result truncated]"
