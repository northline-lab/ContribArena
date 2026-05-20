from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contribarena.errors import InfrastructureError
from contribarena.models.surface import RunSummary


SURFACE_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class SurfaceIndexResult:
    input_dir: Path
    output_dir: Path
    runs_indexed: int
    files_written: list[Path]
    artifacts_copied: int
    skipped: list[str]


def index_surface_data(
    *,
    input_dir: Path,
    output_dir: Path,
    public_base_url: str = "",
) -> SurfaceIndexResult:
    """Build sanitized public surface JSON from run artifact summaries."""

    if not input_dir.exists():
        raise InfrastructureError(f"Surface input directory does not exist: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    loaded_runs, skipped = _load_run_summaries(input_dir=input_dir, output_dir=output_dir)
    loaded_runs = sorted(
        loaded_runs,
        key=lambda run: str(run.payload.get("started_at") or run.payload.get("run_id")),
        reverse=True,
    )

    artifact_files, copied_artifacts, artifact_skips = _copy_public_artifacts(
        loaded_runs,
        output_dir=output_dir,
    )
    skipped.extend(artifact_skips)
    public_runs = [
        _public_run(
            run.payload,
            copied_artifacts=copied_artifacts,
            public_base_url=public_base_url,
        )
        for run in loaded_runs
    ]
    leaderboard = _apply_frozen_leaderboard(
        input_dir,
        _leaderboard(public_runs),
    )
    stats = _stats(public_runs)
    seasons = _merge_season_state(input_dir, _seasons(public_runs))
    participants = _participants(public_runs)
    discovery_calls = _discovery_calls(loaded_runs)
    self_reviews = _self_reviews(loaded_runs)
    phase_history = _phase_history(loaded_runs)
    tool_violations = _tool_violations(loaded_runs)
    pr_lifecycle = _pr_lifecycle(loaded_runs)
    scheduler_events = _merge_runtime_events(input_dir, _scheduler_events(loaded_runs))
    season_workspaces = _merge_state_workspaces(input_dir, _season_workspaces(loaded_runs))
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    files_written: list[Path] = [*artifact_files]
    files_written.append(_write_json(output_dir / "runs.json", {"runs": public_runs}))
    files_written.append(_write_json(output_dir / "leaderboard.json", {"leaderboard": leaderboard}))
    files_written.append(_write_json(output_dir / "stats.json", stats))
    files_written.append(_write_json(output_dir / "seasons.json", {"seasons": seasons}))
    files_written.append(_write_json(output_dir / "participants.json", {"participants": participants}))
    files_written.append(_write_json(output_dir / "pr_lifecycle.json", {"pr_lifecycle": pr_lifecycle}))
    files_written.append(_write_json(output_dir / "discovery_calls.json", {"discovery": discovery_calls}))
    files_written.append(_write_json(output_dir / "scheduler_events.json", {"scheduler": scheduler_events}))
    files_written.append(_write_json(output_dir / "season_workspaces.json", {"workspaces": season_workspaces}))

    run_output_dir = output_dir / "runs"
    run_output_dir.mkdir(exist_ok=True)
    for run in public_runs:
        run_id = str(run["run_id"])
        run_payload = dict(run)
        run_payload["phase_history"] = phase_history.get(run_id, [])
        run_payload["tool_violations"] = tool_violations.get(run_id, [])
        run_payload["self_review"] = self_reviews.get(run_id, [])
        files_written.append(_write_json(run_output_dir / f"{run_id}.json", run_payload))
        files_written.append(
            _write_json(run_output_dir / f"{run_id}.discovery.json", {"discovery": discovery_calls.get(run_id, [])})
        )
        files_written.append(
            _write_json(run_output_dir / f"{run_id}.self_review.json", {"self_review": self_reviews.get(run_id, [])})
        )

    files_written.append(
        _write_json(
            output_dir / "surface.json",
            {
                "schema_version": SURFACE_SCHEMA_VERSION,
                "generated_at": generated_at,
                "stats": stats,
                "leaderboard": leaderboard,
                "runs": [
                    {
                        **run,
                        "phase_history": phase_history.get(str(run.get("run_id") or ""), []),
                        "tool_violations": tool_violations.get(str(run.get("run_id") or ""), []),
                        "self_review": self_reviews.get(str(run.get("run_id") or ""), []),
                    }
                    for run in public_runs
                ],
                "seasons": seasons,
                "participants": participants,
                "pr_lifecycle": pr_lifecycle,
                "discovery": discovery_calls,
                "scheduler": scheduler_events,
                "workspaces": season_workspaces,
                "skipped": skipped,
            },
        )
    )
    return SurfaceIndexResult(
        input_dir=input_dir,
        output_dir=output_dir,
        runs_indexed=len(public_runs),
        files_written=files_written,
        artifacts_copied=len(artifact_files),
        skipped=skipped,
    )


def build_leaderboard_snapshot(
    *,
    input_dir: Path,
    season_id: str,
) -> dict[str, Any]:
    """Build the immutable public leaderboard payload for a completed season."""

    loaded_runs, skipped = _load_run_summaries(input_dir=input_dir, output_dir=input_dir / ".snapshot")
    public_runs = [
        _public_run(run.payload, copied_artifacts=set(), public_base_url="")
        for run in loaded_runs
    ]
    scoped_runs = [
        run
        for run in public_runs
        if isinstance(run.get("season"), dict) and run["season"].get("id") == season_id
    ]
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "season_id": season_id,
        "generated_at": generated_at,
        "runs_count": len(scoped_runs),
        "leaderboard": _leaderboard(scoped_runs),
        "skipped": skipped,
    }


