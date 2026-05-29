from __future__ import annotations

from datetime import UTC, datetime

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.models import (
    AgentFinalResult,
    CiCheck,
    CiStatus,
    PullRequestDraft,
    QualityGateCheck,
    QualityGateResult,
    TerminalState,
)


def evaluate_contribution_quality(
    config: RunConfig,
    result: AgentFinalResult,
    capture: ArtifactCapture,
    patch: str,
) -> QualityGateResult:
    checks: list[QualityGateCheck] = []
    blockers: list[str] = []
    warnings: list[str] = []

    _add_check(
        checks,
        blockers,
        "agent_completed",
        result.status == "completed",
        f"agent status is {result.status}",
    )
    _add_check(
        checks,
        blockers,
        "patch_submitted",
        _has_patch_diff(patch),
        "shadow patch contains a git diff" if patch.strip() else "no submitted git diff",
    )
    _add_check(
        checks,
        blockers,
        "verified_after_edit",
        _has_successful_verification_after_last_edit(capture),
        "successful verification exists after the last edit",
    )

    paths = _patch_paths(patch)
    suspicious = _suspicious_patch_paths(paths)
    _add_check(
        checks,
        blockers,
        "maintainer_appropriate_paths",
        not suspicious,
        "no generated or temporary files in patch"
        if not suspicious
        else "suspicious paths: " + ", ".join(suspicious),
    )
    changed_lines = _changed_line_count(patch)
    minimal = len(paths) <= 12 and changed_lines <= 600
    _add_check(
        checks,
        blockers,
        "minimality",
        minimal,
        f"{len(paths)} file(s), {changed_lines} changed line(s)",
    )
    _add_check(
        checks,
        blockers,
        "risk_scope",
        result.selected_task.risk != "high",
        f"selected task risk is {result.selected_task.risk}",
    )

    if config.issue is not None:
        _add_check(
            checks,
            blockers,
            "problem_linked",
            bool(result.problem_statement_summary.strip()),
            "problem statement summary present",
        )
        _add_check(
            checks,
            blockers,
            "reproduction_or_reasoning",
            bool(result.reproduction_notes.strip()),
            "reproduction or reasoning notes present",
        )
        _add_check(
            checks,
            blockers,
            "verification_summary",
            bool(result.verification_summary.strip()),
            "verification summary present",
        )

    failed_commands = [command for command in capture.commands if command.exit_code != 0]
    if failed_commands:
        warnings.append(f"{len(failed_commands)} workspace command(s) returned non-zero")
        checks.append(
            QualityGateCheck(
                name="command_health",
                status="warn",
                detail=f"{len(failed_commands)} failed command(s)",
            )
        )
    else:
        checks.append(
            QualityGateCheck(
                name="command_health",
                status="pass",
                detail="no failed workspace commands recorded",
            )
        )

    return QualityGateResult(
        status="block" if blockers else "pass",
        blockers=blockers,
        warnings=warnings,
        checks=checks,
    )


def apply_quality_gate_to_result(
    result: AgentFinalResult,
    quality_gate: QualityGateResult,
) -> None:
    if quality_gate.status == "pass" or result.status != "completed":
        return
    result.status = "blocked"
    result.blockers.extend(
        [f"quality gate blocked PR readiness: {blocker}" for blocker in quality_gate.blockers]
    )
    if not result.verification_summary:
        result.verification_summary = "; ".join(quality_gate.blockers)


def build_pr_draft(
    config: RunConfig,
    result: AgentFinalResult,
    patch: str,
    *,
    run_id: str = "",
) -> PullRequestDraft:
    title = _pr_title(config, result)
    branch = _branch_name(result, run_id=run_id)
    labels = [_pr_lifecycle_label(config)]
    if config.issue is not None:
        labels.append("issue-solving")
    if result.selected_task.risk:
        labels.append(f"risk-{result.selected_task.risk}")
    notice_heading, notice_body = _pr_notice(config)
    body = "\n".join(
        [
            "## Summary",
            "",
            result.problem_statement_summary
            or result.selected_task.expected_change
            or result.selected_task.rationale
            or "Dry-run contribution prepared by ContribArena.",
            "",
            "## Verification",
            "",
            result.verification_summary or "No verification summary was returned.",
            "",
            "## Risk",
            "",
            f"- Selected task risk: {result.selected_task.risk}",
            f"- Files changed: {', '.join(_patch_paths(patch)) or 'n/a'}",
            "",
            notice_heading,
            "",
            notice_body,
        ]
    )
    return PullRequestDraft(title=title, branch=branch, labels=labels, body=body)


