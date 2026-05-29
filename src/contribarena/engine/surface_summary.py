from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from contribarena.config.schema import RunConfig
from contribarena.engine.seasons import derive_participant_id
from contribarena.engine.seasons import normalize_model_identity
from contribarena.models.artifacts import ArtifactEntry
from contribarena.models.lifecycle import TerminalState
from contribarena.models.surface import (
    RunSummary,
    SurfaceArtifact,
    SurfaceAgent,
    SurfaceJudgement,
    SurfaceMaintainerOutcome,
    SurfacePipelineStage,
    SurfacePullRequest,
    SurfaceQualityGate,
    SurfaceRepository,
    SurfaceRubricScore,
    SurfaceSeason,
)


PUBLIC_ARTIFACTS = {
    "run_summary.json",
    "patch.diff",
    "quality_gate.json",
    "judgement.json",
    "judge_packet.json",
    "judge_dimension_packets.json",
    "pr_description.md",
    "postmortem.md",
    "ci_status.json",
    "terminal_state.json",
}

OPERATOR_ARTIFACTS = {
    "agent_final_result.json",
    "artifact_manifest.json",
    "config.json",
    "repo_profile.md",
    "opportunity_rank.md",
    "selected_task.md",
    "eligibility_report.json",
    "maintainer_fit.md",
    "spam_risk.md",
}


def build_run_summary(
    *,
    config: RunConfig,
    run_id: str,
    run_dir: Path,
    artifact_entries: list[ArtifactEntry],
    terminal: TerminalState,
    repo_slug: str,
) -> RunSummary:
    trace_times = _trace_times(run_dir / "trace.jsonl")
    quality_gate = _quality_gate(run_dir / "quality_gate.json")
    patch = _read_text(run_dir / "patch.diff")
    pr = _pull_request(run_dir)
    maintainer = _maintainer_outcome(run_dir, pr)
    submission_outcome = _submission_outcome(run_dir, terminal)
    ranking_eligible, ranking_reason, score_status = _ranking_policy(submission_outcome)
    repository = _repository(config, repo_slug)
    judgement = _judgement(run_dir)
    return RunSummary(
        run_id=run_id,
        run_mode=config.run.mode,
        model=config.run.model,
        wake_source=config.run.wake_source,
        agent=_agent(config),
        repository=repository,
        season=SurfaceSeason(
            id=config.run.season_id or config.judgement.season_id,
            name=(config.season.name if config.season else config.judgement.season_name),
            phase=_season_phase(config),
        ),
        opportunity_source=_opportunity_source(config),
        opportunity_source_ref=_opportunity_source_ref(config),
        started_at=trace_times["started_at"],
        completed_at=trace_times["completed_at"],
        duration_seconds=trace_times["duration_seconds"],
        run_status=terminal.status,
        terminal_reason=terminal.reason,
        terminal_layer=terminal.layer,
        contribution_class=_contribution_class(patch),
        pipeline=_pipeline(run_dir, terminal, quality_gate, patch, pr, maintainer),
        quality_gate=quality_gate,
        pull_request=pr,
        maintainer_outcome=maintainer,
        judgement=judgement,
        artifacts=_surface_artifacts(run_dir, artifact_entries),
        workspace={
            "persistent_metadata_path": str(config.workspace.persistent_metadata_path or ""),
        },
        replacement=_replacement_payload(run_dir, terminal),
        judgement_retry=_judgement_retry_payload(run_dir),
        live_submission_retry=_live_submission_retry_payload(run_dir),
        submission_outcome=submission_outcome,
        score_status=score_status,
        ranking_eligible=ranking_eligible,
        ranking_exclusion_reason=ranking_reason,
        contribution_thread_id=_contribution_thread_id(repository.full_name, pr),
    )


def _agent(config: RunConfig) -> SurfaceAgent:
    season_id = config.run.season_id or ""
    participant_id = config.run.participant_id or (
        derive_participant_id(season_id, config.run.model) if season_id else ""
    )
    display_name = normalize_model_identity(participant_id or config.run.model)
    return SurfaceAgent(
        name=display_name,
        handle=display_name,
        participant_id=participant_id,
    )


def _replacement_payload(run_dir: Path, terminal: TerminalState) -> dict[str, object]:
    path = run_dir / "replacement_state.json"
    if path.exists():
        payload = _read_json(path)
        return payload if isinstance(payload, dict) else {}
    if terminal.layer in {"model_runtime", "pr"} and _transient_message(terminal.message):
        return {
            "status": "due",
            "reason": terminal.reason,
            "layer": terminal.layer,
            "source": "terminal_state",
        }
    return {}


