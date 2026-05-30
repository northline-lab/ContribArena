from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.surface import (
    RuntimeStatus,
    RunSummary,
    SurfaceAgent,
    SurfaceArtifact,
    SurfaceJudgement,
    SurfaceMaintainerOutcome,
    SurfacePipelineStage,
    SurfacePullRequest,
    SurfaceQualityGate,
    SurfaceRepository,
    SurfaceRubricScore,
    SurfaceSeason,
)


class SurfaceAgentTest(unittest.TestCase):

    def test_defaults(self) -> None:
        agent = SurfaceAgent()
        self.assertEqual("builtin", agent.name)
        self.assertEqual("", agent.handle)
        self.assertEqual("", agent.participant_id)

    def test_custom_values(self) -> None:
        agent = SurfaceAgent(name="gpt-4", handle="@alice", participant_id="p-123")
        self.assertEqual("gpt-4", agent.name)
        self.assertEqual("@alice", agent.handle)
        self.assertEqual("p-123", agent.participant_id)


class RuntimeStatusTest(unittest.TestCase):

    def test_defaults(self) -> None:
        rs = RuntimeStatus()
        self.assertEqual("", rs.status)
        self.assertEqual("", rs.reason)
        self.assertEqual("", rs.message)
        self.assertEqual(0, rs.attempts)

    def test_custom_values(self) -> None:
        rs = RuntimeStatus(status="running", reason="retry", message="tls error", attempts=3)
        self.assertEqual("running", rs.status)
        self.assertEqual("retry", rs.reason)
        self.assertEqual("tls error", rs.message)
        self.assertEqual(3, rs.attempts)


class SurfaceRepositoryTest(unittest.TestCase):

    def test_defaults(self) -> None:
        repo = SurfaceRepository()
        self.assertEqual("", repo.full_name)
        self.assertEqual("", repo.url)

    def test_custom_values(self) -> None:
        repo = SurfaceRepository(full_name="org/repo", url="https://github.com/org/repo")
        self.assertEqual("org/repo", repo.full_name)
        self.assertEqual("https://github.com/org/repo", repo.url)


class SurfacePipelineStageTest(unittest.TestCase):

    def test_defaults(self) -> None:
        stage = SurfacePipelineStage(stage_id="agent")
        self.assertEqual("agent", stage.stage_id)
        self.assertEqual("unknown", stage.status)
        self.assertEqual("", stage.started_at)
        self.assertEqual("", stage.completed_at)
        self.assertEqual("", stage.summary)
        self.assertEqual([], stage.source_artifacts)

    def test_all_valid_stage_ids(self) -> None:
        for sid in [
            "agent", "repo_discovery", "workspace", "patch_diff",
            "quality_gate", "pull_request", "maintainer_outcome",
        ]:
            stage = SurfacePipelineStage(stage_id=sid)
            self.assertEqual(sid, stage.stage_id)

    def test_invalid_stage_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id="invalid_stage")

    def test_all_valid_pipeline_statuses(self) -> None:
        for ps in [
            "not_started", "running", "passed", "failed",
            "blocked", "skipped", "pending", "unknown",
        ]:
            stage = SurfacePipelineStage(stage_id="agent", status=ps)
            self.assertEqual(ps, stage.status)

    def test_invalid_pipeline_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id="agent", status="not_a_status")


class SurfaceQualityGateTest(unittest.TestCase):

    def test_defaults(self) -> None:
        gate = SurfaceQualityGate()
        self.assertEqual("unknown", gate.status)
        self.assertEqual([], gate.warnings)

    def test_all_valid_statuses(self) -> None:
        for s in ("pass", "block", "fail", "unknown"):
            gate = SurfaceQualityGate(status=s)
            self.assertEqual(s, gate.status)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceQualityGate(status="maybe")

    def test_custom_warnings(self) -> None:
        gate = SurfaceQualityGate(status="block", warnings=["no tests", "diff too large"])
        self.assertEqual(["no tests", "diff too large"], gate.warnings)


class SurfacePullRequestTest(unittest.TestCase):

    def test_defaults(self) -> None:
        pr = SurfacePullRequest()
        self.assertEqual("", pr.url)
        self.assertIsNone(pr.number)
        self.assertEqual("none", pr.state)

    def test_custom_values(self) -> None:
        pr = SurfacePullRequest(url="https://github.com/org/repo/pull/1", number=1, state="open")
        self.assertEqual("https://github.com/org/repo/pull/1", pr.url)
        self.assertEqual(1, pr.number)
        self.assertEqual("open", pr.state)

    def test_all_valid_states(self) -> None:
        for s in ("none", "open", "closed", "merged", "unknown"):
            pr = SurfacePullRequest(state=s)
            self.assertEqual(s, pr.state)

    def test_invalid_state_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePullRequest(state="pending")