def render_pr_description(draft: PullRequestDraft) -> str:
    labels = ", ".join(draft.labels) if draft.labels else "n/a"
    return "\n".join(
        [
            f"# {draft.title}",
            "",
            f"- Branch: `{draft.branch}`",
            f"- Labels: {labels}",
            "",
            draft.body,
        ]
    )


def build_ci_status(capture: ArtifactCapture, quality_gate: QualityGateResult) -> CiStatus:
    if quality_gate.status != "pass":
        return CiStatus(
            status="not_run",
            checks=[
                CiCheck(
                    name="dry_run_quality_gate",
                    status="skipped",
                    details="CI observation skipped because contribution quality gate did not pass.",
                )
            ],
        )
    checks: list[CiCheck] = []
    for item in capture.aci_results:
        if item.tool != "aci_verify":
            continue
        checks.append(
            CiCheck(
                name="local_verification",
                status="success" if item.success else "failure",
                details=item.output or item.error or "",
            )
        )
    if not checks:
        checks.append(
            CiCheck(
                name="local_verification",
                status="skipped",
                details="No local verification command was recorded.",
            )
        )
    overall = "failure" if any(check.status == "failure" for check in checks) else "success"
    return CiStatus(status=overall, checks=checks)


def live_action_log_entries(draft: PullRequestDraft | None) -> list[dict[str, object]]:
    if draft is None:
        return [
            {
                "ts": datetime.now(UTC).isoformat(),
                "mode": "dry_run",
                "action": "github.open_pr",
                "status": "skipped",
                "reason": "quality_gate_not_passed",
                "external_write": False,
            }
        ]
    return [
        {
            "ts": datetime.now(UTC).isoformat(),
            "mode": "dry_run",
            "action": "github.open_pr",
            "status": "prepared",
            "title": draft.title,
            "branch": draft.branch,
            "labels": draft.labels,
            "external_write": False,
        }
    ]


def render_postmortem(
    terminal: TerminalState,
    quality_gate: QualityGateResult,
    ci_status: CiStatus,
    draft: PullRequestDraft | None,
) -> str:
    sections = [
        "# Postmortem",
        "",
        "## Terminal State",
        "",
        f"- Status: {terminal.status}",
        f"- Reason: {terminal.reason}",
        f"- Layer: {terminal.layer}",
        "",
        "## Contribution Gate",
        "",
        f"- Status: {quality_gate.status}",
    ]
    if quality_gate.blockers:
        sections.extend(["", "### Blockers", "", *[f"- {item}" for item in quality_gate.blockers]])
    if quality_gate.warnings:
        sections.extend(["", "### Warnings", "", *[f"- {item}" for item in quality_gate.warnings]])
    sections.extend(
        [
            "",
            "## PR Lifecycle",
            "",
            f"- Draft produced: {draft is not None}",
            f"- CI status: {ci_status.status}",
            "",
            "## Lesson",
            "",
            _postmortem_lesson(terminal, quality_gate, ci_status),
        ]
    )
    return "\n".join(sections)


def render_quality_gate_section(quality_gate: QualityGateResult) -> list[str]:
    sections = ["", "## Contribution Quality Gate", "", f"- Status: {quality_gate.status}"]
    if quality_gate.blockers:
        sections.extend(["", "### Blockers", "", *[f"- {item}" for item in quality_gate.blockers]])
    if quality_gate.warnings:
        sections.extend(["", "### Warnings", "", *[f"- {item}" for item in quality_gate.warnings]])
    sections.extend(
        [
            "",
            "### Checks",
            "",
            *[f"- {check.name}: {check.status} ({check.detail})" for check in quality_gate.checks],
        ]
    )
    return sections


