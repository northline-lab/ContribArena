from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.models.judgement import (
    JudgementDimension,
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


class JudgementSeasonTest(unittest.TestCase):
    def test_required_id(self) -> None:
        s = JudgementSeason(id="season_0")
        self.assertEqual("season_0", s.id)

    def test_defaults(self) -> None:
        s = JudgementSeason(id="s")
        self.assertEqual("", s.name)
        self.assertEqual("unknown", s.phase)

    def test_explicit_phase(self) -> None:
        s = JudgementSeason(id="s", phase="owned_repo_calibration")
        self.assertEqual("owned_repo_calibration", s.phase)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementSeason(id="s", phase="bad_phase")

    def test_missing_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementSeason()


class JudgementTargetTest(unittest.TestCase):
    def test_defaults(self) -> None:
        t = JudgementTarget()
        self.assertEqual("run", t.kind)
        self.assertEqual("", t.pr_url)

    def test_explicit_pr_url(self) -> None:
        t = JudgementTarget(pr_url="https://github.com/owner/repo/pull/1")
        self.assertEqual("https://github.com/owner/repo/pull/1", t.pr_url)


class JudgementPanelTest(unittest.TestCase):
    def test_required_panel_id(self) -> None:
        p = JudgementPanel(panel_id="panel-1")
        self.assertEqual("panel-1", p.panel_id)
        self.assertEqual("mean", p.aggregation)

    def test_missing_panel_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementPanel()


class JudgementRubricScoreTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        r = JudgementRubricScore(dimension="project_fit", score=3)
        self.assertEqual("project_fit", r.dimension)
        self.assertEqual(3, r.score)

    def test_defaults(self) -> None:
        r = JudgementRubricScore(dimension="execution_correctness", score=5)
        self.assertEqual([], r.evidence)
        self.assertEqual([], r.notes)
        self.assertEqual(5, r.max_score)
        self.assertEqual(0, r.weight)
        self.assertEqual("llm", r.source)

    def test_score_bounds_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=6)
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="project_fit", score=-1)

    def test_invalid_dimension_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementRubricScore(dimension="bad_dim", score=1)

    def test_all_dimensions_accepted(self) -> None:
        for dim in get_args(JudgementDimension):
            r = JudgementRubricScore(dimension=dim, score=0)
            self.assertEqual(dim, r.dimension)


class JudgementJudgeResultTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        r = JudgementJudgeResult(judge_id="j1", model="gpt-4", rubric=[], judge_score=4.5)
        self.assertEqual("j1", r.judge_id)
        self.assertEqual("gpt-4", r.model)
        self.assertEqual([], r.rubric)
        self.assertEqual(4.5, r.judge_score)

    def test_default_error(self) -> None:
        r = JudgementJudgeResult(judge_id="j1", model="m", rubric=[], judge_score=0)
        self.assertEqual("", r.error)

    def test_with_rubric_scores(self) -> None:
        score = JudgementRubricScore(dimension="project_fit", score=3)
        r = JudgementJudgeResult(judge_id="j1", model="m", rubric=[score], judge_score=3.0)
        self.assertEqual(1, len(r.rubric))

    def test_missing_required_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementJudgeResult(judge_id="j1")


class JudgementAggregateRubricScoreTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        a = JudgementAggregateRubricScore(
            dimension="project_fit", mean_score=3.5, disagreement=0.5
        )
        self.assertEqual("project_fit", a.dimension)
        self.assertEqual(3.5, a.mean_score)
        self.assertEqual(0.5, a.disagreement)

    def test_defaults(self) -> None:
        a = JudgementAggregateRubricScore(
            dimension="project_fit", mean_score=2.0, disagreement=0.0
        )
        self.assertEqual(5, a.max_score)
        self.assertEqual(0, a.weight)

    def test_invalid_dimension_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementAggregateRubricScore(dimension="bad", mean_score=1.0, disagreement=0.0)


class JudgementMaintainerOutcomeTest(unittest.TestCase):
    def test_defaults(self) -> None:
        m = JudgementMaintainerOutcome()
        self.assertEqual("pending", m.status)
        self.assertEqual("none", m.source)

    def test_explicit_values(self) -> None:
        m = JudgementMaintainerOutcome(status="merged", source="github")
        self.assertEqual("merged", m.status)
        self.assertEqual("github", m.source)


class JudgementArtifactTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        a = JudgementArtifact(
            season_id="season_0",
            run_id="run-1",
            judge_panel=JudgementPanel(panel_id="p1"),
            judge_score=3.5,
            arena_score=3.5,
        )
        self.assertEqual("season_0", a.season_id)
        self.assertEqual("run-1", a.run_id)
        self.assertEqual(3.5, a.judge_score)
        self.assertEqual(3.5, a.arena_score)

    def test_defaults(self) -> None:
        a = JudgementArtifact(
            season_id="s",
            run_id="r",
            judge_panel=JudgementPanel(panel_id="p"),
            judge_score=0,
            arena_score=0,
        )
        self.assertEqual("1", a.schema_version)
        self.assertEqual("judged", a.status)
        self.assertEqual([], a.judges)
        self.assertEqual([], a.aggregate_rubric)
        self.assertEqual(0, a.real_world_adjustment)
        self.assertEqual("", a.created_at)

    def test_all_status_literals(self) -> None:
        for status in ("not_judged", "judged", "partial_fallback", "fallback", "deferred", "failed", "unknown"):
            a = JudgementArtifact(
                season_id="s", run_id="r",
                judge_panel=JudgementPanel(panel_id="p"),
                judge_score=0, arena_score=0, status=status,
            )
            self.assertEqual(status, a.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact(
                season_id="s", run_id="r",
                judge_panel=JudgementPanel(panel_id="p"),
                judge_score=0, arena_score=0, status="bad_status",
            )

    def test_missing_required_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgementArtifact(season_id="s", run_id="r")


class JudgePacketTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        p = JudgePacket(
            season=JudgementSeason(id="season_0"),
            run_id="run-1",
        )
        self.assertEqual("run-1", p.run_id)
        self.assertEqual("season_0", p.season.id)

    def test_defaults(self) -> None:
        p = JudgePacket(season=JudgementSeason(id="s"), run_id="r")
        self.assertEqual("1", p.schema_version)
        self.assertEqual({}, p.repository)
        self.assertEqual({}, p.opportunity)
        self.assertEqual([], p.pipeline)
        self.assertEqual("unknown", p.contribution_class)
        self.assertTrue(p.ranking_eligible)
        self.assertEqual("", p.ranking_exclusion_reason)

    def test_default_factory_independence(self) -> None:
        p1 = JudgePacket(season=JudgementSeason(id="s"), run_id="r")
        p2 = JudgePacket(season=JudgementSeason(id="s"), run_id="r")
        p1.pipeline.append({"step": 1})
        self.assertEqual([], p2.pipeline)

    def test_missing_required_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JudgePacket(run_id="r")

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import JudgePacket as JP, JudgementArtifact as JA
        self.assertIs(JP, JudgePacket)
        self.assertIs(JA, JudgementArtifact)


if __name__ == "__main__":
    unittest.main()