class SurfaceMaintainerOutcomeTest(unittest.TestCase):

    def test_defaults(self) -> None:
        outcome = SurfaceMaintainerOutcome()
        self.assertEqual("pending", outcome.status)
        self.assertEqual("", outcome.observed_at)
        self.assertEqual("none", outcome.source)

    def test_all_valid_statuses(self) -> None:
        for s in (
            "pending", "reviewed", "changes_requested",
            "merged", "closed", "stale", "unknown",
        ):
            outcome = SurfaceMaintainerOutcome(status=s)
            self.assertEqual(s, outcome.status)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(status="approved")

    def test_all_valid_sources(self) -> None:
        for src in (
            "github_pr_state", "github_review", "github_comment",
            "ci_status", "manual_adjudication", "none",
        ):
            outcome = SurfaceMaintainerOutcome(source=src)
            self.assertEqual(src, outcome.source)

    def test_invalid_source_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(source="email")


class SurfaceSeasonTest(unittest.TestCase):

    def test_defaults(self) -> None:
        season = SurfaceSeason()
        self.assertEqual("", season.id)
        self.assertEqual("", season.name)
        self.assertEqual("unknown", season.phase)

    def test_all_valid_phases(self) -> None:
        for p in ("owned_repo_calibration", "external_live", "archived", "unknown"):
            season = SurfaceSeason(phase=p)
            self.assertEqual(p, season.phase)

    def test_invalid_phase_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceSeason(phase="beta")


class SurfaceRubricScoreTest(unittest.TestCase):

    def test_dimension_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceRubricScore()

    def test_defaults_with_required_dimension(self) -> None:
        score = SurfaceRubricScore(dimension="code_quality")
        self.assertEqual("code_quality", score.dimension)
        self.assertEqual(0, score.score)
        self.assertEqual(5, score.max_score)
        self.assertEqual(0, score.weight)

    def test_custom_values(self) -> None:
        score = SurfaceRubricScore(dimension="review", score=3.5, max_score=10, weight=0.3)
        self.assertEqual("review", score.dimension)
        self.assertEqual(3.5, score.score)
        self.assertEqual(10, score.max_score)
        self.assertEqual(0.3, score.weight)


class SurfaceJudgementTest(unittest.TestCase):

    def test_defaults(self) -> None:
        j = SurfaceJudgement()
        self.assertEqual("not_judged", j.status)
        self.assertIsNone(j.judge_score)
        self.assertEqual(0, j.real_world_adjustment)
        self.assertIsNone(j.arena_score)
        self.assertEqual([], j.rubric_summary)
        self.assertEqual([], j.source_artifacts)

    def test_all_valid_statuses(self) -> None:
        for s in (
            "not_judged", "judged", "partial_fallback",
            "fallback", "deferred", "failed", "unknown",
        ):
            j = SurfaceJudgement(status=s)
            self.assertEqual(s, j.status)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceJudgement(status="excellent")

    def test_with_nested_rubric_scores(self) -> None:
        scores = [
            SurfaceRubricScore(dimension="code_quality", score=4.0),
            SurfaceRubricScore(dimension="review", score=3.5),
        ]
        j = SurfaceJudgement(status="judged", judge_score=7.5, rubric_summary=scores)
        self.assertEqual(2, len(j.rubric_summary))
        self.assertEqual("code_quality", j.rubric_summary[0].dimension)


class SurfaceArtifactTest(unittest.TestCase):

    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact()

    def test_defaults_with_required_fields(self) -> None:
        art = SurfaceArtifact(name="trace.jsonl", kind="trace")
        self.assertEqual("trace.jsonl", art.name)
        self.assertEqual("trace", art.kind)
        self.assertEqual("internal", art.visibility)
        self.assertEqual("", art.url)
        self.assertEqual(0, art.size_bytes)
        self.assertFalse(art.redacted)

    def test_all_valid_visibilities(self) -> None:
        for v in ("public", "operator", "internal"):
            art = SurfaceArtifact(name="f", kind="k", visibility=v)
            self.assertEqual(v, art.visibility)

    def test_invalid_visibility_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact(name="f", kind="k", visibility="secret")

    def test_custom_values(self) -> None:
        art = SurfaceArtifact(
            name="patch.diff", kind="patch", visibility="public",
            url="https://example.com/patch.diff",
            size_bytes=2048, redacted=True,
        )
        self.assertEqual("patch.diff", art.name)
        self.assertEqual("public", art.visibility)
        self.assertEqual(2048, art.size_bytes)
        self.assertTrue(art.redacted)


