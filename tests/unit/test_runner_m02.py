from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.runner import Runner
from contribarena.models import AgentFinalResult, OpportunitySummary, RepoSummary, SelectedTask
from contribarena.models.agent_result import WorkspaceSummary


class FakeM02Agent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_find_files("*.py", "repo")  # type: ignore[attr-defined]
        tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
        tools.aci_search("old", "repo")  # type: ignore[attr-defined]
        tools.aci_insert("repo/app.py", 1, "temporary")  # type: ignore[attr-defined]
        tools.aci_undo()  # type: ignore[attr-defined]
        tools.aci_replace("repo/app.py", "old", "new")  # type: ignore[attr-defined]
        tools.aci_suggest_verification("repo")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]

        return AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall test repository.",
            opportunities=[
                OpportunitySummary(
                    title="Replace old marker",
                    rationale="Low-risk deterministic test change.",
                    risk="low",
                    source="test",
                )
            ],
            selected_task=SelectedTask(
                title="Replace old marker",
                rationale="Exercise M0.2 ACI path.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=True,
                notes="M0.2 shadow patch submitted.",
            ),
        )


class RunnerM02Test(unittest.TestCase):
    def test_runner_captures_aci_trajectory_and_shadow_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                'args="$*"\n'
                'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
                'if [ "$1" = "rm" ]; then exit 0; fi\n'
                'if [ "$1" = "exec" ]; then\n'
                '  case "$args" in\n'
                '    *"find repo"*) printf "repo/app.py\\n"; exit 0 ;;\n'
                '    *"find . -maxdepth 3"*) printf "./pyproject.toml\\n./app.py\\n"; exit 0 ;;\n'
                '    *"cat -- repo/app.py"*) printf "old\\n"; exit 0 ;;\n'
                '    *"nl -ba repo/app.py"*) printf "     1\\told\\n"; exit 0 ;;\n'
                '    *"rg --line-number"*) printf "repo/app.py:1:old\\n"; exit 0 ;;\n'
                '    *"python3 -m compileall ."*) printf "compile ok\\n"; exit 0 ;;\n'
                '    *"git diff --binary -- ."*) '
                'printf "diff --git a/repo/app.py b/repo/app.py\\n"; exit 0 ;;\n'
                '    *"git apply -"*) exit 0 ;;\n'
                '    *) printf "/workspace\\n"; exit 0 ;;\n'
                "  esac\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                result = Runner(agent=FakeM02Agent()).run(
                    _config(tmp_path / "runs"),
                    output_dir=tmp_path / "runs",
                )
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual("completed", result.status)
            names = {path.name for path in result.run_dir.iterdir()}
            self.assertTrue(
                {
                    "trajectory.json",
                    "patch.diff",
                    "test_log.txt",
                    "quality_report.md",
                    "workspace_command.json",
                }.issubset(names)
            )
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            self.assertIn("aci_find_files", {step["tool"] for step in trajectory})
            self.assertIn("aci_insert", {step["tool"] for step in trajectory})
            self.assertIn("aci_undo", {step["tool"] for step in trajectory})
            self.assertIn("aci_replace", {step["tool"] for step in trajectory})
            self.assertIn("aci_suggest_verification", {step["tool"] for step in trajectory})
            self.assertIn("aci_verify", {step["tool"] for step in trajectory})
            self.assertIn("aci_submit_patch", {step["tool"] for step in trajectory})
            workspace_command = json.loads(
                (result.run_dir / "workspace_command.json").read_text()
            )
            self.assertIn("aci_results", workspace_command)
            self.assertIn(
                "diff --git a/repo/app.py b/repo/app.py",
                (result.run_dir / "patch.diff").read_text(),
            )


def _config(output_root: Path) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="shadow", model="local-stub"),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        workspace=WorkspaceConfig(command_timeout_seconds=10),
        artifacts=ArtifactConfig(output_root=output_root),
    )


if __name__ == "__main__":
    unittest.main()
