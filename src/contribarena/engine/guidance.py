from __future__ import annotations

import json
import shlex
from dataclasses import dataclass

from contribarena.config.schema import RunConfig
from contribarena.models import CommandResult


@dataclass
class GuidanceInstallResult:
    installed: bool
    command: CommandResult | None
    manifest: dict[str, object]
    enabled: bool = True
    error: str = ""
    skipped_reason: str = ""


def install_guidance_sidecar(
    workspace: object,
    config: RunConfig,
    *,
    run_id: str,
    repo_full_name: str,
) -> GuidanceInstallResult:
    manifest = _guidance_manifest(config, run_id, repo_full_name)
    if not config.guidance.enabled:
        return GuidanceInstallResult(
            False,
            None,
            manifest,
            enabled=False,
            skipped_reason="guidance_disabled",
        )
    entry = _guidance_entry(config)
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
    command = (
        "mkdir -p .contribarena/guidance && "
        f"printf %s {shlex.quote(entry)} > .contribarena/guidance/guidance_entry.md && "
        f"printf %s {shlex.quote(manifest_text)} > .contribarena/guidance/guidance_manifest.json"
    )
    try:
        result = workspace.run(command)  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - defensive fail-soft path
        return GuidanceInstallResult(
            False,
            None,
            manifest,
            enabled=config.guidance.enabled,
            error=str(exc),
        )
    return GuidanceInstallResult(
        installed=result.exit_code == 0,
        command=result,
        manifest=manifest,
        enabled=config.guidance.enabled,
        error="" if result.exit_code == 0 else result.stderr or result.stdout,
    )


def guidance_artifact_payload(result: GuidanceInstallResult) -> dict[str, object]:
    return {
        "schema_version": "1",
        "enabled": result.enabled,
        "installed": result.installed,
        "available": result.installed,
        "degraded": bool(result.error),
        "skipped_reason": result.skipped_reason,
        "sidecar_path": ".contribarena/guidance",
        "entry_path": ".contribarena/guidance/guidance_entry.md",
        "manifest_path": ".contribarena/guidance/guidance_manifest.json",
        "manifest": result.manifest,
        "error": result.error,
    }


def _guidance_manifest(
    config: RunConfig,
    run_id: str,
    repo_full_name: str,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "run_id": run_id,
        "repo_full_name": repo_full_name,
        "run_mode": config.run.mode,
        "enabled": config.guidance.enabled,
        "sources": [
            {
                "kind": "guidance_entry",
                "path": ".contribarena/guidance/guidance_entry.md",
                "present": config.guidance.enabled,
            }
        ],
        "expected_repo_sources": [
            "AGENTS.md",
            "CONTRIBUTING.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE/",
            ".github/ISSUE_TEMPLATE/",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
        ],
        "notes": [
            "Guidance is a fixed sidecar, not memory and not a quality gate.",
            "Repository docs remain external sources of truth; inspect them before PR work.",
        ],
    }


def _guidance_entry(config: RunConfig) -> str:
    mode = config.run.mode
    return "\n".join(
        [
            "# ContribArena Agent Guidance",
            "",
            "This sidecar is fixed system guidance for the current run. It is not",
            "memory, not a checklist, and not part of the target repository patch.",
            "",
            f"Run mode: {mode}",
            "",
            "Before choosing or editing a PR-shaped change:",
            "",
            "1. Inspect the target repository's CONTRIBUTING file when present.",
            "2. Inspect PR and issue templates when present.",
            "3. Use memory tools for prior run evidence when the task spans many steps.",
            "4. Keep the change small, code-or-test focused, and reviewable.",
            "",
            "Use existing ACI tools such as aci_view, aci_search, and aci_find_files",
            "to read repository guidance. Record durable observations with memory",
            "tools only when they are concrete and useful for this run or future runs.",
            "",
        ]
    )
