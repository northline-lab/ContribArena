from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

    def shared_dir(self, season_id: str) -> Path:
        return self.season_dir(season_id) / "shared"

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

    def transition(
        self,
        season_id: str,
        status: SeasonStatus,
        fallback: SeasonConfig | None = None,
        *,
        force_with_open_prs: bool = False,
    ) -> dict[str, Any]:
        config = self.load(season_id, fallback)
        if status == "completed" and not force_with_open_prs:
            open_prs = tracked_open_prs(self, season_id, config)
            if open_prs:
                refs = ", ".join(
                    f"{item.get('repository')}#{item.get('number')}" for item in open_prs[:5]
                )
                raise ConfigError(f"season_has_open_prs: {refs}")
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


def participant_state_path(config: RunConfig) -> Path | None:
    participant_dir = participant_dir_for_config(config)
    return participant_dir / "participant_state.json" if participant_dir is not None else None


def repo_workspace_dir(config: RunConfig, repo_slug: str) -> Path | None:
    participant_dir = participant_dir_for_config(config)
    if participant_dir is None:
        return None
    safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_slug).strip("-") or "repo"
    return participant_dir / "workspaces" / safe_repo


def workspace_key_for(config: RunConfig, repo_slug: str) -> str:
    if config.run.season_id and config.run.participant_id:
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_slug).strip("-") or "repo"
        return f"{config.run.season_id}-{config.run.participant_id}-{safe_repo}"
    return ""


def load_participant_state(store: SeasonStore, season_id: str, participant_id: str) -> dict[str, Any]:
    path = store.participant_dir(season_id, participant_id) / "participant_state.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid participant state {path}: {exc}") from exc
    return raw if isinstance(raw, dict) else {}


def shared_signals_for_config(config: RunConfig) -> dict[str, Any]:
    if not config.run.season_id:
        return {}
    store = SeasonStore.from_config(config)
    shared_dir = store.shared_dir(config.run.season_id)
    return {
        "repository_guidance": _read_shared_signal(shared_dir / "repository_guidance.json"),
        "maintainer_signals": _read_shared_signal(shared_dir / "maintainer_signals.json"),
    }


def _read_shared_signal(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid shared signal {path}: {exc}") from exc
    if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None:
        return payload
    return None


def save_participant_state(
    store: SeasonStore,
    season_id: str,
    participant_id: str,
    state: dict[str, Any],
) -> None:
    path = store.participant_dir(season_id, participant_id) / "participant_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tracked_open_prs(
    store: SeasonStore,
    season_id: str,
    fallback: SeasonConfig | None = None,
) -> list[dict[str, Any]]:
    season = store.load(season_id, fallback)
    rows: list[dict[str, Any]] = []
    active_statuses = {"tracking", "needs_response", "stale", "blocked"}
    for participant in season.participants:
        participant_id = participant_id_for(season, participant)
        path = store.participant_dir(season_id, participant_id) / "pr_history.json"
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid participant PR history {path}: {exc}") from exc
        if not isinstance(raw, dict):
            continue
        for record in raw.get("lifecycle_records", []):
            if not isinstance(record, dict):
                continue
            lifecycle_status = str(record.get("lifecycle_status") or "")
            state = str(record.get("state") or "")
            if lifecycle_status in active_statuses or state == "open":
                item = dict(record)
                item.setdefault("participant_id", participant_id)
                rows.append(item)
    return rows


def participant_max_concurrent(
    season: SeasonConfig,
    participant: SeasonParticipantConfig,
) -> int:
    return participant.max_concurrent_runs or season.defaults.max_concurrent_runs


