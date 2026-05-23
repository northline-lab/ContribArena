from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str
    state: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
