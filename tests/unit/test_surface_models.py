from __future__ import annotations

import unittest
from typing import get_args

from pydantic import ValidationError

from contribarena.models.surface import (
    ArtifactVisibility,
    ContributionClass,
    MaintainerOutcomeStatus,
    PipelineStatus,
    RunSummary,
    RuntimeStatus,
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

    def test_explicit_values(self) -> None:
        agent = SurfaceAgent(name="claude", handle="bot", participant_id="season:bot")

        self.assertEqual("claude", agent.name)
        self.assertEqual("bot", agent.handle)
        self.assertEqual("season:bot", agent.participant_id)


class RuntimeStatusTest(unittest.TestCase):
    def test_defaults(self) -> None:
        status = RuntimeStatus()

        self.assertEqual("", status.status)
        self.assertEqual("", status.reason)
        self.assertEqual("", status.message)
        self.assertEqual(0, status.attempts)

    def test_explicit_values(self) -> None:
        status = RuntimeStatus(
            status="failed", reason="timeout", message="workspace stalled", attempts=3
        )

        self.assertEqual("failed", status.status)
        self.assertEqual("timeout", status.reason)
        self.assertEqual("workspace stalled", status.message)
        self.assertEqual(3, status.attempts)


class SurfaceRepositoryTest(unittest.TestCase):
    def test_defaults(self) -> None:
        repo = SurfaceRepository()

        self.assertEqual("", repo.full_name)
        self.assertEqual("", repo.url)

    def test_explicit_values(self) -> None:
        repo = SurfaceRepository(
            full_name="owner/repo", url="https://github.com/owner/repo"
        )

        self.assertEqual("owner/repo", repo.full_name)
        self.assertEqual("https://github.com/owner/repo", repo.url)


class SurfacePipelineStageTest(unittest.TestCase):
    def test_required_stage_id(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage()  # type: ignore[call-arg]

    def test_defaults(self) -> None:
        stage = SurfacePipelineStage(stage_id="agent")

        self.assertEqual("agent", stage.stage_id)
        self.assertEqual("unknown", stage.status)
        self.assertEqual("", stage.started_at)
        self.assertEqual("", stage.completed_at)
        self.assertEqual("", stage.summary)
        self.assertEqual([], stage.source_artifacts)

    def test_all_stage_id_literals_accepted(self) -> None:
        for stage_id in (
            "agent",
            "repo_discovery",
            "workspace",
            "patch_diff",
            "quality_gate",
            "pull_request",
            "maintainer_outcome",
        ):
            stage = SurfacePipelineStage(stage_id=stage_id)
            self.assertEqual(stage_id, stage.stage_id)

    def test_invalid_stage_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id="nonexistent_stage")  # type: ignore[arg-type]

    def test_all_status_literals_accepted(self) -> None:
        for status in get_args(PipelineStatus):
            stage = SurfacePipelineStage(stage_id="agent", status=status)
            self.assertEqual(status, stage.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id="agent", status="nope")  # type: ignore[arg-type]

    def test_source_artifacts_factory_independence(self) -> None:
        first = SurfacePipelineStage(stage_id="agent")
        second = SurfacePipelineStage(stage_id="workspace")

        first.source_artifacts.append("a.json")

        self.assertEqual(["a.json"], first.source_artifacts)
        self.assertEqual([], second.source_artifacts)


class SurfaceQualityGateTest(unittest.TestCase):
    def test_defaults(self) -> None:
        gate = SurfaceQualityGate()

        self.assertEqual("unknown", gate.status)
        self.assertEqual([], gate.warnings)

    def test_all_status_literals_accepted(self) -> None:
        for status in ("pass", "block", "fail", "unknown"):
            gate = SurfaceQualityGate(status=status)
            self.assertEqual(status, gate.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceQualityGate(status="warning")  # type: ignore[arg-type]

    def test_warnings_factory_independence(self) -> None:
        first = SurfaceQualityGate()
        second = SurfaceQualityGate()

        first.warnings.append("missing license")

        self.assertEqual(["missing license"], first.warnings)
        self.assertEqual([], second.warnings)


class SurfacePullRequestTest(unittest.TestCase):
    def test_defaults(self) -> None:
        pr = SurfacePullRequest()

        self.assertEqual("", pr.url)
        self.assertIsNone(pr.number)
        self.assertEqual("none", pr.state)

    def test_all_state_literals_accepted(self) -> None:
        for state in ("none", "open", "closed", "merged", "unknown"):
            pr = SurfacePullRequest(state=state)
            self.assertEqual(state, pr.state)

    def test_invalid_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePullRequest(state="draft")  # type: ignore[arg-type]

    def test_number_can_be_set(self) -> None:
        pr = SurfacePullRequest(
            url="https://github.com/owner/repo/pull/7", number=7, state="open"
        )

        self.assertEqual(7, pr.number)
        self.assertEqual("open", pr.state)


class SurfaceMaintainerOutcomeTest(unittest.TestCase):
    def test_defaults(self) -> None:
        outcome = SurfaceMaintainerOutcome()

        self.assertEqual("pending", outcome.status)
        self.assertEqual("", outcome.observed_at)
        self.assertEqual("none", outcome.source)

    def test_all_status_literals_accepted(self) -> None:
        for status in get_args(MaintainerOutcomeStatus):
            outcome = SurfaceMaintainerOutcome(status=status)
            self.assertEqual(status, outcome.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(status="approved")  # type: ignore[arg-type]

    def test_all_source_literals_accepted(self) -> None:
        for source in (
            "github_pr_state",
            "github_review",
            "github_comment",
            "ci_status",
            "manual_adjudication",
            "none",
        ):
            outcome = SurfaceMaintainerOutcome(source=source)
            self.assertEqual(source, outcome.source)

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(source="slack")  # type: ignore[arg-type]


class SurfaceSeasonTest(unittest.TestCase):
    def test_defaults(self) -> None:
        season = SurfaceSeason()

        self.assertEqual("", season.id)
        self.assertEqual("", season.name)
        self.assertEqual("unknown", season.phase)

    def test_all_phase_literals_accepted(self) -> None:
        for phase in ("owned_repo_calibration", "external_live", "archived", "unknown"):
            season = SurfaceSeason(phase=phase)
            self.assertEqual(phase, season.phase)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceSeason(phase="draft")  # type: ignore[arg-type]


class SurfaceRubricScoreTest(unittest.TestCase):
    def test_required_dimension(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceRubricScore()  # type: ignore[call-arg]

    def test_defaults(self) -> None:
        score = SurfaceRubricScore(dimension="correctness")

        self.assertEqual("correctness", score.dimension)
        self.assertEqual(0, score.score)
        self.assertEqual(5, score.max_score)
        self.assertEqual(0, score.weight)

    def test_explicit_values(self) -> None:
        score = SurfaceRubricScore(
            dimension="clarity", score=4.5, max_score=5, weight=0.25
        )

        self.assertEqual("clarity", score.dimension)
        self.assertEqual(4.5, score.score)
        self.assertEqual(5, score.max_score)
        self.assertEqual(0.25, score.weight)


class SurfaceJudgementTest(unittest.TestCase):
    def test_defaults(self) -> None:
        judgement = SurfaceJudgement()

        self.assertEqual("not_judged", judgement.status)
        self.assertIsNone(judgement.judge_score)
        self.assertEqual(0, judgement.real_world_adjustment)
        self.assertIsNone(judgement.arena_score)
        self.assertEqual([], judgement.rubric_summary)
        self.assertEqual([], judgement.source_artifacts)

    def test_all_status_literals_accepted(self) -> None:
        for status in (
            "not_judged",
            "judged",
            "partial_fallback",
            "fallback",
            "deferred",
            "failed",
            "unknown",
        ):
            judgement = SurfaceJudgement(status=status)
            self.assertEqual(status, judgement.status)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceJudgement(status="complete")  # type: ignore[arg-type]

    def test_nested_rubric_summary(self) -> None:
        judgement = SurfaceJudgement(
            status="judged",
            judge_score=72.5,
            real_world_adjustment=10,
            arena_score=82.5,
            rubric_summary=[SurfaceRubricScore(dimension="correctness", score=4)],
            source_artifacts=["judgement.json"],
        )

        self.assertEqual("judged", judgement.status)
        self.assertEqual(72.5, judgement.judge_score)
        self.assertEqual(82.5, judgement.arena_score)
        self.assertEqual(1, len(judgement.rubric_summary))
        self.assertIsInstance(judgement.rubric_summary[0], SurfaceRubricScore)
        self.assertEqual(["judgement.json"], judgement.source_artifacts)

    def test_list_factory_independence(self) -> None:
        first = SurfaceJudgement()
        second = SurfaceJudgement()

        first.rubric_summary.append(SurfaceRubricScore(dimension="x"))
        first.source_artifacts.append("j.json")

        self.assertEqual(1, len(first.rubric_summary))
        self.assertEqual(["j.json"], first.source_artifacts)
        self.assertEqual([], second.rubric_summary)
        self.assertEqual([], second.source_artifacts)


class SurfaceArtifactTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact()  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            SurfaceArtifact(name="trace.json")  # type: ignore[call-arg]

    def test_defaults(self) -> None:
        artifact = SurfaceArtifact(name="trace.json", kind="trace")

        self.assertEqual("trace.json", artifact.name)
        self.assertEqual("trace", artifact.kind)
        self.assertEqual("internal", artifact.visibility)
        self.assertEqual("", artifact.url)
        self.assertEqual(0, artifact.size_bytes)
        self.assertFalse(artifact.redacted)

    def test_all_visibility_literals_accepted(self) -> None:
        for visibility in get_args(ArtifactVisibility):
            artifact = SurfaceArtifact(
                name="a.json", kind="trace", visibility=visibility
            )
            self.assertEqual(visibility, artifact.visibility)

    def test_invalid_visibility_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact(name="a", kind="trace", visibility="private")  # type: ignore[arg-type]


class RunSummaryTest(unittest.TestCase):
    def _required(self) -> dict[str, str]:
        return {"run_id": "run-1", "run_mode": "shadow", "model": "claude-test"}

    def test_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary()  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            RunSummary(run_id="run-1")  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            RunSummary(run_id="run-1", run_mode="shadow")  # type: ignore[call-arg]

    def test_defaults(self) -> None:
        summary = RunSummary(**self._required())

        self.assertEqual("1", summary.schema_version)
        self.assertEqual("run-1", summary.run_id)
        self.assertEqual("shadow", summary.run_mode)
        self.assertEqual("claude-test", summary.model)
        self.assertEqual("unranked", summary.wake_source)
        self.assertIsInstance(summary.agent, SurfaceAgent)
        self.assertIsInstance(summary.repository, SurfaceRepository)
        self.assertIsInstance(summary.season, SurfaceSeason)
        self.assertEqual("none", summary.opportunity_source)
        self.assertEqual("", summary.opportunity_source_ref)
        self.assertEqual("", summary.started_at)
        self.assertEqual("", summary.completed_at)
        self.assertIsNone(summary.duration_seconds)
        self.assertEqual("unknown", summary.run_status)
        self.assertEqual("", summary.terminal_reason)
        self.assertEqual("", summary.terminal_layer)
        self.assertEqual("unknown", summary.contribution_class)
        self.assertEqual([], summary.pipeline)
        self.assertIsInstance(summary.quality_gate, SurfaceQualityGate)
        self.assertIsInstance(summary.pull_request, SurfacePullRequest)
        self.assertIsInstance(summary.maintainer_outcome, SurfaceMaintainerOutcome)
        self.assertIsInstance(summary.judgement, SurfaceJudgement)
        self.assertEqual([], summary.artifacts)
        self.assertEqual({}, summary.workspace)
        self.assertEqual({}, summary.replacement)
        self.assertEqual({}, summary.judgement_retry)
        self.assertEqual({}, summary.live_submission_retry)
        self.assertEqual("", summary.submission_outcome)
        self.assertEqual("not_judged", summary.score_status)
        self.assertTrue(summary.ranking_eligible)
        self.assertEqual("", summary.ranking_exclusion_reason)
        self.assertEqual("", summary.contribution_thread_id)

    def test_schema_version_locked_to_1(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(schema_version="2", **self._required())  # type: ignore[arg-type]

    def test_all_wake_source_literals_accepted(self) -> None:
        for wake_source in ("manual", "auto", "unranked"):
            summary = RunSummary(wake_source=wake_source, **self._required())
            self.assertEqual(wake_source, summary.wake_source)

    def test_invalid_wake_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(wake_source="cron", **self._required())  # type: ignore[arg-type]

    def test_all_opportunity_source_literals_accepted(self) -> None:
        for source in ("issue_url", "discovery_event_id", "none"):
            summary = RunSummary(opportunity_source=source, **self._required())
            self.assertEqual(source, summary.opportunity_source)

    def test_invalid_opportunity_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(opportunity_source="random", **self._required())  # type: ignore[arg-type]

    def test_all_contribution_class_literals_accepted(self) -> None:
        for cls in get_args(ContributionClass):
            summary = RunSummary(contribution_class=cls, **self._required())
            self.assertEqual(cls, summary.contribution_class)

    def test_invalid_contribution_class_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(contribution_class="refactor", **self._required())  # type: ignore[arg-type]

    def test_all_score_status_literals_accepted(self) -> None:
        for status in ("scored", "diagnostic_only", "not_judged", "deferred", "failed"):
            summary = RunSummary(score_status=status, **self._required())
            self.assertEqual(status, summary.score_status)

    def test_invalid_score_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(score_status="pending", **self._required())  # type: ignore[arg-type]

    def test_factory_independence(self) -> None:
        first = RunSummary(**self._required())
        second = RunSummary(**self._required())

        first.pipeline.append(SurfacePipelineStage(stage_id="agent"))
        first.artifacts.append(SurfaceArtifact(name="a", kind="trace"))
        first.workspace["key"] = "value"

        self.assertEqual(1, len(first.pipeline))
        self.assertEqual(1, len(first.artifacts))
        self.assertEqual({"key": "value"}, first.workspace)
        self.assertEqual([], second.pipeline)
        self.assertEqual([], second.artifacts)
        self.assertEqual({}, second.workspace)

    def test_importable_from_models_package(self) -> None:
        from contribarena.models import RunSummary as PackageRunSummary

        self.assertIs(RunSummary, PackageRunSummary)


if __name__ == "__main__":
    unittest.main()