def parse_duration_seconds(value: str) -> int:
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smhd])", text)
    if match is None:
        raise ConfigError(f"invalid duration: {value}")
    amount = int(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return amount * multiplier


def participant_is_due(
    *,
    season: SeasonConfig,
    participant: SeasonParticipantConfig,
    state: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    last_wake_at = str(state.get("last_wake_at") or "")
    if not last_wake_at:
        return True
    try:
        last = datetime.fromisoformat(last_wake_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    interval = parse_duration_seconds(participant.wake_interval or season.defaults.wake_interval)
    return (now or datetime.now(UTC)) - last >= timedelta(seconds=interval)


def mark_participant_run_started(
    store: SeasonStore,
    season_id: str,
    participant_id: str,
    *,
    repo_slug: str,
    wake_source: str,
    increment_active: bool = True,
) -> dict[str, Any]:
    state = load_participant_state(store, season_id, participant_id)
    now = datetime.now(UTC).isoformat()
    state.update(
        {
            "season_id": season_id,
            "participant_id": participant_id,
            "last_wake_at": now,
            "last_run_started_at": now,
            "last_repo_slug": repo_slug,
            "last_wake_source": wake_source,
            "active_runs": int(state.get("active_runs") or 0) + (1 if increment_active else 0),
        }
    )
    save_participant_state(store, season_id, participant_id, state)
    return state


def mark_participant_run_finished(
    config: RunConfig,
    *,
    run_id: str,
    status: str,
    repo_slug: str,
    latest_goal_summary: str = "",
) -> None:
    path = participant_state_path(config)
    if path is None or not config.run.season_id or not config.run.participant_id:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        raw = {}
    state = raw if isinstance(raw, dict) else {}
    now = datetime.now(UTC).isoformat()
    active_runs = max(0, int(state.get("active_runs") or 0) - 1)
    pr_counts = _participant_pr_counts(path.parent / "pr_history.json")
    state.update(
        {
            "season_id": config.run.season_id,
            "participant_id": config.run.participant_id,
            "last_run_id": run_id,
            "last_run_status": status,
            "last_run_at": now,
            "last_repo_slug": repo_slug,
            "active_runs": active_runs,
            "runs_count": int(state.get("runs_count") or 0) + 1,
            "failures": int(state.get("failures") or 0) + (0 if status == "completed" else 1),
            "prs_opened": pr_counts["prs_opened"],
            "merged_prs": pr_counts["merged_prs"],
        }
    )
    if latest_goal_summary:
        state["latest_goal_summary"] = latest_goal_summary[:1000]
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _participant_pr_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"prs_opened": 0, "merged_prs": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"prs_opened": 0, "merged_prs": 0}
    if not isinstance(raw, dict):
        return {"prs_opened": 0, "merged_prs": 0}
    refs: set[tuple[str, int]] = set()
    merged: set[tuple[str, int]] = set()
    for key in ("pull_requests", "lifecycle_records"):
        records = raw.get(key, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            repository = str(record.get("repository") or "")
            try:
                number = int(record.get("number"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if not repository:
                continue
            ref = (repository, number)
            refs.add(ref)
            if record.get("state") == "merged" or record.get("lifecycle_status") == "merged":
                merged.add(ref)
    return {"prs_opened": len(refs), "merged_prs": len(merged)}


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
        if config.run.wake_source == "manual":
            state = load_participant_state(store, season_id, participant_id)
            if int(state.get("active_runs") or 0) >= participant_max_concurrent(season, participant):
                raise ConfigError(f"participant_at_concurrency_limit: {participant_id}")
        store.participant_dir(season_id, participant_id).mkdir(parents=True, exist_ok=True)
        return SeasonAdmission(
            season_id=season_id,
            participant_id=participant_id,
            participant=participant,
            ranked=True,
            wake_source=config.run.wake_source if config.run.wake_source != "unranked" else "manual",
        )
    raise ConfigError(f"participant_not_in_allowlist: {participant_id}")


def cleanup_season_workspaces(store: SeasonStore, season_id: str, fallback: SeasonConfig | None = None) -> list[dict[str, Any]]:
    season = store.load(season_id, fallback)
    results: list[dict[str, Any]] = []
    for participant in season.participants:
        participant_id = participant_id_for(season, participant)
        workspaces = store.participant_dir(season_id, participant_id) / "workspaces"
        if workspaces.exists():
            for metadata in sorted(workspaces.glob("*/container_id")):
                container = metadata.read_text(encoding="utf-8").strip()
                if not container:
                    continue
                completed = subprocess.run(
                    ["docker", "rm", "-f", container],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                results.append(
                    {
                        "participant_id": participant_id,
                        "workspace": str(metadata.parent),
                        "container": container,
                        "exit_code": completed.returncode,
                    }
                )
            shutil.rmtree(workspaces, ignore_errors=True)
        memory = store.participant_dir(season_id, participant_id) / "memory"
        shutil.rmtree(memory, ignore_errors=True)
    return results