@dataclass(frozen=True)
class _LoadedRun:
    payload: dict[str, Any]
    run_dir: Path


def _load_run_summaries(
    *,
    input_dir: Path,
    output_dir: Path,
) -> tuple[list[_LoadedRun], list[str]]:
    runs: list[_LoadedRun] = []
    skipped: list[str] = []
    for path in sorted(input_dir.rglob("run_summary.json")):
        if _is_relative_to(path, output_dir):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{path}: {exc}")
            continue
        if not isinstance(payload, dict):
            skipped.append(f"{path}: run_summary.json must contain an object")
            continue
        runs.append(_LoadedRun(payload=_normalize_run_summary(payload), run_dir=path.parent))
    return runs, skipped


def _normalize_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("schema_version", SURFACE_SCHEMA_VERSION)
    normalized.setdefault("run_id", "")
    normalized.setdefault("run_mode", "")
    normalized.setdefault("model", "")
    normalized.setdefault("agent", {"name": "builtin", "handle": ""})
    normalized.setdefault("repository", {"full_name": "", "url": ""})
    normalized.setdefault("season", {"id": "", "name": "", "phase": "unknown"})
    normalized.setdefault("opportunity_source", "none")
    normalized.setdefault("opportunity_source_ref", "")
    normalized.setdefault("started_at", "")
    normalized.setdefault("completed_at", "")
    normalized.setdefault("duration_seconds", None)
    if normalized.get("duration_seconds") is None:
        normalized["duration_seconds"] = 0
    normalized.setdefault("run_status", "unknown")
    normalized.setdefault("terminal_reason", "")
    normalized.setdefault("terminal_layer", "")
    normalized.setdefault("contribution_class", "unknown")
    normalized.setdefault("quality_gate", {"status": "unknown", "warnings": []})
    normalized.setdefault("pull_request", {"url": "", "number": None, "state": "none"})
    normalized.setdefault(
        "maintainer_outcome",
        {"status": "pending", "observed_at": "", "source": "none"},
    )
    normalized.setdefault("judgement", {"status": "not_judged"})
    normalized["pipeline"] = [
        _normalize_stage(stage)
        for stage in normalized.get("pipeline", [])
        if isinstance(stage, dict)
    ]
    normalized["artifacts"] = [
        _normalize_artifact(artifact)
        for artifact in normalized.get("artifacts", [])
        if isinstance(artifact, dict)
    ]
    maintainer = normalized.get("maintainer_outcome")
    if isinstance(maintainer, dict) and maintainer.get("source") == "github_merge":
        maintainer["source"] = "github_pr_state"
    try:
        return RunSummary.model_validate(normalized).model_dump(mode="json")
    except ValueError:
        return normalized


