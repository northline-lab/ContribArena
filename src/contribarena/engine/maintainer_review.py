from __future__ import annotations

from dataclasses import dataclass
import json
import time

from agents.exceptions import ModelBehaviorError, UserError
from agents.models.interface import ModelProvider

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.operator_events import truncate_for_operator
from contribarena.memory.redact import redact_text


@dataclass(frozen=True)
class MaintainerReviewResult:
    row: dict[str, object]
    unavailable: bool = False


def run_maintainer_prereview(
    *,
    config: RunConfig,
    model_provider: ModelProvider | None,
    capture: ArtifactCapture,
    patch: str,
    round_number: int,
) -> MaintainerReviewResult:
    if model_provider is None or config.run.model == "local-stub":
        return _fallback_review("review_simulator_unavailable", round_number)
    prompt = _review_prompt(config, capture, patch, round_number)
    try:
        from agents import Agent, ModelSettings, RunConfig as AgentsRunConfig, Runner

        agent = Agent(
            name="contribarena-maintainer-prereview",
            instructions=(
                "You are a maintainer pre-review simulator. Review the draft patch for "
                "minimality, verification, duplicate risk, repository fit, and PR readiness. "
                "Return only compact JSON with keys severity, concerns, suggested_changes, "
                "and summary. severity must be approve, comment, or request_changes. "
                "concerns and suggested_changes must be arrays of short strings."
            ),
            model=config.run.model,
            model_settings=ModelSettings(max_tokens=min(config.run.budget.max_tokens or 1200, 1200)),
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=1,
            run_config=AgentsRunConfig(
                model_provider=model_provider,
                workflow_name="ContribArena maintainer pre-review",
                tracing_disabled=True,
            ),
        )
    except (ModelBehaviorError, UserError, Exception) as exc:  # pragma: no cover - provider path
        return _fallback_review(
            "review_simulator_unavailable",
            round_number,
            error=redact_text(str(exc), max_chars=500),
        )
    return _parse_review(str(result.final_output or ""), round_number)


def _review_prompt(
    config: RunConfig,
    capture: ArtifactCapture,
    patch: str,
    round_number: int,
) -> str:
    repo = config.discovery.candidates[0].full_name if config.discovery.candidates else ""
    recent = "\n".join(
        f"- {step.tool}: {truncate_for_operator(step.result_summary, 180)}"
        for step in capture.steps[-12:]
    )
    return (
        f"Repository: {repo}\n"
        f"Review round: {round_number}\n\n"
        "Recent run evidence:\n"
        f"{recent or '- none'}\n\n"
        "Draft patch:\n"
        f"{truncate_for_operator(patch, 6000)}\n\n"
        "Return JSON only."
    )


def _parse_review(text: str, round_number: int) -> MaintainerReviewResult:
    try:
        payload = json.loads(_json_object_text(text))
    except json.JSONDecodeError:
        return _fallback_review(
            "review_simulator_unavailable",
            round_number,
            error="review simulator returned non-json output",
        )
    if not isinstance(payload, dict):
        return _fallback_review(
            "review_simulator_unavailable",
            round_number,
            error="review simulator returned a non-object payload",
        )
    severity = str(payload.get("severity") or "comment").strip().lower()
    if severity not in {"approve", "comment", "request_changes"}:
        severity = "comment"
    concerns = _string_list(payload.get("concerns"))
    suggested = _string_list(payload.get("suggested_changes"))
    summary = redact_text(str(payload.get("summary") or ""), max_chars=1000)
    return MaintainerReviewResult(
        row={
            "schema_version": "1",
            "tool_call_id": f"maintainer_prereview:{round_number}",
            "phase": "review",
            "status": "completed",
            "round": round_number,
            "severity": severity,
            "concerns": concerns,
            "suggested_changes": suggested,
            "summary": summary,
            "ts": time.time(),
        }
    )


def _fallback_review(
    status: str,
    round_number: int,
    *,
    error: str = "",
) -> MaintainerReviewResult:
    return MaintainerReviewResult(
        unavailable=True,
        row={
            "schema_version": "1",
            "tool_call_id": f"maintainer_prereview:{round_number}",
            "phase": "review",
            "status": status,
            "round": round_number,
            "severity": "unavailable",
            "concerns": [],
            "suggested_changes": [],
            "error": error,
            "fallback": "Review Readiness uses patch, PR description, and repo norms.",
            "ts": time.time(),
        },
    )


def _json_object_text(text: str) -> str:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [redact_text(str(item), max_chars=500) for item in value[:10] if str(item).strip()]