def _judgement_retry_payload(run_dir: Path) -> dict[str, object]:
    path = run_dir / "judgement_retry_state.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _live_submission_retry_payload(run_dir: Path) -> dict[str, object]:
    path = run_dir / "live_submission_retry_state.json"
    if not path.exists():
        return {}
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def _transient_message(message: str) -> bool:
    text = message.lower()
    return any(
        marker in text
        for marker in (
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
        )
    )


def _season_phase(config: RunConfig) -> str:
    if config.season:
        if config.season.discovery_profile.scope == "external":
            return "external_live"
        return "owned_repo_calibration"
    return config.judgement.season_phase


def _trace_times(path: Path) -> dict[str, Any]:
    events = _read_jsonl(path)
    if not events:
        return {"started_at": "", "completed_at": "", "duration_seconds": None}
    started_at = str(events[0].get("ts", ""))
    completed = next(
        (event for event in reversed(events) if event.get("event") == "run.completed"),
        events[-1],
    )
    completed_at = str(completed.get("ts", ""))
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": _duration_seconds(started_at, completed_at),
    }


def _duration_seconds(started_at: str, completed_at: str) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        return max(
            0.0,
            (
                datetime.fromisoformat(completed_at)
                - datetime.fromisoformat(started_at)
            ).total_seconds(),
        )
    except ValueError:
        return None


def _quality_gate(path: Path) -> SurfaceQualityGate:
    payload = _read_json(path)
    status = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
    if status not in {"pass", "block", "fail"}:
        status = "unknown"
    warnings = payload.get("warnings", []) if isinstance(payload, dict) else []
    return SurfaceQualityGate(
        status=status,
        warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
    )


def _pull_request(run_dir: Path) -> SurfacePullRequest:
    for entry in _read_jsonl(run_dir / "live_action_log.jsonl"):
        if entry.get("action") == "github.open_pr" and entry.get("status") in {"opened", "existing"}:
            number = entry.get("pr_number")
            if number is None:
                number = entry.get("number")
            return SurfacePullRequest(
                url=str(entry.get("pr_url") or entry.get("url") or ""),
                number=number if isinstance(number, int) else None,
                state="open",
            )
    if (run_dir / "pr_description.md").exists():
        return SurfacePullRequest(state="none")
    return SurfacePullRequest()


def _submission_outcome(run_dir: Path, terminal: TerminalState) -> str:
    rows = _read_jsonl(run_dir / "live_action_log.jsonl")
    open_rows = [row for row in rows if row.get("action") == "github.open_pr"]
    if any(row.get("status") in {"opened", "existing"} for row in open_rows):
        return "opened_pr"
    if any(row.get("status") == "updated" for row in open_rows):
        return "updated_pr"
    if any(row.get("error_kind") == "quality_gate" for row in open_rows):
        return "no_pr_quality_blocked"
    if any(row.get("error_kind") == "governance_block" for row in open_rows):
        return "no_pr_governance_blocked_agent"
    if any(row.get("error_kind") == "pr_identity_mismatch" for row in open_rows):
        return "no_pr_identity_mismatch"
    if any(
        str(row.get("error_kind") or "") in {
            "git_prepare_branch_transient",
            "git_push_transient",
            "git_push_nontransient",
            "branch_history_invalid",
            "fork_invalid",
            "github_api_transient",
            "infrastructure",
        }
        for row in rows
    ):
        return "no_pr_infrastructure_failure"
    if terminal.reason == "run_interrupted":
        return "run_interrupted"
    if terminal.layer == "model_runtime":
        return "no_pr_provider_failure"
    if rows or terminal.reason.startswith("live_pr"):
        return "no_pr_agent_failure"
    if terminal.status == "completed":
        return "opened_pr" if any(row.get("action") == "github.open_pr" for row in rows) else ""
    return "no_pr_agent_failure" if terminal.status in {"failed", "blocked"} else ""


def _ranking_policy(submission_outcome: str) -> tuple[bool, str, str]:
    if not submission_outcome:
        return True, "", "not_judged"
    if submission_outcome in {
        "opened_pr",
        "updated_pr",
        "no_pr_agent_failure",
        "no_pr_quality_blocked",
        "no_pr_governance_blocked_agent",
    }:
        return True, "", "scored"
    reason = f"submission_{submission_outcome}"
    return False, reason, "diagnostic_only"


