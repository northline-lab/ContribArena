from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.models.interface import ModelProvider
from json_repair import repair_json
from pydantic import BaseModel, Field

from contribarena.config.schema import JudgementJudgeConfig, RunConfig
from contribarena.models.judgement import (
    JudgePacket,
    JudgementAggregateRubricScore,
    JudgementArtifact,
    JudgementJudgeResult,
    JudgementMaintainerOutcome,
    JudgementPanel,
    JudgementRubricScore,
    JudgementSeason,
    JudgementTarget,
)
from contribarena.providers import ContribArenaModelProvider


DIMENSIONS = [
    "project_selection_quality",
    "opportunity_identification_quality",
    "repository_understanding_and_plan",
    "solution_correctness",
    "verification_evidence_quality",
    "maintainer_acceptability",
]

DEFAULT_DIMENSION_WEIGHTS = {
    "project_selection_quality": 0.10,
    "opportunity_identification_quality": 0.15,
    "repository_understanding_and_plan": 0.15,
    "solution_correctness": 0.25,
    "verification_evidence_quality": 0.15,
    "maintainer_acceptability": 0.20,
}


def build_judge_packet(
    *,
    config: RunConfig,
    run_id: str,
    run_dir: Path,
) -> JudgePacket:
    summary = _read_json(run_dir / "run_summary.json")
    return JudgePacket(
        season=JudgementSeason(
            id=config.judgement.season_id,
            name=config.judgement.season_name,
            phase=config.judgement.season_phase,
        ),
        run_id=run_id,
        repository=_dict(summary.get("repository")),
        opportunity={
            "source": summary.get("opportunity_source", "none"),
            "source_ref": summary.get("opportunity_source_ref", ""),
        },
        terminal={
            "status": summary.get("run_status", "unknown"),
            "reason": summary.get("terminal_reason", ""),
            "layer": summary.get("terminal_layer", ""),
        },
        pipeline=[_dict(item) for item in summary.get("pipeline", []) if isinstance(item, dict)],
        quality_gate=_dict(summary.get("quality_gate")),
        pull_request=_dict(summary.get("pull_request")),
        maintainer_outcome=_dict(summary.get("maintainer_outcome")),
        contribution_class=str(summary.get("contribution_class", "unknown")),
        artifacts=[
            str(item.get("name", ""))
            for item in summary.get("artifacts", [])
            if isinstance(item, dict) and item.get("name")
        ],
        selected_task_summary=_read_excerpt(run_dir / "selected_task.md"),
        eligibility_summary=_read_excerpt(run_dir / "eligibility_report.json"),
        maintainer_fit_summary=_read_excerpt(run_dir / "maintainer_fit.md"),
        behavior_summary=_behavior_summary(run_dir),
        patch_excerpt=_read_excerpt(run_dir / "patch.diff", max_chars=8000),
        pr_description_excerpt=_read_excerpt(run_dir / "pr_description.md", max_chars=6000),
        verification_excerpt=_verification_excerpt(run_dir),
    )


def build_judge_dimension_packets(packet: JudgePacket) -> dict[str, dict[str, object]]:
    return {dimension: _dimension_packet(dimension, packet) for dimension in DIMENSIONS}


def judge_run(
    *,
    config: RunConfig,
    run_id: str,
    run_dir: Path,
    packet: JudgePacket,
    model_provider: ModelProvider | None = None,
) -> JudgementArtifact:
    provider = model_provider or ContribArenaModelProvider(config.models)
    judges = [_judge_from_config(config, judge, packet, provider) for judge in _judges(config)]
    aggregate = _aggregate(judges, _dimension_weights(config))
    judge_score = _mean([judge.judge_score for judge in judges])
    maintainer = JudgementMaintainerOutcome(
        status=str(packet.maintainer_outcome.get("status", "pending")),
        source=str(packet.maintainer_outcome.get("source", "none")),
    )
    adjustment = _real_world_adjustment(config, packet)
    pr_url = str(packet.pull_request.get("url", ""))
    return JudgementArtifact(
        status=_judgement_status(judges),
        season_id=config.judgement.season_id,
        run_id=run_id,
        target=JudgementTarget(pr_url=pr_url),
        judge_panel=JudgementPanel(panel_id=config.judgement.panel_id),
        judges=judges,
        aggregate_rubric=aggregate,
        judge_score=round(judge_score, 2),
        real_world_adjustment=adjustment,
        arena_score=round(max(0.0, judge_score + adjustment), 2),
        maintainer_outcome=maintainer,
        evidence=_evidence(run_dir),
        created_at=datetime.now(UTC).isoformat(),
    )


