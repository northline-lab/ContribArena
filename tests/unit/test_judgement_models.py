from __future__ import annotations

import unittest

from pydantic import ValidationError

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


class JudgementDimensionTest(unittest.TestCase):
    """JudgementDimension is a Literal type alias with 9 valid values."""

    def test_all_dimension_values_accepted(self) -> None:
        expected = [
            "project_fit",
            "opportunity_quality",
            "duplicate_avoidance",
            "repository_understanding",
            "execution_correctness",
            "verification_quality",
            "submission_discipline",
            "review_readiness",
            "agentic_judgment",
        ]
        for dim in expected:
            score = JudgementRubricScore(dimension=dim, score=3)
            self.assertEqual(dim, score.dimension)

    def test_invalid_dimension_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="invalid_dimension", score=3)


class JudgementStatusTest(unittest.TestCase):
    """JudgementStatus is a Literal type alias with 7 valid values."""

    def test_all_status_values_accepted(self) -> None:
        expected = [
            "not_judged",
            "judged",
            "partial_fallback",
            "fallback",
            "deferred",
            "failed",
            "unknown",
        ]
        for status in expected:
            artifact = JudgementArtifact(
                season_id="s1",
                run_id="r1",
                judge_panel=JudgementPanel(panel_id="p1"),
                judge_score=4.0,
                arena_score=4.0,
                status=status,
            )
            self.assertEqual(status, artifact.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact(
                season_id="s1",
                run_id="r1",
                judge_panel=JudgementPanel(panel_id="p1"),
                judge_score=4.0,
                arena_score=4.0,
                status="invalid_status",
            )


class JudgementSeasonTest(unittest.TestCase):
    """JudgementSeason has 1 required field and 2 defaults."""

    def test_required_fields(self) -> None:
        season = JudgementSeason(id="season_0")
        self.assertEqual("season_0", season.id)

    def test_defaults(self) -> None:
        season = JudgementSeason(id="season_0")
        self.assertEqual("", season.name)
        self.assertEqual("unknown", season.phase)

    def test_explicit_values(self) -> None:
        season = JudgementSeason(
            id="season_1",
            name="Season 1",
            phase="owned_repo_calibration",
        )
        self.assertEqual("season_1", season.id)
        self.assertEqual("Season 1", season.name)
        self.assertEqual("owned_repo_calibration", season.phase)

    def test_all_phase_values_accepted(self) -> None:
        phases = ["owned_repo_calibration", "external_live", "archived", "unknown"]
        for phase in phases:
            season = JudgementSeason(id="s1", phase=phase)
            self.assertEqual(phase, season.phase)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementSeason(id="s1", phase="invalid_phase")

    def test_missing_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementSeason()


class JudgementTargetTest(unittest.TestCase):
    """JudgementTarget has no required fields, 2 defaults."""

    def test_defaults(self) -> None:
        target = JudgementTarget()
        self.assertEqual("run", target.kind)
        self.assertEqual("", target.pr_url)

    def test_explicit_values(self) -> None:
        target = JudgementTarget(kind="run", pr_url="https://github.com/repo/pull/1")
        self.assertEqual("run", target.kind)
        self.assertEqual("https://github.com/repo/pull/1", target.pr_url)

    def test_kind_must_be_run(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementTarget(kind="other")


class JudgementPanelTest(unittest.TestCase):
    """JudgementPanel has 1 required field and 1 default."""

    def test_required_field(self) -> None:
        panel = JudgementPanel(panel_id="panel_1")
        self.assertEqual("panel_1", panel.panel_id)

    def test_default_aggregation(self) -> None:
        panel = JudgementPanel(panel_id="panel_1")
        self.assertEqual("mean", panel.aggregation)

    def test_explicit_aggregation(self) -> None:
        panel = JudgementPanel(panel_id="panel_1", aggregation="mean")
        self.assertEqual("mean", panel.aggregation)

    def test_invalid_aggregation_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementPanel(panel_id="panel_1", aggregation="median")

    def test_missing_panel_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementPanel()


class JudgementRubricScoreTest(unittest.TestCase):
    """JudgementRubricScore has 1 required field and 6 defaults with constraints."""

    def test_required_fields(self) -> None:
        score = JudgementRubricScore(dimension="project_fit", score=4)
        self.assertEqual("project_fit", score.dimension)
        self.assertEqual(4, score.score)

    def test_defaults(self) -> None:
        score = JudgementRubricScore(dimension="project_fit", score=3)
        self.assertEqual([], score.evidence)
        self.assertEqual([], score.notes)
        self.assertEqual(5, score.max_score)
        self.assertEqual(0, score.weight)
        self.assertEqual("llm", score.source)

    def test_explicit_values(self) -> None:
        score = JudgementRubricScore(
            dimension="execution_correctness",
            evidence=["test passes", "code compiles"],
            notes=["good implementation"],
            score=5,
            max_score=5,
            weight=1.5,
            source="fallback",
        )
        self.assertEqual("execution_correctness", score.dimension)
        self.assertEqual(["test passes", "code compiles"], score.evidence)
        self.assertEqual(["good implementation"], score.notes)
        self.assertEqual(5, score.score)
        self.assertEqual(5, score.max_score)
        self.assertEqual(1.5, score.weight)
        self.assertEqual("fallback", score.source)

    def test_score_bounds_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=-1)
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=6)

    def test_weight_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=3, weight=-0.5)

    def test_source_literal_values(self) -> None:
        for source in ["llm", "fallback"]:
            score = JudgementRubricScore(
                dimension="project_fit", score=3, source=source
            )
            self.assertEqual(source, score.source)

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=3, source="human")

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit")
        with self.assertRaises(ValidationError):
            JudgementRubricScore(score=3)

    def test_evidence_factory_independence(self) -> None:
        score1 = JudgementRubricScore(dimension="project_fit", score=3)
        score2 = JudgementRubricScore(dimension="project_fit", score=4)
        score1.evidence.append("test")
        self.assertEqual([], score2.evidence)


