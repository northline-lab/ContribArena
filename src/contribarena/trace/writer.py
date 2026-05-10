from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import TraceEvent


class TraceWriter:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def write(self, state: str, event: str, payload: dict[str, Any] | None = None) -> None:
        trace_event = TraceEvent(
            run_id=self.run_id,
            state=state,
            event=event,
            payload=payload or {},
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace_event.model_dump(mode="json"), ensure_ascii=True) + "\n")