def _judges(config: RunConfig) -> list[JudgementJudgeConfig]:
    if config.judgement.judges:
        return config.judgement.judges
    return [
        JudgementJudgeConfig(id=_judge_id(model), model=model)
        for model in _configured_judge_models(config)
    ]


def _configured_judge_models(config: RunConfig) -> list[str]:
    providers = config.models.providers
    models = [
        *(f"compatible/{name}" for name in providers.compatible),
        *(f"responses/{name}" for name in providers.responses),
        *(f"anthropic/{name}" for name in providers.anthropic),
        *(f"gemini/{name}" for name in providers.gemini),
    ]
    return models or [config.run.model]


def _judge_from_config(
    config: RunConfig,
    judge: JudgementJudgeConfig,
    packet: JudgePacket,
    model_provider: ModelProvider,
) -> JudgementJudgeResult:
    if judge.model != "local-stub":
        llm_result = _try_llm_judge(config, judge, packet, model_provider)
        if llm_result is not None:
            return llm_result
    rubric = _apply_weights(
        [
            item.model_copy(update={"source": "fallback"})
            for item in _heuristic_rubric(packet)
        ],
        _dimension_weights(config),
    )
    score = _rubric_score(rubric)
    return JudgementJudgeResult(
        judge_id=judge.id,
        model=judge.model,
        rubric=rubric,
        judge_score=round(score, 2),
        error="deterministic_fallback",
    )


class _JudgeDimensionOutput(BaseModel):
    dimension: str
    evidence: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=5)


def _try_llm_judge(
    config: RunConfig,
    judge: JudgementJudgeConfig,
    packet: JudgePacket,
    model_provider: ModelProvider,
) -> JudgementJudgeResult | None:
    try:
        from agents import Agent, ModelSettings, RunConfig as AgentsRunConfig, Runner
    except ImportError:
        return None
    try:
        fallback = {item.dimension: item for item in _heuristic_rubric(packet)}
        rubric: list[JudgementRubricScore] = []
        errors: list[str] = []
        for dimension in DIMENSIONS:
            try:
                rubric.append(
                    _run_llm_dimension_judge(
                        agent_cls=Agent,
                        agents_run_config_cls=AgentsRunConfig,
                        model_settings_cls=ModelSettings,
                        runner=Runner,
                        dimension=dimension,
                        judge=judge,
                        model_provider=model_provider,
                        packet=packet,
                    )
                )
            except Exception as exc:
                rubric.append(fallback[dimension].model_copy(update={"source": "fallback"}))
                errors.append(f"{dimension}: {str(exc)[:160]}")
        rubric = _apply_weights(rubric, _dimension_weights(config))
        score = _rubric_score(rubric)
        return JudgementJudgeResult(
            judge_id=judge.id,
            model=judge.model,
            rubric=rubric,
            judge_score=round(score, 2),
            error="; ".join(errors),
        )
    except Exception as exc:
        rubric = _apply_weights(
            [
                item.model_copy(update={"source": "fallback"})
                for item in _heuristic_rubric(packet)
            ],
            _dimension_weights(config),
        )
        score = _rubric_score(rubric)
        return JudgementJudgeResult(
            judge_id=judge.id,
            model=judge.model,
            rubric=rubric,
            judge_score=round(score, 2),
            error=f"llm_judge_failed: {str(exc)[:240]}",
        )


