from __future__ import annotations

import tempfile
import json
import unittest
import unittest.mock
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    CompatibleModelConfig,
    DiscoveryConfig,
    JudgementJudgeConfig,
    ModelProvidersConfig,
    ModelsConfig,
    RepoCandidate,
    ResponsesModelConfig,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine import judgement as judgement_module
from contribarena.engine.judge_refresh import (
    mark_transient_judgement_retry_due,
    refresh_due_judgements,
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
        behavior_summary={
            "verification_attempts": 3,
            "command_count": 2,
            "aci_step_count": 8,
            "tools_used": [
                "repo_get_open_prs",
                "repo_get_recent_merged_prs",
                "aci_view",
                "aci_verify",
                "aci_submit_patch",
            ],
        },
        patch_excerpt="diff --git a/app.py b/app.py\n-old\n+new\n",
        pr_description_excerpt="Fixes the reported issue with a small patch.",
        verification_excerpt="compileall passed",
        discovery_calls_summary='{"query":"agent framework","returned_count":3}',
        phase_scout_project_excerpt='{"repo":"example/repo","decision":"selected"}',
        phase_scout_opportunity_excerpt=(
            '{"selected_opportunity":true,"duplicate_evidence_ref":"duplicate:1"}'
        ),
        phase_scout_duplicate_excerpt='{"opportunity_id":"1","tool":"repo_get_open_prs"}',
        goal_events_excerpt='{"status":"active","scope":"contribution"}',
        phase_transition_excerpt='{"phase":"work"}',
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
                "project_fit": 0.0,
                "opportunity_quality": 0.0,
                "duplicate_avoidance": 0.0,
                "repository_understanding": 0.0,
                "execution_correctness": 1.0,
                "verification_quality": 0.0,
                "review_readiness": 0.0,
                "agentic_judgment": 0.0,
            }
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        self.assertEqual(0.0, judgement.judge_score)
        execution = next(
            item for item in judgement.aggregate_rubric if item.dimension == "execution_correctness"
        )
        self.assertEqual(1.0, execution.weight)
        self.assertTrue(
            all(
                score.weight == 1.0
                for score in judgement.judges[0].rubric
                if score.dimension == "execution_correctness"
            )
        )

    def test_judge_emits_m010_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            judgement = judge_run(
                config=config,
                run_id="run-1",
                run_dir=Path(tmp),
                packet=_packet(),
            )

        self.assertEqual(
            [
                "project_fit",
                "opportunity_quality",
                "duplicate_avoidance",
                "repository_understanding",
                "execution_correctness",
                "verification_quality",
                "review_readiness",
                "agentic_judgment",
            ],
            [item.dimension for item in judgement.aggregate_rubric],
        )

    def test_old_dimension_weight_names_are_accepted_as_aliases(self) -> None:
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
                "duplicate_avoidance": 0.0,
                "agentic_judgment": 0.0,
            }
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        self.assertEqual(0.0, judgement.judge_score)

    def test_duplicate_claim_without_pr_tool_is_floored_to_zero(self) -> None:
        packet = _packet()
        packet.phase_scout_duplicate_excerpt = ""
        packet.behavior_summary["tools_used"] = ["aci_search", "aci_view"]

        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            judgement = judge_run(config=config, run_id="run-1", run_dir=Path(tmp), packet=packet)

        duplicate = next(
            item
            for item in judgement.judges[0].rubric
            if item.dimension == "duplicate_avoidance"
        )
        self.assertEqual(0, duplicate.score)
        self.assertTrue(duplicate.notes)

    def test_open_discovery_single_candidate_project_fit_floor(self) -> None:
        packet = _packet()
        packet.repository = {"discovery_mode": "open", "query": "python agent"}
        packet.phase_scout_project_excerpt = '{"tool":"repo.metadata"}\n'

        rubric = judgement_module._heuristic_rubric(packet)

        project_fit = next(item for item in rubric if item.dimension == "project_fit")
        self.assertLessEqual(project_fit.score, 2)
        self.assertIn("only one candidate", project_fit.notes[0])

    def test_verification_excerpt_prioritizes_verification_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "test_log.txt").write_text("early exploration only\n", encoding="utf-8")
            (run_dir / "workspace_command.json").write_text(
                """
                {
                  "commands": [
                    {"command": "git clone https://example.test/repo", "stdout": "", "stderr": "", "exit_code": 0, "timed_out": false},
                    {"command": "python3 -c \\"import tomllib; print('TOML valid')\\"", "stdout": "TOML valid\\n", "stderr": "", "exit_code": 0, "timed_out": false}
                  ]
                }
                """,
                encoding="utf-8",
            )

            excerpt = judgement_module._verification_excerpt(run_dir)

            self.assertIn("Verification Command Evidence", excerpt)
            self.assertIn("tomllib", excerpt)
            self.assertIn("TOML valid", excerpt)
            self.assertNotIn("git clone", excerpt)

    def test_verification_excerpt_prefers_typed_verification_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "workspace_command.json").write_text(
                """
                {
                  "commands": [
                    {
                      "command": "python3 -m pytest tests",
                      "stdout": "setup-like text from an old row\\n",
                      "stderr": "",
                      "exit_code": 0,
                      "timed_out": false,
                      "command_type": "setup"
                    },
                    {
                      "command": "python3 -m compileall .",
                      "stdout": "compile ok\\n",
                      "stderr": "",
                      "exit_code": 0,
                      "timed_out": false,
                      "command_type": "verification"
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )

            excerpt = judgement_module._verification_excerpt(run_dir)

            self.assertIn("compileall", excerpt)
            self.assertIn("compile ok", excerpt)
            self.assertNotIn("pytest tests", excerpt)
            self.assertNotIn("setup-like text", excerpt)

    def test_dimension_packets_are_scoped_to_primary_evidence(self) -> None:
        packet = _packet()

        packets = judgement_module.build_judge_dimension_packets(packet)

        self.assertIn("phase_scout_project_comparison", packets["project_fit"])
        self.assertIn("discovery_calls_summary", packets["project_fit"])
        self.assertNotIn("patch_excerpt", packets["project_fit"])
        self.assertIn("patch_excerpt", packets["execution_correctness"])
        self.assertNotIn("phase_scout_duplicate_check", packets["execution_correctness"])
        self.assertIn("phase_review_response", packets["review_readiness"])
        self.assertNotIn("phase_scout_project_comparison", packets["review_readiness"])

    def test_discovery_excerpt_preserves_jsonl_line_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_summary.json").write_text(
                """
                {
                  "schema_version": "1",
                  "run_id": "run-1",
                  "repository": {"full_name": "example/repo"},
                  "run_status": "completed",
                  "quality_gate": {"status": "pass"},
                  "judgement": {"status": "pending"}
                }
                """,
                encoding="utf-8",
            )
            (run_dir / "discovery_log.jsonl").write_text(
                '{"seq":1,"query":"a"}\n{"seq":2,"query":"b"}\n',
                encoding="utf-8",
            )

            packet = judgement_module.build_judge_packet(
                config=_config(Path(tmp)),
                run_id="run-1",
                run_dir=run_dir,
            )

        self.assertEqual(
            '{"seq":1,"query":"a"}\n{"seq":2,"query":"b"}\n',
            packet.discovery_calls_summary,
        )

    def test_default_judges_use_all_configured_provider_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp), explicit_judges=False)
            config.run.model = "compatible/worker"
            config.models = ModelsConfig(
                providers=ModelProvidersConfig(
                    compatible={
                        "qwen": CompatibleModelConfig(
                            base_url="https://example.com/v1",
                            api_key_env="TEST_API_KEY",
                        )
                    },
                    responses={
                        "gpt": ResponsesModelConfig(
                            base_url="https://example.com/v1",
                            api_key_env="TEST_API_KEY",
                        )
                    },
                )
            )
            judges = judgement_module._judges(config)

        self.assertEqual(
            ["compatible/qwen", "responses/gpt"],
            [judge.model for judge in judges],
        )

    def test_dimension_judge_retries_three_times_with_exponential_backoff(self) -> None:
        calls = 0
        sleeps: list[float] = []

        class FakeAgent:
            def __init__(self, **kwargs: object) -> None:
                pass

        class FakeModelSettings:
            def __init__(self, **kwargs: object) -> None:
                pass

        class FakeRunConfig:
            def __init__(self, **kwargs: object) -> None:
                pass

        class FakeRunner:
            @staticmethod
            def run_sync(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                raise RuntimeError("transient judge failure")

        with unittest.mock.patch.object(judgement_module, "_sleep_before_retry", sleeps.append):
            with self.assertRaises(RuntimeError):
                judgement_module._run_llm_dimension_judge(
                    agent_cls=FakeAgent,
                    agents_run_config_cls=FakeRunConfig,
                    model_settings_cls=FakeModelSettings,
                    runner=FakeRunner(),
                    dimension="execution_correctness",
                    judge=JudgementJudgeConfig(id="judge", model="compatible/judge"),
                    model_provider=object(),  # type: ignore[arg-type]
                    packet=_packet(),
                )

        self.assertEqual(3, calls)
        self.assertEqual([1.0, 2.0], sleeps)

    def test_transient_judge_fallback_is_deferred_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            summary = {
                "run_id": "run-1",
                "run_status": "completed",
                "season": {"id": "season_0"},
                "judgement": {
                    "status": "partial_fallback",
                    "judge_score": 50,
                    "arena_score": 50,
                    "judges": [
                        {
                            "judge_id": "gpt",
                            "model": "responses/gpt55",
                            "error": "execution_correctness: APIConnectionError: Connection error.",
                            "rubric": [],
                        }
                    ],
                },
            }
            (run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            self.assertTrue(mark_transient_judgement_retry_due(run_dir))

            updated = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            retry = json.loads((run_dir / "judgement_retry_state.json").read_text(encoding="utf-8"))
            self.assertEqual("due", retry["status"])
            self.assertEqual("deferred", updated["judgement"]["status"])
            self.assertIsNone(updated["judgement"]["judge_score"])
            self.assertIsNone(updated["judgement"]["arena_score"])

    def test_transient_judge_retry_exhaustion_marks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            judgement = {
                "status": "partial_fallback",
                "judge_score": 10,
                "arena_score": 10,
                "judges": [
                    {
                        "judge_id": "responses_gpt55",
                        "error": "APIConnectionError: Connection error.",
                    }
                ],
            }
            (run_dir / "judgement.json").write_text(
                json.dumps(judgement, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            summary = {
                "run_id": "run-a",
                "judgement": {"status": "partial_fallback", "judge_score": 10, "arena_score": 10},
            }
            (run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            (run_dir / "judgement_retry_state.json").write_text(
                json.dumps({"status": "due", "attempts": 3}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            self.assertTrue(mark_transient_judgement_retry_due(run_dir))

            retry = json.loads((run_dir / "judgement_retry_state.json").read_text(encoding="utf-8"))
            updated = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", retry["status"])
            self.assertEqual(4, retry["attempts"])
            self.assertEqual("failed", updated["judgement"]["status"])

    def test_due_judgement_refresh_skips_transient_replacement_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-a"
            run_dir.mkdir()
            summary = {
                "run_id": "run-a",
                "season": {"id": "season_0"},
                "started_at": "2026-05-22T00:00:00+00:00",
                "run_status": "failed",
                "terminal_reason": "model_runtime",
                "terminal_layer": "model_runtime",
                "replacement": {
                    "status": "due",
                    "layer": "model_runtime",
                    "message": "APIConnectionError: Connection error.",
                },
                "judgement": {
                    "status": "deferred",
                    "judge_score": None,
                    "arena_score": None,
                },
            }
            (run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            (run_dir / "judgement_retry_state.json").write_text(
                json.dumps({"status": "due", "attempts": 2}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            result = refresh_due_judgements(
                config=_config(Path(tmp)),
                input_dir=Path(tmp),
                season_id="season_0",
            )

            self.assertEqual(0, result.runs_judged)
            retry = json.loads((run_dir / "judgement_retry_state.json").read_text(encoding="utf-8"))
            updated = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("skipped", retry["status"])
            self.assertEqual("transient_replacement_pending", retry["reason"])
            self.assertEqual("skipped", updated["judgement_retry"]["status"])
            self.assertEqual("not_judged", updated["judgement"]["status"])

    def test_due_judgement_refresh_skips_terminal_state_transient_without_replacement_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-a"
            run_dir.mkdir()
            summary = {
                "run_id": "run-a",
                "season": {"id": "season_0"},
                "started_at": "2026-05-22T00:00:00+00:00",
                "run_status": "failed",
                "terminal_reason": "model_runtime",
                "terminal_layer": "model_runtime",
                "judgement": {"status": "deferred", "judge_score": None, "arena_score": None},
            }
            (run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            (run_dir / "terminal_state.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "reason": "model_runtime",
                        "layer": "model_runtime",
                        "message": "APIConnectionError: Connection error.",
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "judgement_retry_state.json").write_text(
                json.dumps({"status": "due", "attempts": 1}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            result = refresh_due_judgements(
                config=_config(Path(tmp)),
                input_dir=Path(tmp),
                season_id="season_0",
            )

            self.assertEqual(0, result.runs_judged)
            retry = json.loads((run_dir / "judgement_retry_state.json").read_text(encoding="utf-8"))
            updated = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("skipped", retry["status"])
            self.assertEqual("transient_replacement_pending", retry["reason"])
            self.assertEqual("not_judged", updated["judgement"]["status"])


def _config(output_root: Path, *, explicit_judges: bool = True) -> RunConfig:
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
    if explicit_judges:
        config.judgement.judges = [JudgementJudgeConfig(id="judge_a", model="local-stub")]
    return config


if __name__ == "__main__":
    unittest.main()