def _contribution_thread_id(repository: str, pr: SurfacePullRequest) -> str:
    if not repository or pr.number is None:
        return ""
    return f"{repository}#{pr.number}"


def _maintainer_outcome(
    run_dir: Path,
    pr: SurfacePullRequest,
) -> SurfaceMaintainerOutcome:
    lifecycle = _read_json(run_dir / "pr_lifecycle_state.json")
    if isinstance(lifecycle, dict):
        records = lifecycle.get("records", [])
        if isinstance(records, list) and records:
            record = records[0]
            if isinstance(record, dict):
                state = str(record.get("state", "open"))
                status = str(record.get("lifecycle_status", "tracking"))
                observed_at = str(record.get("last_observed_at", ""))
                if state == "merged" or status == "merged":
                    return SurfaceMaintainerOutcome(
                        status="merged",
                        observed_at=observed_at,
                        source="github_pr_state",
                    )
                if status == "rejected":
                    return SurfaceMaintainerOutcome(
                        status="changes_requested",
                        observed_at=observed_at,
                        source="github_review",
                    )
                if state == "closed" or status == "closed":
                    return SurfaceMaintainerOutcome(
                        status="closed",
                        observed_at=observed_at,
                        source="github_pr_state",
                    )
                if status == "stale":
                    return SurfaceMaintainerOutcome(
                        status="stale",
                        observed_at=observed_at,
                        source="github_pr_state",
                    )
                if status == "needs_response":
                    return SurfaceMaintainerOutcome(
                        status="reviewed",
                        observed_at=observed_at,
                        source="github_review",
                    )
    return SurfaceMaintainerOutcome(status="pending" if pr.state == "open" else "unknown")


def _judgement(run_dir: Path) -> SurfaceJudgement:
    payload = _read_json(run_dir / "judgement.json")
    if not isinstance(payload, dict):
        return SurfaceJudgement()
    rubric = payload.get("aggregate_rubric", [])
    return SurfaceJudgement(
        status=str(payload.get("status", "judged")),  # type: ignore[arg-type]
        judge_score=_float_or_none(payload.get("judge_score")),
        real_world_adjustment=int(payload.get("real_world_adjustment", 0) or 0),
        arena_score=_float_or_none(payload.get("arena_score")),
        rubric_summary=[
            SurfaceRubricScore(
                dimension=str(item.get("dimension", "")),
                score=float(item.get("mean_score", 0) or 0),
                max_score=int(item.get("max_score", 5) or 5),
                weight=float(item.get("weight", 0) or 0),
            )
            for item in rubric
            if isinstance(item, dict)
        ],
        source_artifacts=[
            name
            for name in ("judgement.json", "judge_packet.json", "judge_dimension_packets.json")
            if (run_dir / name).exists()
        ],
    )


def _repository(config: RunConfig, repo_slug: str) -> SurfaceRepository:
    if config.discovery.candidates:
        candidate = config.discovery.candidates[0]
        return SurfaceRepository(full_name=candidate.full_name, url=str(candidate.url))
    return SurfaceRepository(full_name=repo_slug)


def _opportunity_source(config: RunConfig) -> str:
    if config.issue is not None and config.issue.source_url is not None:
        return "issue_url"
    if config.issue is not None:
        return "discovery_event_id"
    return "none"


def _opportunity_source_ref(config: RunConfig) -> str:
    if config.issue is None:
        return ""
    if config.issue.source_url is not None:
        return str(config.issue.source_url)
    return config.issue.title or "configured_issue"


def _pipeline(
    run_dir: Path,
    terminal: TerminalState,
    quality_gate: SurfaceQualityGate,
    patch: str,
    pr: SurfacePullRequest,
    maintainer: SurfaceMaintainerOutcome,
) -> list[SurfacePipelineStage]:
    return [
        _stage(
            "agent",
            _terminal_stage_status(terminal.status),
            "Agent returned a terminal result.",
            ["trajectory.json", "terminal_state.json"],
        ),
        _stage(
            "repo_discovery",
            "passed" if (run_dir / "selected_task.md").exists() else "unknown",
            "Repository and task evidence were recorded.",
            ["repo_profile.md", "opportunity_rank.md", "selected_task.md"],
        ),
        _stage(
            "workspace",
            "passed" if (run_dir / "workspace_command.json").exists() else "failed",
            "Workspace command and ACI evidence were captured.",
            ["workspace_command.json"],
        ),
        _stage(
            "patch_diff",
            "passed" if patch.strip() else "skipped",
            "Patch diff was captured." if patch.strip() else "No patch diff was submitted.",
            ["patch.diff"],
        ),
        _stage(
            "quality_gate",
            _quality_stage_status(quality_gate.status),
            f"Quality gate status: {quality_gate.status}.",
            ["quality_gate.json"],
        ),
        _stage(
            "pull_request",
            "passed" if pr.state == "open" else "skipped",
            "Live pull request was opened." if pr.state == "open" else "No live pull request was opened.",
            ["pr_description.md", "live_action_log.jsonl"],
        ),
        _stage(
            "maintainer_outcome",
            "pending" if maintainer.status == "pending" else "passed",
            f"Maintainer outcome: {maintainer.status}.",
            ["pr_lifecycle_state.json", "pr_review_log.jsonl"],
        ),
    ]


