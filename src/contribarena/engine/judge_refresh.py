from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from contribarena.config.schema import RunConfig
from contribarena.engine.judgement import (
    build_judge_dimension_packets,
    build_judge_packet,
    judge_run,
)
from contribarena.engine.operator_events import OperatorProgressWriter
from contribarena.engine.runner import _judgement_progress_reporter
from contribarena.engine.surface_summary import _judgement
from contribarena.errors import InfrastructureError
from contribarena.trace import TraceWriter


@dataclass(frozen=True)
class JudgeRefreshResult:
    runs_judged: int
    skipped: list[str]


def refresh_judgement(
    *,
    config: RunConfig,
    input_dir: Path,
    run_id: str | None = None,
    all_unjudged: bool = False,
    force: bool = False,
) -> JudgeRefreshResult:
    run_dirs = _target_run_dirs(
        input_dir=input_dir,
        run_id=run_id,
        all_unjudged=all_unjudged,
        force=force,
    )
    judged = 0
    skipped: list[str] = []
    for run_dir in run_dirs:
        summary = _read_json(run_dir / "run_summary.json")
        current_status = (
            summary.get("judgement", {}).get("status")
            if isinstance(summary.get("judgement"), dict)
            else None
        )
        if not force and current_status in {"judged", "partial_fallback", "fallback"}:
            skipped.append(f"{run_dir}: already judged")
            continue
        rid = str(summary.get("run_id") or run_dir.name)
        packet = build_judge_packet(config=config, run_id=rid, run_dir=run_dir)
        _write_json(run_dir / "judge_packet.json", packet.model_dump(mode="json"))
        _write_json(run_dir / "judge_dimension_packets.json", build_judge_dimension_packets(packet))
        trace = TraceWriter(run_dir / "trace.jsonl", rid)
        operator = OperatorProgressWriter(run_dir / "operator_events.jsonl", rid, stream=True)
        judgement = judge_run(
            config=config,
            run_id=rid,
            run_dir=run_dir,
            packet=packet,
            progress=_judgement_progress_reporter(trace=trace, operator=operator),
        )
        _write_json(run_dir / "judgement.json", judgement.model_dump(mode="json"))
        summary["judgement"] = _judgement(run_dir).model_dump(mode="json")
        _ensure_artifact(summary, "judge_packet.json", "json")
        _ensure_artifact(summary, "judge_dimension_packets.json", "json")
        _ensure_artifact(summary, "judgement.json", "json")
        _write_json(run_dir / "run_summary.json", summary)
        judged += 1
    return JudgeRefreshResult(runs_judged=judged, skipped=skipped)


def _target_run_dirs(
    *,
    input_dir: Path,
    run_id: str | None,
    all_unjudged: bool,
    force: bool,
) -> list[Path]:
    if not input_dir.exists():
        raise InfrastructureError(f"judge input directory does not exist: {input_dir}")
    if run_id:
        matches = [
            path.parent
            for path in input_dir.rglob("run_summary.json")
            if _summary_run_id(path) == run_id
        ]
        if not matches:
            raise InfrastructureError(f"run not found for judgement refresh: {run_id}")
        return matches
    if all_unjudged:
        return [
            path.parent
            for path in input_dir.rglob("run_summary.json")
            if _summary_is_terminal(path)
            and (
                force
                or _summary_judgement_status(path)
                not in {"judged", "partial_fallback", "fallback"}
            )
        ]
    raise InfrastructureError("judge requires --run-id or --all-unjudged")


def _summary_run_id(path: Path) -> str:
    payload = _read_json(path)
    return str(payload.get("run_id") or path.parent.name)


def _summary_judgement_status(path: Path) -> str:
    payload = _read_json(path)
    judgement = payload.get("judgement", {}) if isinstance(payload, dict) else {}
    if isinstance(judgement, dict):
        return str(judgement.get("status") or "not_judged")
    return "not_judged"


def _summary_is_terminal(path: Path) -> bool:
    payload = _read_json(path)
    status = str(payload.get("run_status") or "")
    return status not in {"", "unknown", "running", "pending"}


def _ensure_artifact(summary: dict[str, object], name: str, kind: str) -> None:
    artifacts = summary.setdefault("artifacts", [])
    if not isinstance(artifacts, list):
        summary["artifacts"] = artifacts = []
    if any(isinstance(item, dict) and item.get("name") == name for item in artifacts):
        return
    artifacts.append(
        {
            "name": name,
            "kind": kind,
            "visibility": "public",
            "url": "",
            "size_bytes": 0,
            "redacted": False,
        }
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