def _normalize_stage(stage: dict[str, Any]) -> dict[str, Any]:
    stage = dict(stage)
    stage_id = stage.get("stage_id")
    if stage_id == "patch":
        stage["stage_id"] = "patch_diff"
    elif stage_id == "guidance":
        stage["stage_id"] = "repo_discovery"
    stage.setdefault("status", "unknown")
    stage.setdefault("started_at", "")
    stage.setdefault("completed_at", "")
    stage.setdefault("summary", "")
    stage.setdefault("source_artifacts", [])
    return stage


def _normalize_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact = dict(artifact)
    name = str(artifact.get("name") or "")
    artifact.setdefault("kind", _artifact_kind(name))
    artifact.setdefault("visibility", "internal")
    artifact.setdefault("url", "")
    artifact.setdefault("size_bytes", 0)
    artifact.setdefault("redacted", False)
    return artifact


def _artifact_kind(name: str) -> str:
    if name.endswith(".json"):
        return "json"
    if name.endswith(".jsonl"):
        return "jsonl"
    if name.endswith(".md"):
        return "markdown"
    if name.endswith(".diff") or name.endswith(".patch"):
        return "diff"
    return "artifact"


def _public_run(
    run: dict[str, Any],
    *,
    copied_artifacts: set[tuple[str, str]],
    public_base_url: str,
) -> dict[str, Any]:
    payload = dict(run)
    payload["artifacts"] = [
        _public_artifact(
            artifact,
            run_id=str(run.get("run_id") or ""),
            copied_artifacts=copied_artifacts,
            public_base_url=public_base_url,
        )
        for artifact in payload.get("artifacts", [])
        if isinstance(artifact, dict)
    ]
    return payload


def _public_artifact(
    artifact: dict[str, Any],
    *,
    run_id: str,
    copied_artifacts: set[tuple[str, str]],
    public_base_url: str,
) -> dict[str, Any]:
    artifact = dict(artifact)
    if artifact.get("visibility") != "public":
        artifact["url"] = ""
        return artifact
    name = str(artifact.get("name", ""))
    if (run_id, name) not in copied_artifacts:
        artifact["url"] = ""
        return artifact
    if public_base_url:
        base = public_base_url.rstrip("/")
        artifact["url"] = f"{base}/runs/{run_id}/artifacts/{name}"
    else:
        artifact["url"] = f"data/runs/{run_id}/artifacts/{name}"
    return artifact


def _copy_public_artifacts(
    runs: list[_LoadedRun],
    *,
    output_dir: Path,
) -> tuple[list[Path], set[tuple[str, str]], list[str]]:
    copied: list[Path] = []
    copied_keys: set[tuple[str, str]] = set()
    skipped: list[str] = []
    for loaded in runs:
        run_id = str(loaded.payload.get("run_id") or "")
        if not run_id:
            skipped.append(f"{loaded.run_dir}: missing run_id; public artifacts not copied")
            continue
        artifact_dir = output_dir / "runs" / run_id / "artifacts"
        for artifact in loaded.payload.get("artifacts", []):
            if not isinstance(artifact, dict) or artifact.get("visibility") != "public":
                continue
            name = str(artifact.get("name") or "")
            if not name or "/" in name or "\\" in name:
                skipped.append(f"{loaded.run_dir}: invalid public artifact name {name!r}")
                continue
            source = loaded.run_dir / name
            if not source.exists() or not source.is_file():
                skipped.append(f"{loaded.run_dir}: missing public artifact {name!r}")
                continue
            artifact_dir.mkdir(parents=True, exist_ok=True)
            target = artifact_dir / name
            shutil.copyfile(source, target)
            copied.append(target)
            copied_keys.add((run_id, name))
    return copied, copied_keys, skipped


