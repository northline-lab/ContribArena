from __future__ import annotations

import json
from pathlib import Path

from contribarena.memory.schema import MemoryEvent


class MemoryEventLog:
    def __init__(self, root: Path, run_id: str) -> None:
        self.path = root / "events" / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: MemoryEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def read_events(self) -> list[MemoryEvent]:
        if not self.path.exists():
            return []
        events: list[MemoryEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(MemoryEvent.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return events

    def read_text(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")