class JudgementJudgeResultTest(unittest.TestCase):
    """JudgementJudgeResult has 4 required fields and 1 default."""

    def test_required_fields(self) -> None:
        rubric = [JudgementRubricScore(dimension="project_fit", score=4)]
        result = JudgementJudgeResult(
            judge_id="judge_1",
            model="gpt-4",
            rubric=rubric,
            judge_score=4.5,
        )
        self.assertEqual("judge_1", result.judge_id)
        self.assertEqual("gpt-4", result.model)
        self.assertEqual(rubric, result.rubric)
        self.assertEqual(4.5, result.judge_score)

    def test_default_error(self) -> None:
        rubric = [JudgementRubricScore(dimension="project_fit", score=4)]
        result = JudgementJudgeResult(
            judge_id="judge_1",
            model="gpt-4",
            rubric=rubric,
            judge_score=4.5,
        )
        self.assertEqual("", result.error)

    def test_explicit_error(self) -> None:
        rubric = [JudgementRubricScore(dimension="project_fit", score=4)]
        result = JudgementJudgeResult(
            judge_id="judge_1",
            model="gpt-4",
            rubric=rubric,
            judge_score=4.5,
            error="timeout",
        )
        self.assertEqual("timeout", result.error)

    def test_judge_score_must_be_non_negative(self) -> None:
        rubric = [JudgementRubricScore(dimension="project_fit", score=4)]
        with self.assertRaises(ValidationError):
            JudgementJudgeResult(
                judge_id="judge_1",
                model="gpt-4",
                rubric=rubric,
                judge_score=-1.0,
            )

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementJudgeResult()
        rubric = [JudgementRubricScore(dimension="project_fit", score=4)]
        with self.assertRaises(ValidationError):
            JudgementJudgeResult(judge_id="j1", model="m", rubric=rubric)


class JudgementAggregateRubricScoreTest(unittest.TestCase):
    """JudgementAggregateRubricScore has 4 required fields and 1 default."""

    def test_required_fields(self) -> None:
        score = JudgementAggregateRubricScore(
            dimension="project_fit",
            mean_score=4.2,
            max_score=5,
            disagreement=0.3,
        )
        self.assertEqual("project_fit", score.dimension)
        self.assertEqual(4.2, score.mean_score)
        self.assertEqual(5, score.max_score)
        self.assertEqual(0.3, score.disagreement)

    def test_default_weight(self) -> None:
        score = JudgementAggregateRubricScore(
            dimension="project_fit",
            mean_score=4.2,
            max_score=5,
            disagreement=0.3,
        )
        self.assertEqual(0, score.weight)

    def test_explicit_weight(self) -> None:
        score = JudgementAggregateRubricScore(
            dimension="project_fit",
            mean_score=4.2,
            max_score=5,
            disagreement=0.3,
            weight=2.0,
        )
        self.assertEqual(2.0, score.weight)

    def test_mean_score_bounds_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore(
                dimension="project_fit",
                mean_score=-0.1,
                max_score=5,
                disagreement=0.3,
            )
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore(
                dimension="project_fit",
                mean_score=5.1,
                max_score=5,
                disagreement=0.3,
            )

    def test_weight_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore(
                dimension="project_fit",
                mean_score=4.0,
                max_score=5,
                disagreement=0.3,
                weight=-1.0,
            )

    def test_disagreement_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore(
                dimension="project_fit",
                mean_score=4.0,
                max_score=5,
                disagreement=-0.1,
            )

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore()


