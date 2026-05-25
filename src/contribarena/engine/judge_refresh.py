from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contribarena.config.schema import RunConfig
from contribarena.engine.judgement import (
    build_judge_dimension_packets,
    build_judge_packet,
    judge_run,
)
from contribarena.engine.operator_events import OperatorProgressWriter
from contribarena.engine.persistence import atomic_write_json
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.runner import _judgement_progress_reporter
from contribarena.engine.surface_summary import _judgement
from contribarena.errors import InfrastructureError
from contribarena.trace import TraceWriter


TRANSIENT_JUDGE_MARKERS = (
    "apiconnectionerror",
    "connection error",
    "connection reset",
    "socket reset",
    "timeout",
    "timed out",
    "503",
    "502",
    "504",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)
MAX_JUDGEMENT_RETRY_ATTEMPTS = 3
STALE_RUNNING_JUDGEMENT_RETRY_SECONDS = 2 * 60 * 60


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
        read_model_path=config.backend.read_model_path,
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
        if mark_transient_judgement_retry_due(run_dir):
            skipped.append(f"{run_dir}: transient judgement deferred")
            continue
        retry_path = run_dir / "judgement_retry_state.json"
        if retry_path.exists():
            retry_state = {
                "status": "succeeded",
                "completed_at": judgement.created_at,
                "source": "judgement_refresh",
            }
            _write_json(retry_path, retry_state)
            summary = _read_json(run_dir / "run_summary.json")
            summary["judgement_retry"] = retry_state
            _write_json(run_dir / "run_summary.json", summary)
        judged += 1
    return JudgeRefreshResult(runs_judged=judged, skipped=skipped)


def mark_transient_judgement_retry_due(run_dir: Path) -> bool:
    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        return False
    summary = _read_json(summary_path)
    judgement_path = run_dir / "judgement.json"
    judgement = (
        _read_json(judgement_path)
        if judgement_path.exists()
        else summary.get("judgement", {})
    )
    if not isinstance(judgement, dict):
        return False
    if not _judgement_needs_transient_retry(judgement):
        return False
    existing = (
        _read_json(run_dir / "judgement_retry_state.json")
        if (run_dir / "judgement_retry_state.json").exists()
        else summary.get("judgement_retry")
    )
    attempts = int(existing.get("attempts") or 0) if isinstance(existing, dict) else 0
    next_attempt = attempts + 1
    status = "due" if next_attempt <= MAX_JUDGEMENT_RETRY_ATTEMPTS else "failed"
    retry_state = {
        "status": status,
        "attempts": next_attempt,
        "max_attempts": MAX_JUDGEMENT_RETRY_ATTEMPTS,
        "reason": "transient_judge_failure",
        "source": "judge_panel",
    }
    _write_json(run_dir / "judgement_retry_state.json", retry_state)
    summary["judgement_retry"] = retry_state
    judgement["status"] = "deferred" if status == "due" else "failed"
    judgement["judge_score"] = None
    judgement["arena_score"] = None
    if judgement_path.exists():
        _write_json(judgement_path, judgement)
        summary["judgement"] = _judgement(run_dir).model_dump(mode="json")
    else:
        summary["judgement"] = judgement
    _write_json(summary_path, summary)
    return True


def refresh_due_judgements(
    *,
    config: RunConfig,
    input_dir: Path,
    season_id: str | None = None,
    limit: int = 1,
) -> JudgeRefreshResult:
    due = [
        run_dir
        for run_dir in _due_judgement_run_dirs(input_dir, season_id=season_id)
    ][: max(0, limit)]
    judged = 0
    skipped: list[str] = []
    for run_dir in due:
        state = _read_json(run_dir / "judgement_retry_state.json")
        summary = _read_json(run_dir / "run_summary.json")
        if _run_has_transient_replacement(run_dir, summary):
            state["status"] = "skipped"
            state["reason"] = "transient_replacement_pending"
            state["source"] = "judgement_refresh"
            _write_json(run_dir / "judgement_retry_state.json", state)
            summary["judgement_retry"] = state
            judgement = summary.get("judgement", {}) if isinstance(summary.get("judgement"), dict) else {}
            if judgement:
                judgement["status"] = "not_judged"
                judgement["judge_score"] = None
                judgement["arena_score"] = None
                summary["judgement"] = judgement
                judgement_path = run_dir / "judgement.json"
                if judgement_path.exists():
                    _write_json(judgement_path, judgement)
            _write_json(run_dir / "run_summary.json", summary)
            skipped.append(f"{run_dir}: skipped judgement for transient replacement")
            continue
        state["status"] = "running"
        state["started_at"] = datetime.now(UTC).isoformat()
        _write_json(run_dir / "judgement_retry_state.json", state)
        try:
            result = refresh_judgement(
                config=config,
                input_dir=input_dir,
                run_id=_summary_run_id(run_dir / "run_summary.json"),
                force=True,
            )
        except Exception as exc:
            attempts = int(state.get("attempts") or 0) + 1
            state["attempts"] = attempts
            state["max_attempts"] = MAX_JUDGEMENT_RETRY_ATTEMPTS
            state["status"] = (
                "due"
                if _transient_text(str(exc)) and attempts < MAX_JUDGEMENT_RETRY_ATTEMPTS
                else "failed"
            )
            state["last_error"] = str(exc)[:500]
            _write_json(run_dir / "judgement_retry_state.json", state)
            skipped.append(f"{run_dir}: {exc}")
            continue
        judged += result.runs_judged
        skipped.extend(result.skipped)
    return JudgeRefreshResult(runs_judged=judged, skipped=skipped)


