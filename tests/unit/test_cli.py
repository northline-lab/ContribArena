from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from contribarena.cli import app


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
                result = runner.invoke(
                    app,
                    ["run", "--config", str(config_path), "--output-dir", str(output_dir)],
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(0, result.exit_code, result.output)
            run_dirs = list(output_dir.iterdir())
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


if __name__ == "__main__":
    unittest.main()