def _run_llm_dimension_judge(
    *,
    agent_cls: type,
    agents_run_config_cls: type,
    model_settings_cls: type,
    runner: object,
    dimension: str,
    judge: JudgementJudgeConfig,
    model_provider: ModelProvider,
    packet: JudgePacket,
) -> JudgementRubricScore:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            agent = agent_cls(
                name=f"contribarena-judge-{judge.id}-{dimension}-{attempt + 1}",
                instructions=_judge_dimension_instructions(dimension),
                model=judge.model,
                model_settings=model_settings_cls(max_tokens=4096),
            )
            result = runner.run_sync(
                agent,
                json.dumps(_dimension_packet(dimension, packet), ensure_ascii=True),
                max_turns=1,
                run_config=agents_run_config_cls(
                    model_provider=model_provider,
                    workflow_name=f"ContribArena M0.7 Judge {dimension}",
                    tracing_disabled=True,
                ),
            )
            return _normalize_llm_dimension(dimension, str(result.final_output))
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                _sleep_before_retry(float(2**attempt))
    assert last_error is not None
    raise last_error


def _sleep_before_retry(seconds: float) -> None:
    time.sleep(seconds)


def _dimension_packet(dimension: str, packet: JudgePacket) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": packet.schema_version,
        "season": packet.season.model_dump(mode="json"),
        "run_id": packet.run_id,
        "dimension": dimension,
        "terminal": packet.terminal,
        "artifacts": packet.artifacts,
        "contribution_class": packet.contribution_class,
    }
    if dimension == "project_selection_quality":
        base.update(
            {
                "repository": packet.repository,
                "eligibility_summary": packet.eligibility_summary,
                "selected_task_summary": packet.selected_task_summary,
            }
        )
    elif dimension == "opportunity_identification_quality":
        base.update(
            {
                "repository": packet.repository,
                "opportunity": packet.opportunity,
                "selected_task_summary": packet.selected_task_summary,
                "maintainer_fit_summary": packet.maintainer_fit_summary,
            }
        )
    elif dimension == "repository_understanding_and_plan":
        base.update(
            {
                "repository": packet.repository,
                "pipeline": packet.pipeline,
                "selected_task_summary": packet.selected_task_summary,
                "maintainer_fit_summary": packet.maintainer_fit_summary,
                "behavior_summary": packet.behavior_summary,
            }
        )
    elif dimension == "solution_correctness":
        base.update(
            {
                "quality_gate": packet.quality_gate,
                "selected_task_summary": packet.selected_task_summary,
                "patch_excerpt": packet.patch_excerpt,
                "verification_excerpt": packet.verification_excerpt,
            }
        )
    elif dimension == "verification_evidence_quality":
        base.update(
            {
                "quality_gate": packet.quality_gate,
                "behavior_summary": packet.behavior_summary,
                "verification_excerpt": packet.verification_excerpt,
                "patch_excerpt": packet.patch_excerpt,
            }
        )
    elif dimension == "maintainer_acceptability":
        base.update(
            {
                "repository": packet.repository,
                "pull_request": packet.pull_request,
                "maintainer_outcome": packet.maintainer_outcome,
                "maintainer_fit_summary": packet.maintainer_fit_summary,
                "pr_description_excerpt": packet.pr_description_excerpt,
                "patch_excerpt": packet.patch_excerpt,
                "quality_gate": packet.quality_gate,
            }
        )
    return base


def _judge_dimension_instructions(dimension: str) -> str:
    return (
        f"You are a ContribArena M0.7 judge. Score only `{dimension}` for one "
        "anonymized run packet. "
        "Do not infer or reward the hidden agent/model identity. Evaluate the full chain: "
        "project selection, opportunity identification, repository understanding and plan, "
        "solution correctness, verification evidence, and maintainer acceptability. "
        "List concrete evidence first, then assign an integer score 0-5. "
        "Use this scale: 3 means acceptable with flaws, 4 means very good with no major "
        "issues, and 5 means exceptional, near-perfect evidence and execution. "
        "Do not give 5 for merely adequate work. Use 0 only for no valid evidence, "
        "a broken path, or a severe violation. "
        f"The dimension value must be exactly `{dimension}`. "
        f"{_rubric_scale_instructions(dimension)} "
        "Return only JSON with this shape: "
        f'{{"dimension":"{dimension}","evidence":["..."],"score":0}}. '
        "Do not score any other dimension. Do not include markdown."
    )