def _run_has_transient_replacement(run_dir: Path, summary: dict[str, object]) -> bool:
    replacement_path = run_dir / "replacement_state.json"
    if replacement_path.exists():
        replacement = _read_json(replacement_path)
        if isinstance(replacement, dict) and _transient_replacement_payload(replacement):
            return True
    if _transient_replacement_payload(summary.get("replacement")):
        return True
    terminal_path = run_dir / "terminal_state.json"
    terminal = _read_json(terminal_path) if terminal_path.exists() else {}
    if not isinstance(terminal, dict):
        return False
    return str(terminal.get("layer") or "") == "model_runtime" and _transient_text(
        str(terminal.get("message") or "")
    )


def _transient_replacement_payload(replacement: object) -> bool:
    if not isinstance(replacement, dict):
        return False
    if str(replacement.get("status") or "") not in {"due", "running", "replaced", "failed", "exhausted"}:
        return False
    if str(replacement.get("layer") or "") == "model_runtime":
        return True
    return _transient_text(str(replacement.get("message") or ""))


def _due_judgement_run_dirs(input_dir: Path, *, season_id: str | None) -> list[Path]:
    rows: list[tuple[str, Path]] = []
    for state_path in sorted(input_dir.rglob("judgement_retry_state.json")):
        state = _read_json(state_path)
        status = str(state.get("status") or "")
        if status == "running" and _running_retry_is_stale(state):
            status = "due"
            state["status"] = "due"
            state["stale_running_recovered_at"] = datetime.now(UTC).isoformat()
            _write_json(state_path, state)
        if status != "due":
            continue
        run_dir = state_path.parent
        summary_path = run_dir / "run_summary.json"
        if not summary_path.exists():
            continue
        summary = _read_json(summary_path)
        season = summary.get("season", {}) if isinstance(summary.get("season"), dict) else {}
        if season_id and str(season.get("id") or "") != season_id:
            continue
        rows.append((str(summary.get("started_at") or run_dir.name), run_dir))
    return [run_dir for _, run_dir in sorted(rows)]


def _judgement_needs_transient_retry(judgement: dict[str, object]) -> bool:
    if str(judgement.get("status") or "") not in {"fallback", "partial_fallback"}:
        return False
    judges = judgement.get("judges", [])
    if not isinstance(judges, list):
        return False
    return any(
        isinstance(judge, dict) and _transient_text(str(judge.get("error") or ""))
        for judge in judges
    )


def _transient_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in TRANSIENT_JUDGE_MARKERS)


def _target_run_dirs(
    *,
    input_dir: Path,
    read_model_path: Path,
    run_id: str | None,
    all_unjudged: bool,
    force: bool,
) -> list[Path]:
    if not input_dir.exists():
        raise InfrastructureError(f"judge input directory does not exist: {input_dir}")
    if run_id:
        indexed = _indexed_run_dir(read_model_path, run_id)
        if indexed is not None:
            return [indexed]
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
    atomic_write_json(path, payload, ensure_ascii=True)


def _running_retry_is_stale(state: dict[str, object]) -> bool:
    started_at = str(state.get("started_at") or "")
    if not started_at:
        return True
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) - started >= timedelta(
        seconds=STALE_RUNNING_JUDGEMENT_RETRY_SECONDS
    )


def _indexed_run_dir(read_model_path: Path, run_id: str) -> Path | None:
    if not read_model_path.exists():
        return None
    try:
        return SurfaceReadModel(read_model_path).run_dir(run_id)
    except Exception:
        return None
