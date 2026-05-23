from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class OperatorProgressWriter:
    """Writes concise, human-facing progress events."""

    def __init__(self, path: Path, run_id: str, *, stream: bool = False) -> None:
        self.path = path
        self.run_id = run_id
        self.stream = stream
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def write(
        self,
        phase: str,
        status: str,
        summary: str,
        *,
        source: str = "harness",
        evidence: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "source": source,
            "phase": phase,
            "status": status,
            "summary": truncate_for_operator(summary, 240),
            "evidence": _bounded_list(evidence or []),
            "payload": _bounded_payload(payload or {}),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        if self.stream:
            print(
                f"[{phase}] {status}: {summary}",
                file=sys.stderr,
                flush=True,
            )


def truncate_for_operator(value: object, max_chars: int = 180) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _bounded_list(values: list[object], max_items: int = 20) -> list[str]:
    return [truncate_for_operator(value, 180) for value in values[:max_items]]


def _bounded_payload(payload: dict[str, Any], max_items: int = 30) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for index, (key, value) in enumerate(payload.items()):
        if index >= max_items:
            bounded["truncated"] = True
            break
        bounded[str(key)] = _bounded_value(value)
    return bounded


def _bounded_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _bounded_payload(value, max_items=20)
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:20]]
    if isinstance(value, str):
        return truncate_for_operator(value, 240)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return truncate_for_operator(value, 240)
