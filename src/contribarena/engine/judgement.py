from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

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
    "project_fit",
    "opportunity_quality",
    "duplicate_avoidance",
    "repository_understanding",
    "execution_correctness",
    "verification_quality",
    "review_readiness",
    "agentic_judgment",
]

DEFAULT_DIMENSION_WEIGHTS = {
    "project_fit": 0.08,
    "opportunity_quality": 0.12,
    "duplicate_avoidance": 0.10,
    "repository_understanding": 0.08,
    "execution_correctness": 0.25,
    "verification_quality": 0.12,
    "review_readiness": 0.13,
    "agentic_judgment": 0.12,
}


class JudgementProgressReporter(Protocol):
    def __call__(self, event: str, payload: dict[str, Any]) -> None: ...


def build_judge_packet(
    *,
    config: RunConfig,
    run_id: str,
    run_dir: Path,
) -> JudgePacket:
    summary = _read_json(run_dir / "run_summary.json")
    repository = _dict(summary.get("repository"))
    repository.update(_discovery_context(config))
    return JudgePacket(
        season=JudgementSeason(
            id=config.judgement.season_id,
            name=config.judgement.season_name,
            phase=config.judgement.season_phase,
        ),
        run_id=run_id,
        repository=repository,
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
        discovery_calls_summary=_read_jsonl_excerpt(run_dir / "discovery_log.jsonl", max_chars=6000),
        phase_scout_project_excerpt=_read_jsonl_excerpt(
            run_dir / "phase_scout_project_comparison.jsonl", max_chars=6000
        ),
        phase_scout_opportunity_excerpt=_read_jsonl_excerpt(
            run_dir / "phase_scout_opportunity_comparison.jsonl", max_chars=6000
        ),
        phase_scout_duplicate_excerpt=_read_jsonl_excerpt(
            run_dir / "phase_scout_duplicate_check.jsonl", max_chars=6000
        ),
        phase_review_maintainer_excerpt=_read_excerpt(
            run_dir / "phase_review_maintainer_review.jsonl", max_chars=6000
        ),
        phase_review_response_excerpt=_read_excerpt(
            run_dir / "phase_review_response.jsonl", max_chars=6000
        ),
        goal_events_excerpt=_read_excerpt(run_dir / "goal_events.jsonl", max_chars=6000),
        phase_transition_excerpt=_read_excerpt(
            run_dir / "phase_transition.jsonl", max_chars=6000
        ),
        tool_violation_excerpt=_read_excerpt(
            run_dir / "tool_violation_log.jsonl", max_chars=6000
        ),
    )


def build_judge_dimension_packets(packet: JudgePacket) -> dict[str, dict[str, object]]:
    return {dimension: _dimension_packet(dimension, packet) for dimension in DIMENSIONS}


def _discovery_context(config: RunConfig) -> dict[str, object]:
    if config.issue is not None:
        return {"issue_solving": True, "fixed_candidate": True}
    if config.discovery.candidates:
        return {"fixed_candidate": True}
    return {"discovery_mode": "open", "query": config.discovery.query}