def _add_check(
    checks: list[QualityGateCheck],
    blockers: list[str],
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(QualityGateCheck(name=name, status="pass" if passed else "block", detail=detail))
    if not passed:
        blockers.append(f"{name}: {detail}")


def _has_patch_diff(patch: str) -> bool:
    return patch.strip().startswith("diff --git ") or "\ndiff --git " in patch


def _has_successful_verification_after_last_edit(capture: ArtifactCapture) -> bool:
    last_edit_index = -1
    for index, item in enumerate(capture.aci_results):
        if (
            item.tool
            in {
                "aci_apply_patch",
                "aci_replace",
                "aci_insert",
                "aci_create",
                "aci_undo",
            }
            and item.success
        ):
            last_edit_index = index
    accepted_no_command_review = any(
        item.tool == "aci_submit_patch"
        and item.success
        and "no-command verification rationale accepted" in item.review_notes
        for item in capture.aci_results[last_edit_index + 1 :]
    )
    if accepted_no_command_review:
        return True
    return any(
        item.tool == "aci_verify" and item.success
        for item in capture.aci_results[last_edit_index + 1 :]
    )


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            paths.append(parts[3].removeprefix("b/"))
    return sorted(set(paths))


def _suspicious_patch_paths(paths: list[str]) -> list[str]:
    suspicious: list[str] = []
    markers = ("/__pycache__/", ".pyc", ".pyo", ".DS_Store", ".egg-info/", ".pytest_cache/")
    for path in paths:
        normalized = f"/{path}"
        if any(marker in normalized for marker in markers):
            suspicious.append(path)
    return suspicious


def _changed_line_count(patch: str) -> int:
    total = 0
    for line in patch.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            total += 1
    return total


def _pr_title(config: RunConfig, result: AgentFinalResult) -> str:
    if config.issue is not None and config.issue.title:
        return config.issue.title
    return result.selected_task.title


def _pr_lifecycle_label(config: RunConfig) -> str:
    if config.run.mode == "owned_live":
        return "contribarena-live"
    if config.run.mode == "external_live":
        return "contribarena-external-live"
    return "contribarena-dry-run"


def _pr_notice(config: RunConfig) -> tuple[str, str]:
    if config.run.mode == "owned_live":
        return (
            "## Live PR Notice",
            "This PR was opened by the ContribArena harness after local quality and governance gates passed.",
        )
    if config.run.mode == "external_live":
        return (
            "## External Live PR Notice",
            (
                "This PR was opened by the ContribArena harness through a bot account "
                "after local quality, eligibility, maintainer-fit, and governance gates passed. "
                "The change was AI-assisted and is intended to be low-risk and reviewable."
            ),
        )
    return (
        "## Dry-Run Notice",
        "This is a dry-run PR draft. No external repository write was performed.",
    )


def _branch_name(result: AgentFinalResult, *, run_id: str = "") -> str:
    slug = "".join(
        char.lower() if char.isalnum() else "-" for char in result.selected_task.title.strip()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if run_id:
        safe_run_id = "".join(char.lower() if char.isalnum() else "-" for char in run_id).strip(
            "-"
        )
        while "--" in safe_run_id:
            safe_run_id = safe_run_id.replace("--", "-")
        if safe_run_id:
            return f"contribarena/{safe_run_id}-{slug or 'dry-run'}"
    return f"contribarena/{slug or 'dry-run'}"


def _postmortem_lesson(
    terminal: TerminalState,
    quality_gate: QualityGateResult,
    ci_status: CiStatus,
) -> str:
    if terminal.status != "completed":
        return "Run stopped before PR dry-run completion; inspect terminal reason and blockers."
    if quality_gate.status != "pass":
        return "Contribution did not meet PR-readiness requirements."
    if ci_status.status != "success":
        return "CI evidence was not clean enough for a live PR."
    return "Run produced a PR-ready artifact set."
