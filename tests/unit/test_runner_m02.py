from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    IssueConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.agent.contributor import build_agent_instructions
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


class FakeIssueAgent:
    def __init__(
        self,
        verify: bool = True,
        verify_before_edit: bool = False,
        submit_patch: bool = True,
        include_evidence: bool = True,
        status: str = "completed",
        no_command_verification_rationale: str = "",
    ) -> None:
        self.verify = verify
        self.verify_before_edit = verify_before_edit
        self.submit_patch = submit_patch
        self.include_evidence = include_evidence
        self.status = status
        self.no_command_verification_rationale = no_command_verification_rationale

    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        self.prompt = prompt
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
        if self.verify and self.verify_before_edit:
            tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_replace("repo/app.py", "return 'old'", "return 'new'")  # type: ignore[attr-defined]
        if self.verify and not self.verify_before_edit:
            tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        if self.submit_patch:
            tools.aci_submit_patch(  # type: ignore[attr-defined]
                no_command_verification_rationale=self.no_command_verification_rationale
            )

        return AgentFinalResult(
            status=self.status,  # type: ignore[arg-type]
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall issue fixture.",
            opportunities=[
                OpportunitySummary(
                    title="Fix configured problem",
                    rationale="Directly addresses the problem statement.",
                    risk="low",
                    source="configured",
                )
            ],
            selected_task=SelectedTask(
                title="Fix configured problem",
                rationale="Issue-solving mode should not self-select another task.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=True,
                notes="Issue-solving patch submitted.",
            ),
            problem_statement_summary=(
                "Configured issue asks for old marker to become new."
                if self.include_evidence
                else ""
            ),
            reproduction_notes=(
                "Inspected repo/app.py and found the old marker."
                if self.include_evidence
                else ""
            ),
            verification_summary=(
                self.no_command_verification_rationale
                if self.no_command_verification_rationale and self.include_evidence
                else "python3 -m compileall . passed."
                if self.verify and self.include_evidence
                else ""
            ),
        )


class FakeActionRecoveryAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        tools.workspace_run("pwd")  # type: ignore[attr-defined]
        for _ in range(3):
            tools.aci_recover_invalid_action(  # type: ignore[attr-defined]
                "multi_tool_action",
                "Rejected multiple tool calls in one model step.",
                "aci_view, aci_search",
            )
        return AgentFinalResult(
            status="blocked",
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall test repository.",
            opportunities=[
                OpportunitySummary(
                    title="Recover invalid model action",
                    rationale="Exercise invalid action recovery.",
                    risk="low",
                    source="test",
                )
            ],
            selected_task=SelectedTask(
                title="Recover invalid model action",
                rationale="The model issued an invalid action.",
                expected_change="No workspace change.",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[],
                patch_applied=False,
                notes="Invalid model action was rejected before workspace execution.",
            ),
            blockers=["invalid model action"],
        )


