from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from contribarena.config.schema import RunConfig, SeasonConfig, SeasonParticipantConfig
from contribarena.errors import ConfigError


SeasonStatus = Literal["draft", "active", "observing", "completed"]


@dataclass(frozen=True)
class SeasonAdmission:
    season_id: str = ""
    participant_id: str = ""
    participant: SeasonParticipantConfig | None = None
    ranked: bool = False
    wake_source: Literal["manual", "auto", "unranked"] = "unranked"


class SeasonStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_config(cls, config: RunConfig) -> SeasonStore:
        root = config.season.state_root if config.season and config.season.state_root else None
        if root is None:
            root = config.artifacts.output_root.parent / "seasons"
        return cls(root)

    def season_dir(self, season_id: str) -> Path:
        return self.root / season_id

    def config_path(self, season_id: str) -> Path:
        return self.season_dir(season_id) / "season_config.yaml"

    def state_path(self, season_id: str) -> Path:
        return self.season_dir(season_id) / "season_state.json"

    def participant_dir(self, season_id: str, participant_id: str) -> Path:
        return self.season_dir(season_id) / "participants" / participant_id

    def load(self, season_id: str, fallback: SeasonConfig | None = None) -> SeasonConfig:
        path = self.config_path(season_id)
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ConfigError(f"season config must be a mapping: {path}")
            config = SeasonConfig.model_validate(raw)
        elif fallback and fallback.id == season_id:
            config = fallback
        else:
            raise ConfigError(f"unknown_season: {season_id}")
        state = self._load_state(season_id)
        if state:
            status = str(state.get("status") or config.status)
            if status in {"draft", "active", "observing", "completed"}:
                config = config.model_copy(update={"status": status})
        return config

    def list(self, fallback: SeasonConfig | None = None) -> list[SeasonConfig]:
        seasons: dict[str, SeasonConfig] = {}
        if fallback:
            seasons[fallback.id] = self.load(fallback.id, fallback)
        if self.root.exists():
            for path in sorted(self.root.glob("*/season_config.yaml")):
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    config = SeasonConfig.model_validate(raw)
                    seasons[config.id] = self.load(config.id, config)
        return sorted(seasons.values(), key=lambda item: item.id)

    def write_config(self, config: SeasonConfig) -> Path:
        path = self.config_path(config.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        return path

    def transition(self, season_id: str, status: SeasonStatus, fallback: SeasonConfig | None = None) -> dict[str, Any]:
        config = self.load(season_id, fallback)
        state = self._load_state(season_id)
        now = datetime.now(UTC).isoformat()
        transitions = state.get("transitions", []) if isinstance(state.get("transitions"), list) else []
        transitions.append({"ts": now, "status": status})
        state = {
            "season_id": config.id,
            "name": config.name,
            "status": status,
            "updated_at": now,
            "transitions": transitions,
        }
        path = self.state_path(season_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return state

    def _load_state(self, season_id: str) -> dict[str, Any]:
        path = self.state_path(season_id)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid season state {path}: {exc}") from exc
        return raw if isinstance(raw, dict) else {}


def normalize_model_identity(raw: str) -> str:
    value = raw.strip().lower()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-")
    return value or "unknown"


def derive_participant_id(season_id: str, model: str) -> str:
    return f"{season_id}:{normalize_model_identity(model)}"


def participant_id_for(config: SeasonConfig, participant: SeasonParticipantConfig) -> str:
    return participant.id or derive_participant_id(config.id, participant.model)


def participant_dir_for_config(config: RunConfig) -> Path | None:
    if not config.run.season_id or not config.run.participant_id:
        return None
    return SeasonStore.from_config(config).participant_dir(
        config.run.season_id,
        config.run.participant_id,
    )


def participant_memory_root(config: RunConfig) -> Path | None:
    participant_dir = participant_dir_for_config(config)
    return participant_dir / "memory" if participant_dir is not None else None


def participant_goal_state_path(config: RunConfig) -> Path | None:
    participant_dir = participant_dir_for_config(config)
    return participant_dir / "goal_state.json" if participant_dir is not None else None


def participant_governance_state_path(config: RunConfig) -> Path | None:
    participant_dir = participant_dir_for_config(config)
    return participant_dir / "pr_history.json" if participant_dir is not None else None


def admit_run(config: RunConfig) -> SeasonAdmission:
    season_id = config.run.season_id
    if not season_id:
        return SeasonAdmission()
    store = SeasonStore.from_config(config)
    season = store.load(season_id, config.season)
    if season.status != "active":
        raise ConfigError(f"season_not_active: {season_id}")
    participant_id = config.run.participant_id or derive_participant_id(season_id, config.run.model)
    for participant in season.participants:
        if participant_id_for(season, participant) != participant_id:
            continue
        if "agent" not in participant.role:
            raise ConfigError(f"participant_not_agent: {participant_id}")
        store.participant_dir(season_id, participant_id).mkdir(parents=True, exist_ok=True)
        return SeasonAdmission(
            season_id=season_id,
            participant_id=participant_id,
            participant=participant,
            ranked=True,
            wake_source=config.run.wake_source if config.run.wake_source != "unranked" else "manual",
        )
    raise ConfigError(f"participant_not_in_allowlist: {participant_id}")