class RunSummaryTest(unittest.TestCase):

    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary()

    def test_minimum_valid_instance(self) -> None:
        run = RunSummary(run_id="r-1", run_mode="owned_live", model="gpt-4")
        self.assertEqual("1", run.schema_version)
        self.assertEqual("r-1", run.run_id)
        self.assertEqual("owned_live", run.run_mode)
        self.assertEqual("gpt-4", run.model)
        self.assertEqual("unranked", run.wake_source)
        self.assertIsInstance(run.agent, SurfaceAgent)
        self.assertIsInstance(run.repository, SurfaceRepository)
        self.assertIsInstance(run.season, SurfaceSeason)
        self.assertEqual("none", run.opportunity_source)
        self.assertEqual("", run.opportunity_source_ref)
        self.assertEqual("", run.started_at)
        self.assertEqual("", run.completed_at)
        self.assertIsNone(run.duration_seconds)
        self.assertEqual("unknown", run.run_status)
        self.assertEqual("", run.terminal_reason)
        self.assertEqual("", run.terminal_layer)
        self.assertEqual("unknown", run.contribution_class)
        self.assertEqual([], run.pipeline)
        self.assertIsInstance(run.quality_gate, SurfaceQualityGate)
        self.assertIsInstance(run.pull_request, SurfacePullRequest)
        self.assertIsInstance(run.maintainer_outcome, SurfaceMaintainerOutcome)
        self.assertIsInstance(run.judgement, SurfaceJudgement)
        self.assertEqual([], run.artifacts)
        self.assertEqual({}, run.workspace)
        self.assertEqual({}, run.replacement)
        self.assertEqual({}, run.judgement_retry)
        self.assertEqual({}, run.live_submission_retry)
        self.assertEqual("", run.submission_outcome)
        self.assertEqual("not_judged", run.score_status)
        self.assertTrue(run.ranking_eligible)
        self.assertEqual("", run.ranking_exclusion_reason)
        self.assertEqual("", run.contribution_thread_id)

    def test_invalid_schema_version_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(run_id="r-1", run_mode="owned_live", model="gpt-4", schema_version="2")

    def test_invalid_wake_source_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(run_id="r-1", run_mode="owned_live", model="gpt-4", wake_source="scheduled")

    def test_invalid_contribution_class_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(run_id="r-1", run_mode="owned_live", model="gpt-4", contribution_class="refactor")

    def test_invalid_score_status_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(run_id="r-1", run_mode="owned_live", model="gpt-4", score_status="in_progress")

    def test_nested_pipeline_stages(self) -> None:
        stages = [SurfacePipelineStage(stage_id="agent", status="passed")]
        run = RunSummary(run_id="r-2", run_mode="external_live", model="claude-3", pipeline=stages)
        self.assertEqual(1, len(run.pipeline))
        self.assertEqual("agent", run.pipeline[0].stage_id)

    def test_nested_artifacts(self) -> None:
        artifacts = [SurfaceArtifact(name="trace.jsonl", kind="trace", visibility="public")]
        run = RunSummary(run_id="r-3", run_mode="owned_live", model="gpt-4", artifacts=artifacts)
        self.assertEqual(1, len(run.artifacts))
        self.assertEqual("trace.jsonl", run.artifacts[0].name)

    def test_duration_seconds_none_and_value(self) -> None:
        run_none = RunSummary(run_id="r-4", run_mode="owned_live", model="gpt-4")
        self.assertIsNone(run_none.duration_seconds)
        run_val = RunSummary(run_id="r-5", run_mode="owned_live", model="gpt-4", duration_seconds=42.5)
        self.assertEqual(42.5, run_val.duration_seconds)

    def test_ranking_eligible_toggle(self) -> None:
        run = RunSummary(
            run_id="r-6", run_mode="owned_live", model="gpt-4",
            ranking_eligible=False, ranking_exclusion_reason="duplicate",
        )
        self.assertFalse(run.ranking_eligible)
        self.assertEqual("duplicate", run.ranking_exclusion_reason)
