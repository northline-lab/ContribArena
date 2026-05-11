from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contribarena.models.artifacts import ArtifactEntry, ArtifactManifest


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return cleaned.strip("-").lower() or "value"


class ArtifactWriter:
    def __init__(self, output_root: Path, run_id: str, repo_name: str, model: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.run_dir = output_root / f"{stamp}_{slugify(repo_name)}_{slugify(model)}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.run_id = run_id
        self.entries: list[ArtifactEntry] = []
        self._manifest_path: Path | None = None

    def write_json(self, name: str, payload: Any, required: bool = True) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        self._record_entry(ArtifactEntry(name=name, kind="json", required=required, path=name))
        return path

    def write_markdown(self, name: str, body: str, required: bool = True) -> Path:
        path = self.run_dir / name
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        self._record_entry(ArtifactEntry(name=name, kind="markdown", required=required, path=name))
        return path

    def write_text(
        self,
        name: str,
        body: str,
        *,
        kind: str = "text",
        required: bool = True,
    ) -> Path:
        path = self.run_dir / name
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        self._record_entry(ArtifactEntry(name=name, kind=kind, required=required, path=name))  # type: ignore[arg-type]
        return path

    def finalize_manifest(self) -> Path:
        if self._manifest_path is not None:
            return self._manifest_path
        entries = [
            *self.entries,
            ArtifactEntry(
                name="artifact_manifest.json",
                kind="json",
                required=True,
                path="artifact_manifest.json",
            ),
        ]
        manifest = ArtifactManifest(run_id=self.run_id, artifacts=entries)
        path = self.run_dir / "artifact_manifest.json"
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        self._manifest_path = path
        return path

    def _record_entry(self, entry: ArtifactEntry) -> None:
        self.entries = [item for item in self.entries if item.name != entry.name]
        self.entries.append(entry)