def _leaderboard(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run in runs:
        agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        participant_id = str(agent.get("participant_id") or "")
        agent_handle = str(agent.get("handle") or agent.get("name") or "builtin")
        handle = participant_id or agent_handle
        season_id = str(season.get("id") or "")
        key = (season_id, handle, str(agent.get("name") or "builtin"))
        bucket = buckets.setdefault(
            key,
            {
                "season_id": season_id,
                "season_name": str(season.get("name") or ""),
                "season_phase": str(season.get("phase") or "unknown"),
                "participant_id": participant_id,
                "agent_name": str(agent.get("name") or "builtin"),
                "agent_handle": agent_handle,
                "runs": 0,
                "prs_opened": 0,
                "quality_gate_passed": 0,
                "reviewed_prs": 0,
                "merged_prs": 0,
                "judged_runs": 0,
                "judgement_fallback_runs": 0,
                "_judge_scores": [],
                "_arena_scores": [],
            },
        )
        bucket["runs"] += 1
        if _pr_opened(run):
            bucket["prs_opened"] += 1
        if _quality_gate_passed(run):
            bucket["quality_gate_passed"] += 1
        if _reviewed(run):
            bucket["reviewed_prs"] += 1
        if _merged(run):
            bucket["merged_prs"] += 1
        judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
        status = str(judgement.get("status") or "not_judged")
        if status in {"judged", "partial_fallback", "fallback"}:
            bucket["judged_runs"] += 1
        if status in {"partial_fallback", "fallback"}:
            bucket["judgement_fallback_runs"] += 1
        _append_float(bucket["_judge_scores"], judgement.get("judge_score"))
        _append_float(bucket["_arena_scores"], judgement.get("arena_score"))

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        runs_count = int(bucket["runs"])
        prs_opened = int(bucket["prs_opened"])
        row = {
            key: value
            for key, value in bucket.items()
            if key not in {"_judge_scores", "_arena_scores", "quality_gate_passed"}
        }
        row["quality_gate_pass_rate"] = _rate(int(bucket["quality_gate_passed"]), runs_count)
        row["merge_rate"] = _rate(int(bucket["merged_prs"]), prs_opened)
        row["mean_judge_score"] = _mean(bucket["_judge_scores"])
        row["mean_arena_score"] = _mean(bucket["_arena_scores"])
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row.get("mean_arena_score") is not None,
            row.get("mean_arena_score") or 0,
            row.get("merged_prs") or 0,
            row.get("quality_gate_pass_rate") or 0,
            row.get("runs") or 0,
        ),
        reverse=True,
    )


