from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from contribarena.memory._helpers import (
    DEFAULT_PRIORITIES,
    INTENT_ALIASES,
    INTENT_PRIORITIES,
    TEXT_SUFFIXES,
    TRACE_RECORD_TYPES,
    is_text_artifact as _is_text_artifact,
    include_record_type as _include_record_type,
    query_terms as _query_terms,
    record_id as _record_id,
    record_priority as _record_priority,
    record_type as _record_type,
    reason_for_record as _reason_for_record,
    snippet as _snippet,
    split_text as _split_text,
    title_for_record as _title_for_record,
    resolve_intent as _resolve_intent,
)
from contribarena.memory.redact import redact_text
from contribarena.memory.schema import MemorySearchItem


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
