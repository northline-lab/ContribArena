from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from contribarena.cli import app
from contribarena.engine.runner import RunResult


class CliTest(unittest.TestCase):
    def test_init_and_validate(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            init_result = runner.invoke(app, ["init", "--output", str(path)])
            self.assertEqual(0, init_result.exit_code, init_result.output)

            validate_result = runner.invoke(app, ["validate", "--config", str(path)])
            self.assertEqual(0, validate_result.exit_code, validate_result.output)

    def test_run_creates_artifacts_with_fake_docker(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
                'if [ "$1" = "exec" ]; then echo /workspace; exit 0; fi\n'
                'if [ "$1" = "rm" ]; then exit 0; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )

            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            config_path = tmp_path / "config.yaml"
            output_dir = tmp_path / "runs"
            try:
                self.assertEqual(
                    0, runner.invoke(app, ["init", "--output", str(config_path)]).exit_code
                )
                _write_test_memory_root(config_path, tmp_path)
                result = runner.invoke(
                    app,
                    ["run", "--config", str(config_path), "--output-dir", str(output_dir)],
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(0, result.exit_code, result.output)
            run_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
            self.assertEqual(1, len(run_dirs))
            names = {path.name for path in run_dirs[0].iterdir()}
            self.assertTrue(
                {
                    "config.json",
                    "artifact_manifest.json",
                    "trace.jsonl",
                    "repo_profile.md",
                    "opportunity_rank.md",
                    "selected_task.md",
                    "workspace_command.json",
                    "trajectory.json",
                    "patch.diff",
                    "test_log.txt",
                    "terminal_state.json",
                    "quality_gate.json",
                    "ci_status.json",
                    "postmortem.md",
                    "live_action_log.jsonl",
                    "quality_report.md",
                }.issubset(names)
            )
            manifest = json.loads(
                (run_dirs[0] / "artifact_manifest.json").read_text(encoding="utf-8")
            )
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("artifact_manifest.json", manifest_names)
            trace_states = {
                json.loads(line)["state"]
                for line in (run_dirs[0] / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            }
            self.assertTrue(
                {
                    "run_started",
                    "config_loaded",
                    "workspace_starting",
                    "workspace_ready",
                    "agent_initialized",
                    "agent_context_loaded",
                    "agent_final_result",
                    "agent_harness_reviewed",
                    "repo_discovered",
                    "repo_eligible",
                    "repo_profiled",
                    "opportunities_ranked",
                    "task_selected",
                    "workspace_checked",
                    "contribution_reviewed",
                    "ci_observed",
                    "postmortem_written",
                    "artifacts_written",
                    "run_terminal",
                    "run_completed",
                    "workspace_stopped",
                }.issubset(trace_states)
            )

    def test_run_model_override_is_written_to_artifacts(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.yaml"
            self.assertEqual(
                0, runner.invoke(app, ["init", "--output", str(config_path)]).exit_code
            )
            seen_models = []

            def fake_run(self, config, output_dir=None, verbose=False):
                seen_models.append(config.run.model)
                return RunResult(
                    run_id="mock-run",
                    run_dir=tmp_path / "runs" / "mock-run",
                    status="completed",
                    tool_calls=0,
                )

            with patch("contribarena.cli.Runner.run", fake_run):
                result = runner.invoke(
                    app,
                    [
                        "run",
                        "--config",
                        str(config_path),
                        "--model",
                        "compatible/example",
                    ],
                )

            self.assertEqual(0, result.exit_code, result.output)
            self.assertEqual(["compatible/example"], seen_models)

    def test_run_matrix_runs_configured_models(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.yaml"
            self.assertEqual(
                0, runner.invoke(app, ["init", "--output", str(config_path)]).exit_code
            )
            config_text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                config_text.replace(
                    "    compatible: {}\n",
                    (
                        "    compatible:\n"
                        "      alpha:\n"
                        "        base_url: http://localhost/v1\n"
                        "        api_key_env: EMPTY\n"
                        "        model: alpha-model\n"
                        "      beta:\n"
                        "        base_url: http://localhost/v1\n"
                        "        api_key_env: EMPTY\n"
                        "        model: beta-model\n"
                    ),
                ),
                encoding="utf-8",
            )
            seen_models = []

            def fake_run(self, config, output_dir=None, verbose=False):
                seen_models.append(config.run.model)
                return RunResult(
                    run_id=config.run.model.replace("/", "_"),
                    run_dir=tmp_path / "runs" / config.run.model.replace("/", "_"),
                    status="completed",
                    tool_calls=0,
                )

            with patch("contribarena.cli.Runner.run", fake_run):
                result = runner.invoke(
                    app,
                    [
                        "run-matrix",
                        "--config",
                        str(config_path),
                    ],
                )

            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("compatible/alpha", result.output)
            self.assertIn("compatible/beta", result.output)
            self.assertEqual(["compatible/alpha", "compatible/beta"], seen_models)

    def test_run_applies_m010_budget_overrides(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            captured = {}

            def fake_run(self, config, output_dir=None, verbose=False):
                captured["budget"] = config.run.budget
                return RunResult(
                    run_id="run-a",
                    run_dir=root / "runs" / "run-a",
                    status="completed",
                    tool_calls=0,
                )

            with patch("contribarena.cli.Runner.run", fake_run):
                result = runner.invoke(
                    app,
                    [
                        "run",
                        "--config",
                        str(config_path),
                        "--max-candidate-repos",
                        "3",
                        "--max-opportunities",
                        "4",
                        "--max-duplicate-checks",
                        "5",
                        "--max-repo-switches",
                        "1",
                        "--max-opportunity-switches",
                        "2",
                        "--max-review-rounds",
                        "0",
                    ],
                )

            self.assertEqual(0, result.exit_code, result.output)
            budget = captured["budget"]
            self.assertEqual(3, budget.scout.max_candidate_repos_considered)
            self.assertEqual(4, budget.scout.max_opportunities_considered)
            self.assertEqual(5, budget.scout.max_duplicate_checks)
            self.assertEqual(1, budget.work.max_repo_switches)
            self.assertEqual(2, budget.work.max_opportunity_switches)
            self.assertEqual(0, budget.review.max_review_rounds)

    def test_run_applies_season_participant_override(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            captured = {}

            def fake_run(self, config, output_dir=None, verbose=False):
                captured["run"] = config.run
                return RunResult(
                    run_id="run-a",
                    run_dir=root / "runs" / "run-a",
                    status="completed",
                    tool_calls=0,
                )

            with patch("contribarena.cli.Runner.run", fake_run):
                result = runner.invoke(
                    app,
                    [
                        "run",
                        "--config",
                        str(config_path),
                        "--season-id",
                        "season_0",
                        "--participant-id",
                        "season_0:local-stub",
                    ],
                )

            self.assertEqual(0, result.exit_code, result.output)
            run = captured["run"]
            self.assertEqual("season_0", run.season_id)
            self.assertEqual("season_0:local-stub", run.participant_id)
            self.assertEqual("manual", run.wake_source)

    def test_season_cli_transitions_configured_season(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text
                + "\nseason:\n"
                + "  id: season_0\n"
                + "  name: Season 0\n"
                + f"  state_root: {root / 'seasons'}\n"
                + "  participants:\n"
                + "    - model: local-stub\n",
                encoding="utf-8",
            )

            activate = runner.invoke(app, ["season", "activate", "--config", str(config_path)])
            status = runner.invoke(app, ["season", "status", "--config", str(config_path)])
            listing = runner.invoke(app, ["season", "list", "--config", str(config_path)])

            self.assertEqual(0, activate.exit_code, activate.output)
            self.assertEqual(0, status.exit_code, status.output)
            self.assertEqual(0, listing.exit_code, listing.output)
            self.assertIn("Season season_0: active", activate.output)
            self.assertIn("Status:      active", status.output)
            self.assertIn("season_0", listing.output)

    def test_season_start_tick_pause_resume_and_inspect_use_runtime_state(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text
                + f"\nartifacts:\n  output_root: {root / 'runs'}\n"
                + f"\nbackend:\n  read_model_path: {root / 'read.sqlite'}\n"
                + "\nseason:\n"
                + "  id: season_0\n"
                + "  name: Season 0\n"
                + f"  state_root: {root / 'seasons'}\n"
                + "  participants:\n"
                + "    - model: local-stub\n",
                encoding="utf-8",
            )

            with patch("contribarena.engine.season_runtime.LocalController.run_once") as run_once:
                run_once.return_value.status = "season_no_eligible_participant"
                start = runner.invoke(
                    app,
                    ["season", "start", "--config", str(config_path), "--max-heartbeats", "1"],
                )

            self.assertEqual(0, start.exit_code, start.output)
            self.assertIn("Season season_0: ok", start.output)
            state_path = root / "seasons" / "season_0" / "season_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("active", state["status"])
            self.assertEqual("ok", state["heartbeat"]["last_status"])

            pause = runner.invoke(app, ["season", "pause", "--config", str(config_path)])
            status = runner.invoke(app, ["season", "status", "--config", str(config_path)])
            inspect = runner.invoke(app, ["season", "inspect", "--config", str(config_path)])
            resume = runner.invoke(app, ["season", "resume", "--config", str(config_path)])

            self.assertEqual(0, pause.exit_code, pause.output)
            self.assertEqual(0, status.exit_code, status.output)
            self.assertEqual(0, inspect.exit_code, inspect.output)
            self.assertEqual(0, resume.exit_code, resume.output)
            self.assertIn("Season season_0: paused", pause.output)
            self.assertIn("Paused:      true", status.output)
            self.assertIn('"heartbeat"', inspect.output)
            self.assertIn("Season season_0: resumed", resume.output)

    def test_season_workspace_clean_removes_workspaces_and_memory_only(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text
                + "\nseason:\n"
                + "  id: season_0\n"
                + "  name: Season 0\n"
                + f"  state_root: {root / 'seasons'}\n"
                + "  participants:\n"
                + "    - model: local-stub\n",
                encoding="utf-8",
            )
            participant_dir = root / "seasons" / "season_0" / "participants" / "season_0:local-stub"
            workspace_dir = participant_dir / "workspaces" / "example-repo"
            workspace_dir.mkdir(parents=True)
            (workspace_dir / "container_id").write_text("container-1\n", encoding="utf-8")
            memory_dir = participant_dir / "memory"
            memory_dir.mkdir()
            (memory_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (participant_dir / "participant_state.json").write_text("{}\n", encoding="utf-8")
            (participant_dir / "goal_state.json").write_text("{}\n", encoding="utf-8")
            (participant_dir / "pr_history.json").write_text("{}\n", encoding="utf-8")

            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                result = runner.invoke(
                    app,
                    ["season", "workspace", "clean", "--config", str(config_path)],
                )

            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("Cleaned workspaces: 1", result.output)
            run.assert_called_once()
            self.assertFalse((participant_dir / "workspaces").exists())
            self.assertFalse(memory_dir.exists())
            self.assertTrue((participant_dir / "participant_state.json").exists())
            self.assertTrue((participant_dir / "goal_state.json").exists())
            self.assertTrue((participant_dir / "pr_history.json").exists())

    def test_season_complete_blocks_open_prs_unless_forced(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text
                + "\nseason:\n"
                + "  id: season_0\n"
                + "  name: Season 0\n"
                + f"  state_root: {root / 'seasons'}\n"
                + "  participants:\n"
                + "    - model: local-stub\n",
                encoding="utf-8",
            )
            participant_dir = root / "seasons" / "season_0" / "participants" / "season_0:local-stub"
            participant_dir.mkdir(parents=True)
            (participant_dir / "pr_history.json").write_text(
                json.dumps(
                    {
                        "lifecycle_records": [
                            {
                                "repository": "example/repo",
                                "number": 7,
                                "state": "open",
                                "lifecycle_status": "tracking",
                            }
                        ]
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace_dir = participant_dir / "workspaces" / "example-repo"
            workspace_dir.mkdir(parents=True)
            (workspace_dir / "container_id").write_text("container-1\n", encoding="utf-8")

            blocked = runner.invoke(app, ["season", "complete", "--config", str(config_path)])

            self.assertNotEqual(0, blocked.exit_code)
            self.assertIn("season_has_open_prs: example/repo#7", blocked.output)
            self.assertTrue(workspace_dir.exists())

            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                forced = runner.invoke(
                    app,
                    ["season", "complete", "--config", str(config_path), "--force-with-open-prs"],
                )

            self.assertEqual(0, forced.exit_code, forced.output)
            self.assertIn("Season season_0: completed", forced.output)
            self.assertIn("Cleaned workspaces: 1", forced.output)
            run.assert_called_once()
            snapshot = root / "seasons" / "season_0" / "leaderboard_snapshot.json"
            self.assertTrue(snapshot.exists())
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual("season_0", payload["season_id"])

    def test_completed_season_surface_uses_frozen_leaderboard_snapshot(self) -> None:
        from contribarena.engine.surface_indexer import index_surface_data

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            runs = root / "runs"
            run_dir = runs / "run-a"
            run_dir.mkdir(parents=True)
            _write_surface_run(run_dir, "run-a", score=80, pr_state="open")
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text
                + f"\nartifacts:\n  output_root: {runs}\n"
                + "\nseason:\n"
                + "  id: season_0\n"
                + "  name: Season 0\n"
                + f"  state_root: {root / 'seasons'}\n"
                + "  participants:\n"
                + "    - model: local-stub\n",
                encoding="utf-8",
            )

            complete = runner.invoke(app, ["season", "complete", "--config", str(config_path)])
            self.assertEqual(0, complete.exit_code, complete.output)
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            summary["judgement"]["arena_score"] = 10
            (run_dir / "run_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            index_surface_data(input_dir=runs, output_dir=root / "public")

            surface = json.loads((root / "public" / "surface.json").read_text(encoding="utf-8"))
            self.assertEqual(80.0, surface["leaderboard"][0]["mean_arena_score"])

    def test_controller_reports_disabled_starter_config(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )

            result = runner.invoke(app, ["controller", "--config", str(config_path)])

            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("Controller completed: disabled", result.output)
            self.assertIn("Tick 1: disabled", result.output)

    def test_status_reports_empty_backend_state(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )

            result = runner.invoke(app, ["status", "--config", str(config_path)])

            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn("Benchmark status:", result.output)
            self.assertIn("Runs:        0", result.output)

    def test_runs_and_show_read_indexed_backend_state(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            runs_dir = root / "runs"
            _write_run_summary(runs_dir / "run-a")
            self.assertEqual(
                0,
                runner.invoke(app, ["init", "--output", str(config_path)]).exit_code,
            )

            status = runner.invoke(
                app,
                [
                    "status",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                    "--refresh",
                ],
            )
            self.assertEqual(0, status.exit_code, status.output)
            self.assertIn("Read model refreshed: 1 runs", status.output)
            self.assertIn("Runs:        1", status.output)

            runs = runner.invoke(
                app,
                ["runs", "--config", str(config_path), "--input-dir", str(runs_dir)],
            )
            self.assertEqual(0, runs.exit_code, runs.output)
            self.assertIn("run-a", runs.output)
            self.assertIn("agent-a", runs.output)
            self.assertIn("example/repo", runs.output)

            queried_runs = runner.invoke(
                app,
                [
                    "runs",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                    "--query",
                    "example/repo",
                ],
            )
            self.assertEqual(0, queried_runs.exit_code, queried_runs.output)
            self.assertIn("run-a", queried_runs.output)

            missing_runs = runner.invoke(
                app,
                [
                    "runs",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                    "--query",
                    "missing-string",
                ],
            )
            self.assertEqual(0, missing_runs.exit_code, missing_runs.output)
            self.assertIn("No runs found.", missing_runs.output)

            show = runner.invoke(
                app,
                ["show", "run-a", "--config", str(config_path), "--input-dir", str(runs_dir)],
            )
            self.assertEqual(0, show.exit_code, show.output)
            self.assertIn("Run:         run-a", show.output)
            self.assertIn("Artifacts:", show.output)

            phases = runner.invoke(
                app,
                [
                    "inspect-phases",
                    "run-a",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                ],
            )
            self.assertEqual(0, phases.exit_code, phases.output)
            self.assertIn("Phase History:", phases.output)
            self.assertIn("work goal_created", phases.output)
            self.assertIn("Tool Violations:", phases.output)

            artifact = runner.invoke(
                app,
                [
                    "show",
                    "run-a",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                    "--artifact",
                    "patch.diff",
                ],
            )
            self.assertEqual(0, artifact.exit_code, artifact.output)
            self.assertIn("diff --git", artifact.output)

            judge = runner.invoke(
                app,
                [
                    "judge",
                    "--config",
                    str(config_path),
                    "--input-dir",
                    str(runs_dir),
                    "--all-unjudged",
                    "--force",
                ],
            )
            self.assertEqual(0, judge.exit_code, judge.output)
            self.assertIn("Runs judged: 1", judge.output)


def _write_run_summary(path: Path) -> None:
    path.mkdir(parents=True)
    payload = {
        "schema_version": "1",
        "run_id": "run-a",
        "run_mode": "shadow",
        "model": "local-stub",
        "agent": {"name": "Agent A", "handle": "agent-a"},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "season": {"id": "season_0", "name": "Season 0", "phase": "owned_repo_calibration"},
        "opportunity_source": "none",
        "opportunity_source_ref": "",
        "started_at": "2026-05-15T00:00:00Z",
        "completed_at": "2026-05-15T00:01:00Z",
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "complete",
        "terminal_layer": "agent",
        "contribution_class": "low_risk_code",
        "pipeline": [],
        "quality_gate": {"status": "pass", "warnings": []},
        "pull_request": {"url": "https://github.com/example/repo/pull/1", "number": 1, "state": "open"},
        "maintainer_outcome": {"status": "pending", "observed_at": "", "source": "none"},
        "judgement": {
            "status": "judged",
            "judge_score": 72,
            "real_world_adjustment": 2,
            "arena_score": 74,
            "rubric_summary": [],
            "source_artifacts": ["judgement.json"],
        },
        "artifacts": [
            {
                "name": "patch.diff",
                "kind": "diff",
                "visibility": "public",
                "url": "",
                "size_bytes": 120,
                "redacted": False,
            }
        ],
    }
    (path / "run_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (path / "patch.diff").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    (path / "phase_transition.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "event_id": "event-a",
                "source": "goal_events.jsonl",
                "run_id": "run-a",
                "goal_id": "goal-a",
                "event_type": "goal_created",
                "scope": "contribution",
                "status": "active",
                "phase": "work",
                "sub_phase": None,
                "created_at": "2026-05-15T00:00:10Z",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_surface_run(path: Path, run_id: str, *, score: float, pr_state: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "run_mode": "shadow",
        "model": "local-stub",
        "agent": {"name": "local-stub", "handle": "season_0:local-stub"},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "season": {"id": "season_0", "name": "Season 0", "phase": "owned_repo_calibration"},
        "started_at": "2026-05-15T00:00:00Z",
        "completed_at": "2026-05-15T00:01:00Z",
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "complete",
        "terminal_layer": "agent",
        "contribution_class": "low_risk_code",
        "pipeline": [],
        "quality_gate": {"status": "pass", "warnings": []},
        "pull_request": {
            "url": "https://github.com/example/repo/pull/1",
            "number": 1,
            "state": pr_state,
        },
        "maintainer_outcome": {"status": "pending", "observed_at": "", "source": "none"},
        "judgement": {
            "status": "judged",
            "judge_score": score,
            "real_world_adjustment": 0,
            "arena_score": score,
            "rubric_summary": [],
            "source_artifacts": ["judgement.json"],
        },
        "artifacts": [
            {
                "name": "patch.diff",
                "kind": "diff",
                "visibility": "public",
                "url": "",
                "size_bytes": 120,
                "redacted": False,
            }
        ],
    }
    (path / "run_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (path / "patch.diff").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")


def _write_fake_docker(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
        'if [ "$1" = "exec" ]; then echo /workspace; exit 0; fi\n'
        'if [ "$1" = "rm" ]; then exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_test_memory_root(config_path: Path, root: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace("  root: .contribarena/memory\n", f"  root: {root / 'memory'}\n"),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
