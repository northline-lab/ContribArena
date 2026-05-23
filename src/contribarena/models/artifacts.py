from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ArtifactEntry(BaseModel):
    name: str
    kind: Literal["json", "jsonl", "markdown", "text", "diff"]
    required: bool = True
    path: str


class ArtifactManifest(BaseModel):
    run_id: str
    artifacts: list[ArtifactEntry]