def _apply_frozen_leaderboard(input_dir: Path, live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = _leaderboard_snapshots(input_dir)
    if not snapshots:
        return live_rows
    rows: list[dict[str, Any]] = []
    covered_seasons = set(snapshots)
    for season_id in sorted(snapshots):
        rows.extend(snapshots[season_id])
    rows.extend(
        row
        for row in live_rows
        if str(row.get("season_id") or "") not in covered_seasons
    )
    return rows


def _leaderboard_snapshots(input_dir: Path) -> dict[str, list[dict[str, Any]]]:
    snapshots: dict[str, list[dict[str, Any]]] = {}
    roots = [input_dir / "seasons", input_dir.parent / "seasons"]
    seen: set[Path] = set()
    paths: list[Path] = []
    for root in roots:
        for path in sorted(root.glob("*/leaderboard_snapshot.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    for path in paths:
        payload = _read_json_file(path)
        season_id = str(payload.get("season_id") or path.parent.name)
        rows = payload.get("leaderboard", [])
        if season_id and isinstance(rows, list):
            snapshots[season_id] = [
                row
                for row in rows
                if isinstance(row, dict)
            ]
    return snapshots


def _seasons(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seasons: dict[str, dict[str, Any]] = {}
    participant_counts: dict[str, set[str]] = {}
    for run in runs:
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        if not season_id:
            continue
        payload = seasons.setdefault(
            season_id,
            {
                "id": season_id,
                "name": str(season.get("name") or season_id),
                "phase": str(season.get("phase") or "unknown"),
                "status": str(season.get("status") or "unknown"),
                "runs_count": 0,
                "participants_count": 0,
                "wake_sources": [],
            },
        )
        payload["runs_count"] = int(payload["runs_count"]) + 1
        wake_source = str(run.get("wake_source") or "")
        if wake_source and wake_source not in payload["wake_sources"]:
            payload["wake_sources"].append(wake_source)
        participant_id = str(agent.get("participant_id") or "")
        if participant_id:
            participant_counts.setdefault(season_id, set()).add(participant_id)
    for season_id, payload in seasons.items():
        payload["participants_count"] = len(participant_counts.get(season_id, set()))
    return sorted(seasons.values(), key=lambda row: str(row.get("id") or ""))


def _merge_season_state(input_dir: Path, seasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id") or ""): dict(item) for item in seasons}
    for state_path in _season_state_paths(input_dir):
        state = _read_json_file(state_path)
        season_id = str(state.get("season_id") or state_path.parent.name)
        if not season_id:
            continue
        payload = by_id.setdefault(
            season_id,
            {
                "id": season_id,
                "name": str(state.get("name") or season_id),
                "phase": "unknown",
                "runs_count": 0,
                "participants_count": 0,
                "wake_sources": [],
            },
        )
        payload.update(
            {
                "id": season_id,
                "name": str(state.get("name") or payload.get("name") or season_id),
                "status": str(state.get("status") or payload.get("status") or "unknown"),
                "paused": bool(state.get("paused", False)),
                "heartbeat": state.get("heartbeat", {}) if isinstance(state.get("heartbeat"), dict) else {},
                "transitions": state.get("transitions", []) if isinstance(state.get("transitions"), list) else [],
                "runtime_events": state.get("runtime_events", []) if isinstance(state.get("runtime_events"), list) else [],
                "updated_at": str(state.get("updated_at") or ""),
                "leaderboard_frozen": (state_path.parent / "leaderboard_snapshot.json").exists(),
            }
        )
    return sorted(by_id.values(), key=lambda row: str(row.get("id") or ""))


def _season_state_paths(input_dir: Path) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in (input_dir / "seasons", input_dir.parent / "seasons"):
        for path in sorted(root.glob("*/season_state.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return paths


def _participants(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants: dict[tuple[str, str], dict[str, Any]] = {}
    scores: dict[tuple[str, str], list[float]] = {}
    for run in runs:
        season = run.get("season", {}) if isinstance(run.get("season"), dict) else {}
        agent = run.get("agent", {}) if isinstance(run.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        participant_id = str(agent.get("participant_id") or "")
        if not season_id or not participant_id:
            continue
        key = (season_id, participant_id)
        payload = participants.setdefault(
            key,
            {
                "season_id": season_id,
                "participant_id": participant_id,
                "agent_name": str(agent.get("name") or "builtin"),
                "agent_handle": str(agent.get("handle") or participant_id),
                "runs_count": 0,
                "prs_opened": 0,
                "merged_prs": 0,
                "failures": 0,
                "last_run_at": "",
                "latest_run_id": "",
                "mean_arena_score": None,
            },
        )
        payload["runs_count"] = int(payload["runs_count"]) + 1
        if str(run.get("run_status") or "") != "completed":
            payload["failures"] = int(payload["failures"]) + 1
        if _pr_opened(run):
            payload["prs_opened"] = int(payload["prs_opened"]) + 1
        if _merged(run):
            payload["merged_prs"] = int(payload["merged_prs"]) + 1
        started_at = str(run.get("started_at") or "")
        if started_at >= str(payload.get("last_run_at") or ""):
            payload["last_run_at"] = started_at
            payload["latest_run_id"] = str(run.get("run_id") or "")
        judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
        scores.setdefault(key, [])
        _append_float(scores[key], judgement.get("arena_score"))
    for key, payload in participants.items():
        payload["mean_arena_score"] = _mean(scores.get(key, []))
    return sorted(participants.values(), key=lambda row: (str(row.get("season_id") or ""), str(row.get("participant_id") or "")))


def _stats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    prs_opened = sum(1 for run in runs if _pr_opened(run))
    judged_runs = sum(1 for run in runs if _judged(run))
    merged_prs = sum(1 for run in runs if _merged(run))
    quality_gate_passed = sum(1 for run in runs if _quality_gate_passed(run))
    seasons = {
        str(run.get("season", {}).get("id"))
        for run in runs
        if isinstance(run.get("season"), dict) and run.get("season", {}).get("id")
    }
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "runs": len(runs),
        "seasons": sorted(seasons),
        "prs_opened": prs_opened,
        "reviewed_prs": sum(1 for run in runs if _reviewed(run)),
        "merged_prs": merged_prs,
        "judged_runs": judged_runs,
        "quality_gate_pass_rate": _rate(quality_gate_passed, len(runs)),
        "merge_rate": _rate(merged_prs, prs_opened),
    }


def _pr_opened(run: dict[str, Any]) -> bool:
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    return bool(pr.get("url")) or pr.get("state") in {"open", "closed", "merged"}


def _quality_gate_passed(run: dict[str, Any]) -> bool:
    qg = run.get("quality_gate", {}) if isinstance(run.get("quality_gate"), dict) else {}
    return qg.get("status") == "pass"


def _reviewed(run: dict[str, Any]) -> bool:
    outcome = (
        run.get("maintainer_outcome", {})
        if isinstance(run.get("maintainer_outcome"), dict)
        else {}
    )
    return outcome.get("status") in {"reviewed", "changes_requested", "merged"}


def _merged(run: dict[str, Any]) -> bool:
    outcome = (
        run.get("maintainer_outcome", {})
        if isinstance(run.get("maintainer_outcome"), dict)
        else {}
    )
    pr = run.get("pull_request", {}) if isinstance(run.get("pull_request"), dict) else {}
    return outcome.get("status") == "merged" or pr.get("state") == "merged"


def _judged(run: dict[str, Any]) -> bool:
    judgement = run.get("judgement", {}) if isinstance(run.get("judgement"), dict) else {}
    return judgement.get("status") in {"judged", "partial_fallback", "fallback"}


def _discovery_calls(loaded_runs: list[_LoadedRun]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for loaded in loaded_runs:
        run_id = str(loaded.payload.get("run_id") or "")
        rows[run_id] = _read_jsonl(loaded.run_dir / "discovery_log.jsonl")
    return rows


def _self_reviews(loaded_runs: list[_LoadedRun]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for loaded in loaded_runs:
        run_id = str(loaded.payload.get("run_id") or "")
        rows[run_id] = _read_jsonl(loaded.run_dir / "phase_review_maintainer_review.jsonl")
    return rows


def _phase_history(loaded_runs: list[_LoadedRun]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for loaded in loaded_runs:
        run_id = str(loaded.payload.get("run_id") or "")
        rows[run_id] = _read_jsonl(loaded.run_dir / "phase_transition.jsonl")
    return rows


def _tool_violations(loaded_runs: list[_LoadedRun]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for loaded in loaded_runs:
        run_id = str(loaded.payload.get("run_id") or "")
        rows[run_id] = _read_jsonl(loaded.run_dir / "tool_violation_log.jsonl")
    return rows


def _pr_lifecycle(loaded_runs: list[_LoadedRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for loaded in loaded_runs:
        season = loaded.payload.get("season", {}) if isinstance(loaded.payload.get("season"), dict) else {}
        agent = loaded.payload.get("agent", {}) if isinstance(loaded.payload.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        participant_id = str(agent.get("participant_id") or "")
        added_for_run = False
        for payload in _read_jsonl(loaded.run_dir / "pr_review_log.jsonl"):
            item = dict(payload)
            item.setdefault("season_id", season_id)
            item.setdefault("participant_id", participant_id)
            rows.append(item)
            added_for_run = True
        if added_for_run:
            continue
        pr = loaded.payload.get("pull_request", {}) if isinstance(loaded.payload.get("pull_request"), dict) else {}
        repository = loaded.payload.get("repository", {}) if isinstance(loaded.payload.get("repository"), dict) else {}
        number = pr.get("number")
        if repository.get("full_name") and number is not None:
            rows.append(
                {
                    "season_id": season_id,
                    "participant_id": participant_id,
                    "repository": repository.get("full_name"),
                    "number": number,
                    "url": pr.get("url") or "",
                    "state": pr.get("state") or "unknown",
                    "run_id": loaded.payload.get("run_id") or "",
                }
            )
    return rows


def _scheduler_events(loaded_runs: list[_LoadedRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for loaded in loaded_runs:
        season = loaded.payload.get("season", {}) if isinstance(loaded.payload.get("season"), dict) else {}
        agent = loaded.payload.get("agent", {}) if isinstance(loaded.payload.get("agent"), dict) else {}
        season_id = str(season.get("id") or "")
        participant_id = str(agent.get("participant_id") or "")
        if not season_id or not participant_id:
            continue
        for payload in _read_jsonl(loaded.run_dir / "operator_events.jsonl"):
            if payload.get("phase") != "run" or payload.get("status") != "started":
                continue
            rows.append(
                {
                    "season_id": season_id,
                    "participant_id": participant_id,
                    "wake_source": loaded.payload.get("wake_source") or "",
                    "run_id": loaded.payload.get("run_id") or "",
                    "status": "started",
                    "created_at": payload.get("ts") or loaded.payload.get("started_at") or "",
                }
            )
    return rows


def _merge_runtime_events(input_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(rows)
    for state_path in _season_state_paths(input_dir):
        state = _read_json_file(state_path)
        season_id = str(state.get("season_id") or state_path.parent.name)
        for event in state.get("runtime_events", []) if isinstance(state.get("runtime_events"), list) else []:
            if not isinstance(event, dict):
                continue
            merged.append(
                {
                    **event,
                    "season_id": season_id,
                    "participant_id": str(event.get("participant_id") or ""),
                    "wake_source": str(event.get("wake_source") or ""),
                    "run_id": str(event.get("run_id") or ""),
                    "status": str(event.get("heartbeat_status") or event.get("event") or ""),
                    "created_at": str(event.get("ts") or ""),
                    "reason": str(event.get("detail") or event.get("reason") or event.get("error") or ""),
                }
            )
    return sorted(merged, key=lambda row: (str(row.get("created_at") or ""), str(row.get("participant_id") or "")))


def _season_workspaces(loaded_runs: list[_LoadedRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for loaded in loaded_runs:
        config = _read_json_file(loaded.run_dir / "config.json")
        workspace = config.get("workspace", {}) if isinstance(config.get("workspace"), dict) else {}
        metadata_path = workspace.get("persistent_metadata_path")
        if not metadata_path:
            summary_workspace = (
                loaded.payload.get("workspace", {})
                if isinstance(loaded.payload.get("workspace"), dict)
                else {}
            )
            metadata_path = summary_workspace.get("persistent_metadata_path")
        if not metadata_path:
            continue
        season = loaded.payload.get("season", {}) if isinstance(loaded.payload.get("season"), dict) else {}
        agent = loaded.payload.get("agent", {}) if isinstance(loaded.payload.get("agent"), dict) else {}
        repository = loaded.payload.get("repository", {}) if isinstance(loaded.payload.get("repository"), dict) else {}
        metadata = Path(str(metadata_path))
        container_id = ""
        if metadata.exists():
            container_id = metadata.read_text(encoding="utf-8", errors="replace").strip()
        rows.append(
            {
                "season_id": str(season.get("id") or ""),
                "participant_id": str(agent.get("participant_id") or ""),
                "repo_slug": str(repository.get("full_name") or ""),
                "container_id": container_id,
                "metadata_path": str(metadata),
                "last_used_at": _read_text_file(metadata.parent / "last_used_at"),
                "clone_state": _read_json_file(metadata.parent / "clone_state.json"),
            }
        )
    return rows


def _merge_state_workspaces(input_dir: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        (
            str(row.get("season_id") or ""),
            str(row.get("participant_id") or ""),
            str(row.get("repo_slug") or ""),
        ): dict(row)
        for row in rows
    }
    for state_path in _season_state_paths(input_dir):
        season_dir = state_path.parent
        season_id = season_dir.name
        for metadata in sorted(season_dir.glob("participants/*/workspaces/*/container_id")):
            participant_id = metadata.parents[2].name
            repo_slug = metadata.parent.name
            key = (season_id, participant_id, repo_slug)
            by_key[key] = {
                "season_id": season_id,
                "participant_id": participant_id,
                "repo_slug": repo_slug,
                "container_id": _read_text_file(metadata).strip(),
                "metadata_path": str(metadata),
                "last_used_at": _read_text_file(metadata.parent / "last_used_at"),
                "clone_state": _read_json_file(metadata.parent / "clone_state.json"),
                "workspace_status": "recorded",
            }
    return sorted(by_key.values(), key=lambda row: (str(row.get("season_id") or ""), str(row.get("participant_id") or ""), str(row.get("repo_slug") or "")))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _append_float(values: list[float], value: object) -> None:
    try:
        values.append(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