def _rubric_scale_instructions(dimension: str) -> str:
    anchors = {
        "project_selection_quality": (
            "Anchors for project_selection_quality: 5=recently active, clear guidance "
            "or contribution entry, active issue tracker, eligibility pass without "
            "warnings, and a specific reason this repo fits the contribution; 4=active "
            "and eligible with a real but less specific rationale; 3=contributable with "
            "no blockers but thin positive evidence; 2=questionable activity, warnings, "
            "or unclear rules; 1=surface-only signals; 0=archived, unmaintained, rejects "
            "the contribution, or eligibility failed."
        ),
        "opportunity_identification_quality": (
            "Anchors for opportunity_identification_quality: 5=explicit source such as "
            "issue, CI failure, or maintainer discussion, clear user/maintainer value, "
            "small high-leverage scope; 4=real useful feasible sourced opportunity with "
            "simple value argument; 3=real fixable issue but weak value or goal linkage; "
            "2=no source or maintainer signal but not harmful; 1=surface scan only; "
            "0=nonexistent, misread, wrong, or noise."
        ),
        "repository_understanding_and_plan": (
            "Anchors for repository_understanding_and_plan: 5=strong exploration of "
            "guidance, source, tests, PR templates, and style, with a plan matching repo "
            "structure; 4=key context read and reasonable plan with minor omissions; "
            "3=basic code reading and plan but shallow rules/test understanding; "
            "2=only target files read; 1=almost no exploration; 0=violates guidance or "
            "mismatches repo structure."
        ),
        "solution_correctness": (
            "Anchors for solution_correctness: 5=precise minimal fix, quality gate pass, "
            "targeted test or strong verification, no unrelated changes, project style "
            "preserved; 4=solves the problem with pass evidence and only minor edge, "
            "style, or test gaps; 3=likely solves core issue but has test, boundary, or "
            "cleanup gaps; 2=related but rough with obvious omissions, regression risk, "
            "or non-pass gate; 1=weak relation; 0=no patch, unapplyable patch, wrong "
            "file/function, obvious bug, or failed terminal status."
        ),
        "verification_evidence_quality": (
            "Anchors for verification_evidence_quality: 5=targeted problem-specific "
            "output, multiple relevant verification attempts, before/after or regression "
            "evidence, and clear explanation for unavailable checks; 4=tests support "
            "conclusion and main risks covered; 3=basic test or compile check with "
            "incomplete causal link; 2=only proves code runs; 1=no command-level evidence; "
            "0=failed verification ignored or misreported."
        ),
        "maintainer_acceptability": (
            "Anchors for maintainer_acceptability: 5=small precise PR, clear motivation, "
            "implementation, verification, and risk description, matching contribution "
            "class and repo rules, nearly merge-ready; 4=clear scope, accurate description, "
            "credible verification, low risk; 3=valuable but needs questions or small "
            "fixes; 2=unclear boundary, motivation, or verification; 1=near automation "
            "noise; 0=empty or misleading PR description, hidden failure, rule violation, "
            "spam, opt-out, or policy violation."
        ),
    }
    return anchors[dimension]


def _parse_judge_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(repair_json(text))


def _normalize_llm_dimension(dimension: str, text: str) -> JudgementRubricScore:
    payload = _parse_judge_json(text)
    if isinstance(payload, dict) and isinstance(payload.get("rubric"), list):
        for item in payload["rubric"]:
            if (
                isinstance(item, dict)
                and _canonical_dimension(str(item.get("dimension", ""))) == dimension
            ):
                return _score(
                    dimension,
                    int(item.get("score", 0) or 0),
                    [str(value) for value in item.get("evidence", []) if value],
                )
    output = _JudgeDimensionOutput.model_validate(payload)
    canonical = _canonical_dimension(output.dimension)
    if canonical != dimension:
        return _score(
            dimension,
            0,
            [f"judge returned wrong dimension: {output.dimension}"],
        )
    return _score(dimension, output.score, output.evidence)


