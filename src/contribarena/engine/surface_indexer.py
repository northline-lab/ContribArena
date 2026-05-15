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
    leaderboard = _leaderboard(public_runs)
    stats = _stats(public_runs)
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    files_written: list[Path] = [*artifact_files]
    files_written.append(_write_json(output_dir / "runs.json", {"runs": public_runs}))
    files_written.append(_write_json(output_dir / "leaderboard.json", {"leaderboard": leaderboard}))
    files_written.append(_write_json(output_dir / "stats.json", stats))

    run_output_dir = output_dir / "runs"
    run_output_dir.mkdir(exist_ok=True)
    for run in public_runs:
        files_written.append(_write_json(run_output_dir / f"{run['run_id']}.json", run))

    files_written.append(
        _write_json(
            output_dir / "surface.json",
            {
                "schema_version": SURFACE_SCHEMA_VERSION,
                "generated_at": generated_at,
                "stats": stats,
                "leaderboard": leaderboard,
                "runs": public_runs,
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
        handle = str(agent.get("handle") or agent.get("name") or "builtin")
        season_id = str(season.get("id") or "")
        key = (season_id, handle, str(agent.get("name") or "builtin"))
        bucket = buckets.setdefault(
            key,
            {
                "season_id": season_id,
                "season_name": str(season.get("name") or ""),
                "season_phase": str(season.get("phase") or "unknown"),
                "agent_name": str(agent.get("name") or "builtin"),
                "agent_handle": handle,
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