def _stage(
    stage_id: str,
    status: str,
    summary: str,
    source_artifacts: list[str],
) -> SurfacePipelineStage:
    return SurfacePipelineStage(
        stage_id=stage_id,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        summary=summary,
        source_artifacts=source_artifacts,
    )


def _terminal_stage_status(status: str) -> str:
    if status == "completed":
        return "passed"
    if status == "blocked":
        return "blocked"
    if status == "failed":
        return "failed"
    return "unknown"


def _quality_stage_status(status: str) -> str:
    if status == "pass":
        return "passed"
    if status == "block":
        return "blocked"
    if status == "fail":
        return "failed"
    return "unknown"


def _surface_artifacts(
    run_dir: Path,
    entries: list[ArtifactEntry],
) -> list[SurfaceArtifact]:
    names = {entry.name for entry in entries}
    synthetic_entries = [
        ArtifactEntry(
            name="run_summary.json",
            kind="json",
            required=False,
            path="run_summary.json",
        ),
        ArtifactEntry(
            name="trace.jsonl",
            kind="jsonl",
            required=True,
            path="trace.jsonl",
        ),
        ArtifactEntry(
            name="artifact_manifest.json",
            kind="json",
            required=True,
            path="artifact_manifest.json",
        ),
    ]
    entries_with_summary = [
        *entries,
        *[entry for entry in synthetic_entries if entry.name not in names],
    ]
    return [
        SurfaceArtifact(
            name=entry.name,
            kind=entry.kind,
            visibility=_artifact_visibility(entry.name),
            size_bytes=_file_size(run_dir / entry.path),
            redacted=entry.name in PUBLIC_ARTIFACTS,
        )
        for entry in sorted(entries_with_summary, key=lambda item: item.name)
    ]


def _artifact_visibility(name: str) -> str:
    if name in PUBLIC_ARTIFACTS:
        return "public"
    if name in OPERATOR_ARTIFACTS:
        return "operator"
    return "internal"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contribution_class(patch: str) -> str:
    paths = _diff_paths(patch)
    if not paths:
        return "unknown"
    classes: set[str] = set()
    if any(_is_docs_path(path) for path in paths):
        classes.add("docs")
    if any(_is_test_path(path) for path in paths):
        classes.add("tests")
    if any(not _is_docs_path(path) and not _is_test_path(path) for path in paths):
        classes.add("low_risk_code")
    if len(classes) > 1:
        return "mixed"
    return next(iter(classes), "unknown")


def _diff_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            paths.append(parts[3].removeprefix("b/"))
    return sorted(set(paths))


def _normalized_path(path: str) -> str:
    lowered = path.lower()
    return lowered.removeprefix("repo/") if lowered.startswith("repo/") else lowered


def _is_docs_path(path: str) -> bool:
    lowered = _normalized_path(path)
    if _is_test_path(lowered) or _is_code_path(lowered):
        return False
    if lowered.startswith(("docs/", "doc/")):
        return True
    if lowered in {"readme.md", "readme.rst", "changelog.md", "changelog.rst"}:
        return True
    return lowered.endswith((".md", ".rst", ".txt"))


def _is_test_path(path: str) -> bool:
    lowered = _normalized_path(path)
    if lowered.startswith(("tests/", "test/")):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _is_code_path(path: str) -> bool:
    lowered = _normalized_path(path)
    if lowered.startswith(("src/", "lib/", "pkg/", "packages/", "app/")):
        return True
    return bool(
        re.search(
            r"\.(py|js|jsx|ts|tsx|go|rs|java|kt|c|cc|cpp|h|hpp|cs|rb|php|swift|scala|sh)$",
            lowered,
        )
    )


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