class RunnerM02Test(unittest.TestCase):
    def test_issue_solving_agent_instructions_disable_self_selected_tasks(self) -> None:
        instructions = build_agent_instructions(_issue_config(Path("runs")))

        self.assertIn("explicit issue/problem statement", instructions)
        self.assertIn("Do not self-select", instructions)
        self.assertIn("problem_statement_summary", instructions)

    def test_standard_agent_instructions_keep_task_selection(self) -> None:
        instructions = build_agent_instructions(_config(Path("runs")))

        self.assertIn("discover and select exactly one low-risk task", instructions)

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
            submit_results = [
                item
                for item in workspace_command["aci_results"]
                if item["tool"] == "aci_submit_patch"
            ]
            self.assertEqual("submit-time review passed", submit_results[-1]["review_notes"])
            self.assertIn(
                "diff --git a/repo/app.py b/repo/app.py",
                (result.run_dir / "patch.diff").read_text(),
            )

    def test_runner_writes_issue_solving_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            agent = FakeIssueAgent()
            result = _run_with_fake_docker(agent, _issue_config(tmp_path / "runs"), tmp_path)

            self.assertEqual("completed", result.status)
            self.assertIn("M0.2.2 issue-solving", agent.prompt)
            self.assertIn("Recovery templates", agent.prompt)
            self.assertIn("submit_review_failed", agent.prompt)
            names = {path.name for path in result.run_dir.iterdir()}
            self.assertTrue(
                {
                    "problem_statement.md",
                    "reproduction_notes.md",
                    "verification_summary.md",
                    "patch.diff",
                    "trajectory.json",
                    "quality_report.md",
                }.issubset(names)
            )
            self.assertIn(
                "Return old marker should become new marker.",
                (result.run_dir / "problem_statement.md").read_text(),
            )
            self.assertIn(
                "found the old marker",
                (result.run_dir / "reproduction_notes.md").read_text(),
            )
            self.assertIn(
                "compileall",
                (result.run_dir / "verification_summary.md").read_text(),
            )

    def test_runner_records_rejected_action_recovery_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeActionRecoveryAgent(),
                _config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            recovery_step = [
                step for step in trajectory if step["tool"] == "aci_recover_invalid_action"
            ][-1]
            self.assertFalse(recovery_step["accepted"])
            self.assertEqual("multi_tool_action", recovery_step["recovery_kind"])
            self.assertEqual(3, recovery_step["retry_count"])
            self.assertTrue(recovery_step["terminal_after_retries"])
            self.assertEqual("format_exhausted", recovery_step["terminal_status"])
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("Recovery Evidence", report)
            self.assertIn("multi_tool_action", report)
            self.assertIn("terminal_after_retries", report)

    def test_issue_solving_completed_requires_successful_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(verify=False),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("Submit-Time Review", report)
            self.assertIn("requires successful focused verification after the last edit", report)
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            submit_step = [step for step in trajectory if step["tool"] == "aci_submit_patch"][-1]
            self.assertFalse(submit_step["accepted"])
            self.assertEqual("submit_review_failed", submit_step["recovery_kind"])
            self.assertEqual("blocked", submit_step["terminal_status"])

    def test_issue_solving_completed_requires_verification_after_last_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(verify=True, verify_before_edit=True),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            self.assertIn(
                "after the last edit",
                (result.run_dir / "quality_report.md").read_text(),
            )

    def test_issue_solving_completed_requires_submitted_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(submit_patch=False),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            self.assertIn(
                "without a submitted patch",
                (result.run_dir / "quality_report.md").read_text(),
            )

    def test_submit_review_rejects_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
                diff_path="repo/__pycache__/app.cpython-313.pyc",
            )

            self.assertEqual("blocked", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("suspicious generated or temporary files", report)
            self.assertIn("submit_review_failed", report)

    def test_submit_review_accepts_no_command_verification_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(
                    verify=False,
                    no_command_verification_rationale=(
                        "No command verifier exists for this text-only generated fixture; "
                        "reviewed the exact diff."
                    ),
                ),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("completed", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("no-command verification rationale accepted", report)

    def test_submit_review_rejects_weak_no_command_verification_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(
                    verify=False,
                    no_command_verification_rationale="looks fine",
                ),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn(
                "requires successful focused verification after the last edit or a specific",
                report,
            )

    def test_issue_solving_completed_requires_problem_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(include_evidence=False),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("without a problem statement summary", report)
            self.assertIn("without reproduction notes", report)
            self.assertIn("without a verification summary", report)

    def test_issue_solving_blocked_requires_explicit_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(status="blocked", verify=False, submit_patch=False),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            self.assertIn(
                "without an explicit blocker",
                (result.run_dir / "quality_report.md").read_text(),
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


def _issue_config(output_root: Path) -> RunConfig:
    config = _config(output_root)
    config.issue = IssueConfig(
        title="Fix old marker",
        clone_url="https://github.com/example/repo.git",
        problem_statement="Return old marker should become new marker.",
        reproduction_hint="Inspect repo/app.py.",
        verification_hint="Run python3 -m compileall .",
    )
    return config


def _run_with_fake_docker(
    agent: object,
    config: RunConfig,
    tmp_path: Path,
    diff_path: str = "repo/app.py",
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env sh\n"
        'args="$*"\n'
        'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
        'if [ "$1" = "rm" ]; then exit 0; fi\n'
        'if [ "$1" = "exec" ]; then\n'
        '  case "$args" in\n'
        "    *\"cat -- repo/app.py\"*) printf \"def marker():\\n    return 'old'\\n\"; exit 0 ;;\n"
        "    *\"nl -ba repo/app.py\"*) printf \"     1\\tdef marker():\\n     2\\t    return 'old'\\n\"; exit 0 ;;\n"
        '    *"python3 -m compileall ."*) printf "compile ok\\n"; exit 0 ;;\n'
        '    *"git diff --binary -- ."*) '
        f'printf "diff --git a/{diff_path} b/{diff_path}\\n"; exit 0 ;;\n'
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
        return Runner(agent=agent).run(config, output_dir=config.artifacts.output_root)
    finally:
        os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