def judge_run(
    *,
    config: RunConfig,
    run_id: str,
    run_dir: Path,
    packet: JudgePacket,
    model_provider: ModelProvider | None = None,
    progress: JudgementProgressReporter | None = None,
) -> JudgementArtifact:
    provider = model_provider or ContribArenaModelProvider(config.models)
    judge_configs = _judges(config)
    _report_progress(progress, "judgement.started", {"judge_count": len(judge_configs)})
    judges: list[JudgementJudgeResult] = []
    for judge in judge_configs:
        _report_progress(progress, "judgement.judge_started", {"judge_id": judge.id, "model": judge.model})
        result = _judge_from_config(config, judge, packet, provider, progress)
        judges.append(result)
        fallback_dimensions = sum(1 for score in result.rubric if score.source == "fallback")
        _report_progress(
            progress,
            "judgement.judge_finished",
            {
                "judge_id": judge.id,
                "model": judge.model,
                "score": result.judge_score,
                "fallback_dimensions": fallback_dimensions,
                "error": result.error,
            },
        )
    aggregate = _aggregate(judges, _dimension_weights(config))
    judge_score = _mean([judge.judge_score for judge in judges])
    maintainer = JudgementMaintainerOutcome(
        status=str(packet.maintainer_outcome.get("status", "pending")),
        source=str(packet.maintainer_outcome.get("source", "none")),
    )
    adjustment = _real_world_adjustment(config, packet)
    pr_url = str(packet.pull_request.get("url", ""))
    artifact = JudgementArtifact(
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
    _report_progress(
        progress,
        "judgement.finished",
        {
            "status": artifact.status,
            "judge_score": artifact.judge_score,
            "arena_score": artifact.arena_score,
        },
    )
    return artifact


def _judges(config: RunConfig) -> list[JudgementJudgeConfig]:
    if config.judgement.judges:
        return config.judgement.judges
    return [
        JudgementJudgeConfig(id=_judge_id(model), model=model)
        for model in _configured_judge_models(config)
    ]


def _configured_judge_models(config: RunConfig) -> list[str]:
    if config.season is not None:
        models = [
            participant.model
            for participant in config.season.participants
            if "judge" in participant.role
        ]
        return _unique_models(models) or [config.run.model]
    providers = config.models.providers
    models = [
        *(f"compatible/{name}" for name in providers.compatible),
        *(f"responses/{name}" for name in providers.responses),
        *(f"anthropic/{name}" for name in providers.anthropic),
        *(f"gemini/{name}" for name in providers.gemini),
    ]
    return models or [config.run.model]


def _unique_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        unique.append(model)
    return unique


def _judge_from_config(
    config: RunConfig,
    judge: JudgementJudgeConfig,
    packet: JudgePacket,
    model_provider: ModelProvider,
    progress: JudgementProgressReporter | None,
) -> JudgementJudgeResult:
    if judge.model != "local-stub":
        llm_result = _try_llm_judge(config, judge, packet, model_provider, progress)
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
    progress: JudgementProgressReporter | None,
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
                        progress=progress,
                    )
                )
                _report_progress(
                    progress,
                    "judgement.dimension_finished",
                    {
                        "judge_id": judge.id,
                        "model": judge.model,
                        "dimension": dimension,
                        "source": rubric[-1].source,
                        "score": rubric[-1].score,
                    },
                )
            except Exception as exc:
                rubric.append(fallback[dimension].model_copy(update={"source": "fallback"}))
                errors.append(f"{dimension}: {str(exc)[:160]}")
                _report_progress(
                    progress,
                    "judgement.dimension_failed",
                    {
                        "judge_id": judge.id,
                        "model": judge.model,
                        "dimension": dimension,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:240],
                        "fallback": True,
                    },
                )
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
    progress: JudgementProgressReporter | None = None,
) -> JudgementRubricScore:
    last_error: Exception | None = None
    for attempt in range(3):
        _report_progress(
            progress,
            "judgement.dimension_started",
            {
                "judge_id": judge.id,
                "model": judge.model,
                "dimension": dimension,
                "attempt": attempt + 1,
            },
        )
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
                    workflow_name=f"ContribArena M0.10 Judge {dimension}",
                    tracing_disabled=True,
                ),
            )
            return _apply_floors_to_score(
                _normalize_llm_dimension(dimension, str(result.final_output)),
                _ground_truth_check(packet),
            )
        except Exception as exc:
            last_error = exc
            _report_progress(
                progress,
                "judgement.dimension_retry",
                {
                    "judge_id": judge.id,
                    "model": judge.model,
                    "dimension": dimension,
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                    "will_retry": attempt < 2,
                },
            )
            if attempt < 2:
                _sleep_before_retry(float(2**attempt))
    assert last_error is not None
    raise last_error


def _sleep_before_retry(seconds: float) -> None:
    time.sleep(seconds)