class JudgementMaintainerOutcomeTest(unittest.TestCase):
    """JudgementMaintainerOutcome has no required fields, 2 defaults."""

    def test_defaults(self) -> None:
        outcome = JudgementMaintainerOutcome()
        self.assertEqual("pending", outcome.status)
        self.assertEqual("none", outcome.source)

    def test_explicit_values(self) -> None:
        outcome = JudgementMaintainerOutcome(
            status="merged", source="github_api"
        )
        self.assertEqual("merged", outcome.status)
        self.assertEqual("github_api", outcome.source)


class JudgementArtifactTest(unittest.TestCase):
    """JudgementArtifact has 5 required fields and 12 defaults."""

    def test_required_fields(self) -> None:
        artifact = JudgementArtifact(
            season_id="season_0",
            run_id="run_123",
            judge_panel=JudgementPanel(panel_id="p1"),
            judge_score=4.5,
            arena_score=4.5,
        )
        self.assertEqual("season_0", artifact.season_id)
        self.assertEqual("run_123", artifact.run_id)
        self.assertEqual("p1", artifact.judge_panel.panel_id)
        self.assertEqual(4.5, artifact.judge_score)
        self.assertEqual(4.5, artifact.arena_score)

    def test_defaults(self) -> None:
        artifact = JudgementArtifact(
            season_id="s1",
            run_id="r1",
            judge_panel=JudgementPanel(panel_id="p1"),
            judge_score=4.0,
            arena_score=4.0,
        )
        self.assertEqual("1", artifact.schema_version)
        self.assertEqual("judged", artifact.status)
        self.assertEqual("judge_packet.json", artifact.judge_packet)
        self.assertEqual(
            "judge_dimension_packets.json", artifact.judge_dimension_packets
        )
        self.assertEqual([], artifact.judges)
        self.assertEqual([], artifact.aggregate_rubric)
        self.assertEqual(0, artifact.real_world_adjustment)
        self.assertEqual("pending", artifact.maintainer_outcome.status)
        self.assertEqual("none", artifact.maintainer_outcome.source)
        self.assertEqual([], artifact.evidence)
        self.assertEqual("", artifact.created_at)

    def test_explicit_values(self) -> None:
        panel = JudgementPanel(panel_id="panel_1")
        target = JudgementTarget(pr_url="https://github.com/repo/pull/1")
        outcome = JudgementMaintainerOutcome(status="merged", source="github")
        artifact = JudgementArtifact(
            season_id="season_1",
            run_id="run_456",
            target=target,
            judge_panel=panel,
            judge_score=4.8,
            arena_score=4.9,
            status="judged",
            real_world_adjustment=1,
            maintainer_outcome=outcome,
            evidence=["pr_merged", "maintainer_approved"],
            created_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual("season_1", artifact.season_id)
        self.assertEqual("run_456", artifact.run_id)
        self.assertEqual("https://github.com/repo/pull/1", artifact.target.pr_url)
        self.assertEqual("panel_1", artifact.judge_panel.panel_id)
        self.assertEqual(4.8, artifact.judge_score)
        self.assertEqual(4.9, artifact.arena_score)
        self.assertEqual("judged", artifact.status)
        self.assertEqual(1, artifact.real_world_adjustment)
        self.assertEqual("merged", artifact.maintainer_outcome.status)
        self.assertEqual("github", artifact.maintainer_outcome.source)
        self.assertEqual(["pr_merged", "maintainer_approved"], artifact.evidence)
        self.assertEqual("2026-01-01T00:00:00Z", artifact.created_at)

    def test_judge_score_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact(
                season_id="s1",
                run_id="r1",
                judge_panel=JudgementPanel(panel_id="p1"),
                judge_score=-1.0,
                arena_score=4.0,
            )

    def test_arena_score_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact(
                season_id="s1",
                run_id="r1",
                judge_panel=JudgementPanel(panel_id="p1"),
                judge_score=4.0,
                arena_score=-0.1,
            )

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact()
        with self.assertRaises(ValidationError):
            JudgementArtifact(season_id="s1", run_id="r1")

    def test_evidence_factory_independence(self) -> None:
        artifact1 = JudgementArtifact(
            season_id="s1",
            run_id="r1",
            judge_panel=JudgementPanel(panel_id="p1"),
            judge_score=4.0,
            arena_score=4.0,
        )
        artifact2 = JudgementArtifact(
            season_id="s2",
            run_id="r2",
            judge_panel=JudgementPanel(panel_id="p2"),
            judge_score=4.5,
            arena_score=4.5,
        )
        artifact1.evidence.append("test")
        self.assertEqual([], artifact2.evidence)


class JudgePacketTest(unittest.TestCase):
    """JudgePacket has 2 required fields and 26 defaults."""

    def test_required_fields(self) -> None:
        season = JudgementSeason(id="season_0")
        packet = JudgePacket(season=season, run_id="run_123")
        self.assertEqual("season_0", packet.season.id)
        self.assertEqual("run_123", packet.run_id)

    def test_defaults(self) -> None:
        season = JudgementSeason(id="season_0")
        packet = JudgePacket(season=season, run_id="run_123")
        self.assertEqual("1", packet.schema_version)
        self.assertEqual({}, packet.repository)
        self.assertEqual({}, packet.opportunity)
        self.assertEqual({}, packet.terminal)
        self.assertEqual([], packet.pipeline)
        self.assertEqual({}, packet.quality_gate)
        self.assertEqual({}, packet.pull_request)
        self.assertEqual({}, packet.maintainer_outcome)
        self.assertEqual("unknown", packet.contribution_class)
        self.assertEqual([], packet.artifacts)
        self.assertEqual("", packet.selected_task_summary)
        self.assertEqual("", packet.eligibility_summary)
        self.assertEqual("", packet.maintainer_fit_summary)
        self.assertEqual({}, packet.behavior_summary)
        self.assertEqual("", packet.patch_excerpt)
        self.assertEqual("", packet.pr_description_excerpt)
        self.assertEqual("", packet.verification_excerpt)
        self.assertEqual("", packet.discovery_calls_summary)
        self.assertEqual("", packet.phase_scout_project_excerpt)
        self.assertEqual("", packet.phase_scout_opportunity_excerpt)
        self.assertEqual("", packet.phase_scout_duplicate_excerpt)
        self.assertEqual("", packet.phase_review_maintainer_excerpt)
        self.assertEqual("", packet.phase_review_response_excerpt)
        self.assertEqual("", packet.live_action_excerpt)
        self.assertEqual("", packet.submission_outcome)
        self.assertEqual("", packet.score_status)
        self.assertEqual(True, packet.ranking_eligible)
        self.assertEqual("", packet.ranking_exclusion_reason)
        self.assertEqual("", packet.goal_events_excerpt)
        self.assertEqual("", packet.phase_transition_excerpt)
        self.assertEqual("", packet.tool_violation_excerpt)

    def test_explicit_values(self) -> None:
        season = JudgementSeason(id="season_1", name="Season 1")
        packet = JudgePacket(
            season=season,
            run_id="run_456",
            repository={"owner": "test", "repo": "example"},
            contribution_class="test_addition",
            artifacts=["patch.diff", "test_results.json"],
            ranking_eligible=False,
            ranking_exclusion_reason="budget_exhausted",
        )
        self.assertEqual("season_1", packet.season.id)
        self.assertEqual("Season 1", packet.season.name)
        self.assertEqual("run_456", packet.run_id)
        self.assertEqual({"owner": "test", "repo": "example"}, packet.repository)
        self.assertEqual("test_addition", packet.contribution_class)
        self.assertEqual(["patch.diff", "test_results.json"], packet.artifacts)
        self.assertEqual(False, packet.ranking_eligible)
        self.assertEqual("budget_exhausted", packet.ranking_exclusion_reason)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JudgePacket()
        with self.assertRaises(ValidationError):
            JudgePacket(season=JudgementSeason(id="s1"))

    def test_repository_factory_independence(self) -> None:
        season = JudgementSeason(id="season_0")
        packet1 = JudgePacket(season=season, run_id="r1")
        packet2 = JudgePacket(season=season, run_id="r2")
        packet1.repository["test"] = "value"
        self.assertEqual({}, packet2.repository)


class JudgementImportTest(unittest.TestCase):
    """All judgement model classes are importable from contribarena.models."""

    def test_import_from_models_package(self) -> None:
        from contribarena.models import (
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
        self.assertIsNotNone(JudgePacket)
        self.assertIsNotNone(JudgementAggregateRubricScore)
        self.assertIsNotNone(JudgementArtifact)
        self.assertIsNotNone(JudgementJudgeResult)
        self.assertIsNotNone(JudgementMaintainerOutcome)
        self.assertIsNotNone(JudgementPanel)
        self.assertIsNotNone(JudgementRubricScore)
        self.assertIsNotNone(JudgementSeason)
        self.assertIsNotNone(JudgementTarget)


if __name__ == "__main__":
    unittest.main()