def _canonical_dimension(dimension: str) -> str:
    aliases = {
        "opportunity_quality": "opportunity_identification_quality",
        "opportunity_selection_quality": "opportunity_identification_quality",
        "task_selection_quality": "opportunity_identification_quality",
        "verification_evidence": "verification_evidence_quality",
        "repository_understanding": "repository_understanding_and_plan",
        "maintainer_fit": "maintainer_acceptability",
    }
    return aliases.get(dimension, dimension)


def _heuristic_rubric(packet: JudgePacket) -> list[JudgementRubricScore]:
    terminal_status = str(packet.terminal.get("status", "unknown"))
    has_patch = bool(packet.patch_excerpt.strip())
    quality_status = str(packet.quality_gate.get("status", "unknown"))
    pr_state = str(packet.pull_request.get("state", "none"))
    behavior = packet.behavior_summary
    verification_count = int(behavior.get("verification_attempts", 0) or 0)
    command_count = int(behavior.get("command_count", 0) or 0)
    aci_count = int(behavior.get("aci_step_count", 0) or 0)

    if terminal_status == "failed":
        return [
            _score(
                dimension,
                0,
                [f"terminal_status={terminal_status}", "run failed before valid evidence"],
            )
            for dimension in DIMENSIONS
        ]

    project_score = 3
    if packet.repository.get("full_name"):
        project_score += 1
    if packet.eligibility_summary:
        project_score += 1

    opportunity_score = 3
    if packet.selected_task_summary:
        opportunity_score += 1
    if packet.opportunity.get("source") != "none":
        opportunity_score += 1

    understanding_score = 2
    if packet.selected_task_summary:
        understanding_score += 1
    if command_count or aci_count:
        understanding_score += 1
    if packet.maintainer_fit_summary or "repo_guidance.json" in packet.artifacts:
        understanding_score += 1

    correctness_score = 0
    if has_patch:
        correctness_score = 3
        if quality_status == "pass":
            correctness_score += 1
        if terminal_status == "completed":
            correctness_score += 1

    verification_score = 0
    if verification_count:
        verification_score = min(5, 2 + verification_count)
    elif "quality_gate.json" in packet.artifacts and quality_status == "pass":
        verification_score = 3

    acceptability_score = 0
    if has_patch:
        acceptability_score = 3
        if pr_state in {"open", "none"}:
            acceptability_score += 1
        if quality_status == "pass":
            acceptability_score += 1
    elif terminal_status == "blocked":
        acceptability_score = 2

    return [
        _score(
            "project_selection_quality",
            project_score,
            [
                f"repository={packet.repository.get('full_name', '') or 'unknown'}",
                f"eligibility_summary_present={bool(packet.eligibility_summary)}",
            ],
        ),
        _score(
            "opportunity_identification_quality",
            opportunity_score,
            [
                f"opportunity_source={packet.opportunity.get('source', 'none')}",
                f"selected_task_present={bool(packet.selected_task_summary)}",
            ],
        ),
        _score(
            "repository_understanding_and_plan",
            understanding_score,
            [
                f"command_count={command_count}",
                f"aci_step_count={aci_count}",
                f"guidance_artifact_present={'repo_guidance.json' in packet.artifacts}",
            ],
        ),
        _score(
            "solution_correctness",
            correctness_score,
            [
                f"patch_present={has_patch}",
                f"quality_gate={quality_status}",
                f"terminal_status={terminal_status}",
            ],
        ),
        _score(
            "verification_evidence_quality",
            verification_score,
            [
                f"verification_attempts={verification_count}",
                f"verification_excerpt_present={bool(packet.verification_excerpt)}",
            ],
        ),
        _score(
            "maintainer_acceptability",
            acceptability_score,
            [
                f"pull_request_state={pr_state}",
                f"quality_gate={quality_status}",
                f"pr_description_present={bool(packet.pr_description_excerpt)}",
            ],
        ),
    ]


def _score(dimension: str, score: int, evidence: list[str]) -> JudgementRubricScore:
    return JudgementRubricScore(  # type: ignore[arg-type]
        dimension=dimension,
        evidence=evidence,
        score=max(0, min(5, score)),
    )


def _rubric_score(rubric: list[JudgementRubricScore]) -> float:
    if not rubric:
        return 0.0
    total_weight = sum(item.weight for item in rubric)
    if total_weight <= 0:
        return sum(item.score for item in rubric) / (len(rubric) * 5) * 100
    return sum((item.score / item.max_score) * item.weight for item in rubric) / total_weight * 100