def _report_progress(
    progress: JudgementProgressReporter | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if progress is not None:
        progress(event, payload)


def _dimension_packet(dimension: str, packet: JudgePacket) -> dict[str, object]:
    floors = _ground_truth_check(packet)
    base: dict[str, object] = {
        "schema_version": packet.schema_version,
        "season": packet.season.model_dump(mode="json"),
        "run_id": packet.run_id,
        "dimension": dimension,
        "terminal": packet.terminal,
        "contribution_class": packet.contribution_class,
        "deterministic_floors": floors.get(dimension, []),
    }
    if dimension == "project_fit":
        base.update(
            {
                "repository": packet.repository,
                "discovery_calls_summary": packet.discovery_calls_summary,
                "phase_scout_project_comparison": packet.phase_scout_project_excerpt,
            }
        )
    elif dimension == "opportunity_quality":
        base.update(
            {
                "phase_scout_opportunity_comparison": packet.phase_scout_opportunity_excerpt,
            }
        )
    elif dimension == "duplicate_avoidance":
        base.update(
            {
                "phase_scout_duplicate_check": packet.phase_scout_duplicate_excerpt,
                "behavior_summary": packet.behavior_summary,
                "tool_violation_log": packet.tool_violation_excerpt,
            }
        )
    elif dimension == "repository_understanding":
        base.update(
            {
                "behavior_summary": packet.behavior_summary,
                "selected_task_summary": packet.selected_task_summary,
                "goal_events": packet.goal_events_excerpt,
            }
        )
    elif dimension == "execution_correctness":
        base.update(
            {
                "selected_task_summary": packet.selected_task_summary,
                "patch_excerpt": packet.patch_excerpt,
                "quality_gate": packet.quality_gate,
            }
        )
    elif dimension == "verification_quality":
        base.update(
            {
                "verification_excerpt": packet.verification_excerpt,
                "behavior_summary": packet.behavior_summary,
                "quality_gate": packet.quality_gate,
            }
        )
    elif dimension == "review_readiness":
        base.update(
            {
                "pull_request": packet.pull_request,
                "maintainer_outcome": packet.maintainer_outcome,
                "phase_review_maintainer_review": packet.phase_review_maintainer_excerpt,
                "phase_review_response": packet.phase_review_response_excerpt,
                "pr_description_excerpt": packet.pr_description_excerpt,
                "patch_excerpt": packet.patch_excerpt,
                "quality_gate": packet.quality_gate,
            }
        )
    elif dimension == "agentic_judgment":
        base.update(
            {
                "goal_events": packet.goal_events_excerpt,
                "phase_transition": packet.phase_transition_excerpt,
                "tool_violation_log": packet.tool_violation_excerpt,
                "behavior_summary": packet.behavior_summary,
            }
        )
    return base


def _judge_dimension_instructions(dimension: str) -> str:
    return (
        f"You are a ContribArena M0.10 judge. Score only `{dimension}` for one "
        "anonymized run packet. "
        "Do not infer or reward the hidden agent/model identity. Use only the packet evidence "
        "for this dimension plus the provided terminal state and deterministic floors. "
        "List concrete evidence first, then assign an integer score 0-5. "
        "Use this scale: 3 means acceptable with flaws, 4 means very good with no major "
        "issues, and 5 means exceptional, near-perfect evidence and execution. "
        "Do not give 5 for merely adequate work. Apply any deterministic_floors as hard "
        "maximum scores and mention them in evidence. Use 0 only for no valid evidence, "
        "a broken path, or a severe violation. "
        f"The dimension value must be exactly `{dimension}`. "
        f"{_rubric_scale_instructions(dimension)} "
        "Return only JSON with this shape: "
        f'{{"dimension":"{dimension}","evidence":["..."],"score":0}}. '
        "Do not score any other dimension. Do not include markdown."
    )


def _rubric_scale_instructions(dimension: str) -> str:
    anchors = {
        "project_fit": (
            "Anchors for project_fit: 5=compared multiple viable repos or thoroughly audited "
            "the fixed repo, checked activity, value, contribution rules, setup feasibility, "
            "and downsides; 4=good repo fit evidence with minor gaps; 3=eligible but thin "
            "comparison/audit; 2=single shallow signal or notable warnings; 1=surface-only "
            "repo choice; 0=ineligible, archived, hostile to external contribution, or no "
            "project-fit evidence."
        ),
        "opportunity_quality": (
            "Anchors for opportunity_quality: 5=considered multiple issue/code opportunities "
            "with clear value, risk, novelty, maintainer fit, and honest tradeoffs; 4=useful "
            "well-scoped opportunity with credible rationale; 3=real fixable task but weak "
            "breadth or value evidence; 2=plausible but mostly self-invented; 1=surface scan "
            "only; 0=nonexistent, duplicate, misread, or automation noise."
        ),
        "duplicate_avoidance": (
            "Anchors for duplicate_avoidance: 5=checked open and recently merged PRs/issues "
            "with precise queries and no duplicate evidence; 4=credible PR duplicate check "
            "with minor query gaps; 3=some PR/issue duplicate evidence but incomplete; "
            "2=issue-only or weak title search; 1=claim is mostly unsupported; 0=claimed a "
            "duplicate check without PR-tool evidence or selected a known duplicate."
        ),
        "repository_understanding": (
            "Anchors for repository_understanding: 5=read guidance, layout, relevant source, "
            "tests, and plan evidence grounded in real files; 4=key context read with minor "
            "omissions; 3=basic relevant source reading; 2=target file only; 1=almost no "
            "exploration; 0=violates repo guidance or misunderstands structure."
        ),
        "execution_correctness": (
            "Anchors for execution_correctness: 5=precise scoped patch that solves the selected "
            "opportunity with no unrelated side effects and quality gate pass; 4=solves the "
            "main problem with minor edge/style gaps; 3=likely useful but with test/boundary "
            "risk; 2=related but rough or non-pass gate; 1=weak relation; 0=no patch, wrong "
            "file/function, unapplyable patch, obvious bug, or failed terminal status."
        ),
        "verification_quality": (
            "Anchors for verification_quality: 5=targeted reproducible verification with "
            "problem-specific output, relevant failure/retry handling, and coverage of main "
            "risks; 4=tests support conclusion with minor gaps; 3=basic relevant check; "
            "2=only proves code runs; 1=no command-level evidence; 0=failed verification "
            "ignored or misreported."
        ),
        "review_readiness": (
            "Anchors for review_readiness: 5=clear PR description, addressed or "
            "evidence-backed disputed self pre-submission review concerns if present, "
            "low-risk scope, and merge-ready evidence; "
            "4=clear scope and verification with minor review gaps; 3=valuable but needs "
            "questions or small fixes; 2=unclear boundary or unaddressed substantive "
            "pre-review concern; "
            "1=near automation noise; 0=empty/misleading PR description, hidden failure, "
            "spam, opt-out, or policy violation."
        ),
        "agentic_judgment": (
            "Anchors for agentic_judgment: 5=goal transitions, abandon/supersede choices, "
            "budget use, phase-boundary behavior, and initiative are well justified by "
            "evidence; 4=good autonomous judgment with minor inefficiency; 3=reasonable "
            "but thin transition evidence; 2=poor budget/phase discipline or weak evidence; "
            "1=mostly reactive or confused; 0=faked evidence, severe phase violations, or "
            "unjustified abandonment."
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
        "project_selection_quality": "project_fit",
        "opportunity_identification_quality": "opportunity_quality",
        "opportunity_selection_quality": "opportunity_quality",
        "task_selection_quality": "opportunity_quality",
        "repository_understanding_and_plan": "repository_understanding",
        "solution_correctness": "execution_correctness",
        "verification_evidence_quality": "verification_quality",
        "verification_evidence": "verification_quality",
        "maintainer_acceptability": "review_readiness",
        "maintainer_fit": "review_readiness",
        "abandonment_quality": "agentic_judgment",
        "instruction_following": "agentic_judgment",
        "agentic_initiative": "agentic_judgment",
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
    tools_used = {str(tool) for tool in behavior.get("tools_used", []) if tool}
    floors = _ground_truth_check(packet)

    if terminal_status == "failed":
        return [
            _score(
                dimension,
                0,
                [f"terminal_status={terminal_status}", "run failed before valid evidence"],
            )
            for dimension in DIMENSIONS
        ]

    project_score = 2
    if packet.phase_scout_project_excerpt:
        project_score += 2
    elif packet.eligibility_summary:
        project_score += 1
    if packet.repository.get("full_name"):
        project_score += 1

    opportunity_score = 2
    if packet.phase_scout_opportunity_excerpt:
        opportunity_score += 2
    elif packet.selected_task_summary:
        opportunity_score += 1
    if packet.opportunity.get("source") != "none":
        opportunity_score += 1

    duplicate_score = 1
    if packet.phase_scout_duplicate_excerpt:
        duplicate_score = 4
        if {"repo_get_open_prs", "repo_get_recent_merged_prs"} & tools_used:
            duplicate_score += 1
    elif any(tool.startswith("repo_") and "pr" in tool for tool in tools_used):
        duplicate_score = 3

    understanding_score = 2
    if packet.selected_task_summary:
        understanding_score += 1
    if command_count or aci_count:
        understanding_score += 1
    if "repo_guidance.json" in packet.artifacts or packet.goal_events_excerpt:
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

    review_score = 0
    if has_patch:
        review_score = 3
        if pr_state in {"open", "none"}:
            review_score += 1
        if quality_status == "pass":
            review_score += 1
        if packet.phase_review_maintainer_excerpt and not packet.phase_review_response_excerpt:
            review_score = min(review_score, 3)
    elif terminal_status == "blocked":
        review_score = 2

    agentic_score = 3
    if packet.goal_events_excerpt:
        agentic_score += 1
    if packet.phase_transition_excerpt:
        agentic_score += 1
    if packet.tool_violation_excerpt:
        agentic_score -= 1

    rubric = [
        _score(
            "project_fit",
            project_score,
            [
                f"repository={packet.repository.get('full_name', '') or 'unknown'}",
                f"phase_scout_project_present={bool(packet.phase_scout_project_excerpt)}",
            ],
        ),
        _score(
            "opportunity_quality",
            opportunity_score,
            [
                f"opportunity_source={packet.opportunity.get('source', 'none')}",
                f"phase_scout_opportunity_present={bool(packet.phase_scout_opportunity_excerpt)}",
            ],
        ),
        _score(
            "duplicate_avoidance",
            duplicate_score,
            [
                f"phase_scout_duplicate_present={bool(packet.phase_scout_duplicate_excerpt)}",
                f"pr_tools_used={sorted(tool for tool in tools_used if 'pr' in tool)}",
            ],
        ),
        _score(
            "repository_understanding",
            understanding_score,
            [
                f"command_count={command_count}",
                f"aci_step_count={aci_count}",
                f"guidance_artifact_present={'repo_guidance.json' in packet.artifacts}",
            ],
        ),
        _score(
            "execution_correctness",
            correctness_score,
            [
                f"patch_present={has_patch}",
                f"quality_gate={quality_status}",
                f"terminal_status={terminal_status}",
            ],
        ),
        _score(
            "verification_quality",
            verification_score,
            [
                f"verification_attempts={verification_count}",
                f"verification_excerpt_present={bool(packet.verification_excerpt)}",
            ],
        ),
        _score(
            "review_readiness",
            review_score,
            [
                f"pull_request_state={pr_state}",
                f"quality_gate={quality_status}",
                f"pr_description_present={bool(packet.pr_description_excerpt)}",
            ],
        ),
        _score(
            "agentic_judgment",
            agentic_score,
            [
                f"goal_events_present={bool(packet.goal_events_excerpt)}",
                f"phase_transition_present={bool(packet.phase_transition_excerpt)}",
                f"tool_violation_present={bool(packet.tool_violation_excerpt)}",
            ],
        ),
    ]
    return [_apply_floors_to_score(score, floors) for score in rubric]


def _score(dimension: str, score: int, evidence: list[str]) -> JudgementRubricScore:
    return JudgementRubricScore(  # type: ignore[arg-type]
        dimension=dimension,
        evidence=evidence,
        score=max(0, min(5, score)),
    )


def _ground_truth_check(packet: JudgePacket) -> dict[str, list[str]]:
    floors: dict[str, list[str]] = {dimension: [] for dimension in DIMENSIONS}
    behavior = packet.behavior_summary
    tools_used = {str(tool) for tool in behavior.get("tools_used", []) if tool}
    duplicate_text = packet.phase_scout_duplicate_excerpt.lower()
    opportunity_text = packet.phase_scout_opportunity_excerpt.lower()
    goal_text = packet.goal_events_excerpt.lower()
    project_rows = _jsonl_row_count(packet.phase_scout_project_excerpt)
    violation_count = _jsonl_row_count(packet.tool_violation_excerpt)

    if (
        _is_open_discovery_packet(packet)
        and 0 < project_rows <= 1
        and "scout_budget_exhausted" not in packet.phase_scout_project_excerpt.lower()
        and "scout_budget_exhausted" not in packet.phase_transition_excerpt.lower()
    ):
        floors["project_fit"].append(
            "only one candidate scouted in open-discovery mode while budget remained <=2"
        )
    if "selection_invalid" in duplicate_text:
        floors["duplicate_avoidance"].append("selection_invalid flagged by Scout duplicate gate <=1")
    if (
        ("duplicate_check" in opportunity_text or "duplicate_evidence_ref" in opportunity_text)
        and not packet.phase_scout_duplicate_excerpt
        and not any(tool in tools_used for tool in _PR_TOOL_NAMES)
    ):
        floors["duplicate_avoidance"].append("duplicate check claimed without captured PR tool call =0")
    if goal_text.count("unresolved_evidence_ref") > 1 or goal_text.count("invalid_evidence_ref") > 1:
        floors["agentic_judgment"].append("goal update evidence_refs unresolved on more than one attempt <=2")
    if violation_count > 5:
        floors["agentic_judgment"].append("phase_violation count > 5 <=2")
    if (
        "scout_budget_exhausted" in packet.phase_transition_excerpt.lower()
        and "selected_opportunity" not in opportunity_text
    ):
        floors["agentic_judgment"].append(
            "scout budget exhausted without selected_opportunity <=2"
        )
    if (
        (
            "request_changes" in packet.phase_review_maintainer_excerpt.lower()
            or "comment" in packet.phase_review_maintainer_excerpt.lower()
        )
        and not packet.phase_review_response_excerpt
    ):
        floors["review_readiness"].append("substantive pre-review concerns not addressed <=2")
    return {dimension: notes for dimension, notes in floors.items() if notes}


_PR_TOOL_NAMES = {
    "repo_get_open_prs",
    "repo_get_recent_merged_prs",
    "repo_search_prs_by_title",
    "repo_get_issue_linkage",
    "repo_get_pr_review_history",
}


def _is_open_discovery_packet(packet: JudgePacket) -> bool:
    repository = packet.repository
    if repository.get("fixed_candidate") or repository.get("issue_solving"):
        return False
    if packet.opportunity.get("source") == "configured_issue":
        return False
    return bool(repository.get("discovery_mode") == "open" or repository.get("query"))


def _apply_floors_to_score(
    score: JudgementRubricScore,
    floors: dict[str, list[str]],
) -> JudgementRubricScore:
    notes = floors.get(str(score.dimension), [])
    if not notes:
        return score
    capped = score.score
    for note in notes:
        if "=0" in note:
            capped = 0
        elif "<=1" in note:
            capped = min(capped, 1)
        elif "<=2" in note:
            capped = min(capped, 2)
    return score.model_copy(update={"score": capped, "notes": [*score.notes, *notes]})


def _jsonl_row_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


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
    text = _verification_command_excerpt(run_dir)
    if text:
        return text
    for name in ("verification_summary.md", "test_log.txt", "quality_report.md"):
        text = _read_excerpt(run_dir / name, max_chars=4000)
        if text:
            return text
    return ""


def _verification_command_excerpt(run_dir: Path) -> str:
    payload = _read_json(run_dir / "workspace_command.json")
    commands = payload.get("commands", []) if isinstance(payload, dict) else []
    if not isinstance(commands, list):
        return ""
    typed_candidates = [
        command
        for command in commands
        if isinstance(command, dict) and command.get("command_type") == "verification"
    ]
    if typed_candidates:
        candidates = typed_candidates
    else:
        candidates = [
            command
            for command in commands
            if isinstance(command, dict) and _looks_like_verification_command(str(command.get("command", "")))
        ]
    if not candidates:
        candidates = [command for command in commands if isinstance(command, dict) and command.get("exit_code") == 0]
    if not candidates:
        return ""
    lines: list[str] = ["# Verification Command Evidence", ""]
    for command in candidates[-6:]:
        cmd = str(command.get("command", ""))
        stdout = str(command.get("stdout", ""))
        stderr = str(command.get("stderr", ""))
        lines.extend(
            [
                "```bash",
                _cap(cmd, 1200),
                "```",
                f"exit_code={command.get('exit_code')}",
                f"timed_out={command.get('timed_out')}",
            ]
        )
        if stdout:
            lines.extend(["stdout:", "```text", _cap(stdout, 1200), "```"])
        if stderr:
            lines.extend(["stderr:", "```text", _cap(stderr, 1200), "```"])
        lines.append("")
    return "\n".join(lines)[:5000]


def _looks_like_verification_command(command: str) -> bool:
    lowered = command.lower()
    markers = (
        "pytest",
        "compileall",
        "py_compile",
        "tomllib",
        "ruff",
        "mypy",
        "verify",
        "lint",
        "unit",
    )
    return any(marker in lowered for marker in markers)


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


def _read_jsonl_excerpt(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        lines: list[str] = []
        total = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                next_total = total + len(line)
                if lines and next_total > max_chars:
                    lines.append("[truncated]\n")
                    break
                if not lines and next_total > max_chars:
                    return line[:max_chars] + "\n[truncated]"
                lines.append(line)
                total = next_total
        return "".join(lines)
    except OSError:
        return ""


def _cap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[truncated]"


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
