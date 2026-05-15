from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    JudgementJudgeConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.judgement import judge_run
from contribarena.models.judgement import JudgePacket, JudgementSeason


def _packet(
    *,
    terminal_status: str = "completed",
    pr_state: str = "open",
    maintainer_status: str = "pending",
) -> JudgePacket:
    return JudgePacket(
        season=JudgementSeason(id="season_0", name="Season 0", phase="owned_repo_calibration"),
        run_id="run-1",
        repository={"full_name": "example/repo"},
        opportunity={"source": "issue_url", "source_ref": "https://github.com/example/repo/issues/1"},
        terminal={"status": terminal_status},
        quality_gate={"status": "pass"},
        pull_request={"state": pr_state, "url": "https://github.com/example/repo/pull/1"},
        maintainer_outcome={"status": maintainer_status, "source": "test"},
        contribution_class="low_risk_code",
        artifacts=["repo_guidance.json", "quality_gate.json", "patch.diff", "pr_description.md"],
        selected_task_summary="Fix a small correctness issue.",
        eligibility_summary="Repository is eligible for the season.",
        maintainer_fit_summary="The change follows repository guidance.",
        behavior_summary={"verification_attempts": 3, "command_count": 2, "aci_step_count": 8},
        patch_excerpt="diff --git a/app.py b/app.py\n-old\n+new\n",
        pr_description_excerpt="Fixes the reported issue with a small patch.",
        verification_excerpt="compileall passed",
    )


class JudgementScoringTests(unittest.TestCase):
    def test_real_world_merged_adjustment_can_push_arena_score_above_100(self) -> None:
        packet = _packet(pr_state="merged", maintainer_status="merged")

        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        self.assertGreater(judgement.judge_score, 70)
        self.assertEqual(30, judgement.real_world_adjustment)
        self.assertEqual(round(judgement.judge_score + 30, 2), judgement.arena_score)
        self.assertGreater(judgement.arena_score, 100)

    def test_spam_or_policy_adjustment_floors_arena_score_at_zero(self) -> None:
        packet = _packet(terminal_status="failed", maintainer_status="spam")

        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        self.assertEqual(0.0, judgement.judge_score)
        self.assertEqual(-50, judgement.real_world_adjustment)
        self.assertEqual(0.0, judgement.arena_score)

    def test_judge_score_uses_dimension_weights(self) -> None:
        packet = _packet()
        packet.patch_excerpt = ""

        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            config.judgement.dimension_weights = {
                "project_selection_quality": 0.0,
                "opportunity_identification_quality": 0.0,
                "repository_understanding_and_plan": 0.0,
                "solution_correctness": 1.0,
                "verification_evidence_quality": 0.0,
                "maintainer_acceptability": 0.0,
            }
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        self.assertEqual(0.0, judgement.judge_score)
        self.assertEqual(1.0, judgement.aggregate_rubric[3].weight)
        self.assertTrue(all(score.weight == 1.0 for score in judgement.judges[0].rubric[3:4]))


def _config(output_root: Path) -> RunConfig:
    config = RunConfig(
        run=RunSection(id="run-1", mode="shadow", model="local-stub"),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=output_root),
    )
    config.judgement.judges = [JudgementJudgeConfig(id="judge_a", model="local-stub")]
    return config


if __name__ == "__main__":
    unittest.main()