def _aggregate(
    judges: list[JudgementJudgeResult],
    weights: dict[str, float],
) -> list[JudgementAggregateRubricScore]:
    result: list[JudgementAggregateRubricScore] = []
    for dimension in DIMENSIONS:
        scores = [
            item.score
            for judge in judges
            for item in judge.rubric
            if item.dimension == dimension
        ]
        result.append(
            JudgementAggregateRubricScore(  # type: ignore[arg-type]
                dimension=dimension,
                mean_score=round(_mean(scores), 2),
                weight=weights[dimension],
                disagreement=round(_stdev(scores), 2),
            )
        )
    return result


def _judgement_status(judges: list[JudgementJudgeResult]) -> str:
    if not judges:
        return "not_judged"
    scores = [score for judge in judges for score in judge.rubric]
    if not scores:
        return "failed"
    fallback_count = sum(1 for score in scores if score.source == "fallback")
    if fallback_count == 0:
        return "judged"
    if fallback_count == len(scores):
        return "fallback"
    return "partial_fallback"


def _dimension_weights(config: RunConfig) -> dict[str, float]:
    weights = DEFAULT_DIMENSION_WEIGHTS.copy()
    for dimension, weight in config.judgement.dimension_weights.items():
        canonical = _canonical_dimension(dimension)
        if canonical in weights and weight >= 0:
            weights[canonical] = weight
    return weights


def _apply_weights(
    rubric: list[JudgementRubricScore],
    weights: dict[str, float],
) -> list[JudgementRubricScore]:
    return [
        item.model_copy(update={"weight": weights[str(item.dimension)]})
        for item in rubric
    ]


def _real_world_adjustment(config: RunConfig, packet: JudgePacket) -> int:
    adjustments = config.judgement.outcome_adjustments
    status = str(packet.maintainer_outcome.get("status", "unknown"))
    pr_state = str(packet.pull_request.get("state", "none"))
    if status in {"spam", "opt_out", "policy_violation"}:
        return adjustments.spam_or_opt_out
    if status == "merged" or pr_state == "merged":
        return adjustments.merged
    if status == "changes_requested":
        return adjustments.changes_requested
    if status == "reviewed":
        return adjustments.reviewed
    if status == "closed" or pr_state == "closed":
        return adjustments.closed
    if pr_state == "open":
        return adjustments.opened
    return 0


def _behavior_summary(run_dir: Path) -> dict[str, object]:
    trajectory = _read_json(run_dir / "trajectory.json")
    steps = trajectory if isinstance(trajectory, list) else []
    tool_names = [str(step.get("tool", "")) for step in steps if isinstance(step, dict)]
    return {
        "aci_step_count": len(tool_names),
        "tools_used": sorted(set(tool_names)),
        "verification_attempts": sum(1 for tool in tool_names if tool == "aci_verify"),
        "submit_attempts": sum(1 for tool in tool_names if tool == "aci_submit_patch"),
        "command_count": _command_count(run_dir),
    }


def _command_count(run_dir: Path) -> int:
    payload = _read_json(run_dir / "workspace_command.json")
    commands = payload.get("commands", []) if isinstance(payload, dict) else []
    return len(commands) if isinstance(commands, list) else 0


def _verification_excerpt(run_dir: Path) -> str:
    for name in ("verification_summary.md", "test_log.txt", "quality_report.md"):
        text = _read_excerpt(run_dir / name, max_chars=4000)
        if text:
            return text
    return ""


def _evidence(run_dir: Path) -> list[str]:
    names = [
        "judge_packet.json",
        "judge_dimension_packets.json",
        "run_summary.json",
        "patch.diff",
        "quality_gate.json",
        "pr_description.md",
        "selected_task.md",
        "trajectory.json",
        "workspace_command.json",
    ]
    return [name for name in names if (run_dir / name).exists()]


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_excerpt(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _mean(values: list[float] | list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _judge_id(model: str) -> str:
    return model.replace("/", "_").replace("-", "_").replace(".", "_") or "judge"
