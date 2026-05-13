from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from contribarena.config.schema import (
    ArtifactConfig,
    BotIdentityConfig,
    DiscoveryConfig,
    GovernanceConfig,
    GovernanceRateLimits,
    IssueConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.agent.contributor import build_agent_instructions
from contribarena.engine.runner import Runner, _owned_live_push_command
from contribarena.engine.middleware.governance import load_governance_state
from contribarena.errors import AgentError
from contribarena.models import (
    AgentFinalResult,
    EligibilityResult,
    OpportunitySummary,
    RepoMetadata,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.lifecycle import CiCheck, CiStatus
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.providers import TracingModelProvider
from contribarena.tools.github_pr import (
    ForkEnsureResult,
    LabelOperationResult,
    PullRequestCreateResult,
)


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


class FakeFailingAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        raise AgentError("synthetic agent failure")


class FakeIssueAgent:
    def __init__(
        self,
        verify: bool = True,
        verify_before_edit: bool = False,
        submit_patch: bool = True,
        include_evidence: bool = True,
        status: str = "completed",
        no_command_verification_rationale: str = "",
        report_progress: bool = False,
        use_apply_patch: bool = False,
        repo_default_branch: str = "",
    ) -> None:
        self.verify = verify
        self.verify_before_edit = verify_before_edit
        self.submit_patch = submit_patch
        self.include_evidence = include_evidence
        self.status = status
        self.no_command_verification_rationale = no_command_verification_rationale
        self.report_progress = report_progress
        self.use_apply_patch = use_apply_patch
        self.repo_default_branch = repo_default_branch

    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
    ) -> AgentFinalResult:
        self.model_provider = model_provider
        self.prompt = prompt
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
        if self.report_progress:
            tools.operator_report_progress(  # type: ignore[attr-defined]
                "task_discovery",
                "working",
                "Inspecting repo/app.py for the configured marker.",
                '["repo/app.py", "trace.jsonl"]',
            )
        if self.verify and self.verify_before_edit:
            tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        if self.use_apply_patch:
            tools.aci_apply_patch(  # type: ignore[attr-defined]
                [
                    {
                        "type": "update_file",
                        "path": "repo/app.py",
                        "diff": (
                            "*** Begin Patch\n"
                            "*** Update File: repo/app.py\n"
                            "@@\n"
                            " def marker():\n"
                            "-    return 'old'\n"
                            "+    return 'new'\n"
                            "*** End Patch"
                        ),
                    }
                ],
                "fix configured marker",
                ["repo/app.py"],
            )
        else:
            tools.aci_replace("repo/app.py", "return 'old'", "return 'new'")  # type: ignore[attr-defined]
        if self.verify and not self.verify_before_edit:
            tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        if self.submit_patch:
            tools.aci_submit_patch(  # type: ignore[attr-defined]
                no_command_verification_rationale=self.no_command_verification_rationale
            )

        return AgentFinalResult(
            status=self.status,  # type: ignore[arg-type]
            repo=RepoSummary(
                owner="example",
                name="repo",
                url="https://github.com/example/repo",
                default_branch=self.repo_default_branch,
            ),
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
                "Inspected repo/app.py and found the old marker." if self.include_evidence else ""
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

    def test_owned_live_agent_instructions_keep_writes_harness_owned(self) -> None:
        instructions = build_agent_instructions(_owned_live_config(Path("runs"), live_enabled=True))

        self.assertIn("Owned-live mode", instructions)
        self.assertIn("harness will handle governed branch push and PR creation", instructions)

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
            self.assertEqual("run_completed", result.terminal_reason)
            self.assertEqual("run", result.terminal_layer)
            names = {path.name for path in result.run_dir.iterdir()}
            self.assertTrue(
                {
                    "trajectory.json",
                    "patch.diff",
                    "test_log.txt",
                    "terminal_state.json",
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
            workspace_command = json.loads((result.run_dir / "workspace_command.json").read_text())
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
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("completed", terminal["status"])
            self.assertEqual("run_completed", terminal["reason"])
            quality_gate = json.loads((result.run_dir / "quality_gate.json").read_text())
            self.assertEqual("pass", quality_gate["status"])
            ci_status = json.loads((result.run_dir / "ci_status.json").read_text())
            self.assertEqual("success", ci_status["status"])
            self.assertIn("Replace old marker", (result.run_dir / "pr_description.md").read_text())
            self.assertIn("Draft produced: True", (result.run_dir / "postmortem.md").read_text())
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            self.assertIn('"external_write": false', live_action_log)
            trace_states = {
                json.loads(line)["state"]
                for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
            }
            self.assertTrue(
                {
                    "workspace_starting",
                    "workspace_dirty",
                    "workspace_patch_captured",
                    "agent_initialized",
                    "agent_context_loaded",
                    "agent_acting",
                    "agent_final_result",
                    "agent_harness_reviewed",
                    "contribution_reviewed",
                    "pr_dry_run_started",
                    "pr_draft_created",
                    "ci_observed",
                    "postmortem_written",
                    "workspace_stopped",
                }.issubset(trace_states)
            )
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("Terminal reason: run_completed", report)
            self.assertIn("Contribution Quality Gate", report)

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
            quality_gate = json.loads((result.run_dir / "quality_gate.json").read_text())
            self.assertEqual("pass", quality_gate["status"])
            self.assertIn("Fix old marker", (result.run_dir / "pr_description.md").read_text())
            ci_status = json.loads((result.run_dir / "ci_status.json").read_text())
            self.assertEqual("success", ci_status["status"])
            self.assertIn("Draft produced: True", (result.run_dir / "postmortem.md").read_text())

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
            self.assertEqual("failed_to_recover", recovery_step["terminal_status"])
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("Recovery Evidence", report)
            self.assertIn("multi_tool_action", report)
            self.assertIn("terminal_after_retries", report)
            self.assertIn("Agent did not converge: true", report)

    def test_issue_solving_completed_requires_successful_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(verify=False),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            self.assertEqual("quality_gate_blocked", result.terminal_reason)
            self.assertEqual("contribution", result.terminal_layer)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("Submit-Time Review", report)
            self.assertIn("requires successful focused verification after the last edit", report)
            self.assertIn("Terminal reason: quality_gate_blocked", report)
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("completed", terminal["agent_status"])
            self.assertEqual("blocked", terminal["harness_status"])
            quality_gate = json.loads((result.run_dir / "quality_gate.json").read_text())
            self.assertEqual("block", quality_gate["status"])
            self.assertFalse((result.run_dir / "pr_description.md").exists())
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
            self.assertIn("aci_clean_generated", report)
            self.assertIn("submit_review_failed", report)

    def test_submit_review_rejects_untracked_source_edit_provenance(self) -> None:
        class FakeShellEditAgent(FakeIssueAgent):
            def run(
                self,
                config: RunConfig,
                tools: object,
                prompt: str,
                model_provider: object = None,
            ) -> AgentFinalResult:
                tools.workspace_run(  # type: ignore[attr-defined]
                    "git clone https://github.com/example/repo.git repo"
                )
                tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
                tools.workspace_run("cd repo && sed -i s/old/new/ app.py")  # type: ignore[attr-defined]
                tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
                tools.aci_submit_patch()  # type: ignore[attr-defined]
                return AgentFinalResult(
                    status="completed",
                    repo=RepoSummary(
                        owner="example", name="repo", url="https://github.com/example/repo"
                    ),
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
                        commands_run=[],
                        patch_applied=True,
                        notes="Shell edit patch submitted.",
                    ),
                    problem_statement_summary="Configured issue asks for old marker to become new.",
                    reproduction_notes="Inspected repo/app.py and found the old marker.",
                    verification_summary="python3 -m compileall . passed.",
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeShellEditAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            report = (result.run_dir / "quality_report.md").read_text()
            self.assertIn("without unified editor provenance", report)

    def test_runner_records_structured_apply_patch_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(use_apply_patch=True),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            self.assertIn("aci_apply_patch", {step["tool"] for step in trajectory})
            trace_events = [
                json.loads(line)
                for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertIn("aci.apply_patch.started", {event["event"] for event in trace_events})
            dirty_events = [event for event in trace_events if event["event"] == "workspace.dirty"]
            self.assertTrue(
                any(event["payload"].get("tool") == "aci_apply_patch" for event in dirty_events)
            )

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

    def test_quality_report_separates_latest_submit_review_from_prior_failures(self) -> None:
        class FakeRetrySubmitAgent(FakeIssueAgent):
            def run(
                self,
                config: RunConfig,
                tools: object,
                prompt: str,
                model_provider: object = None,
            ) -> AgentFinalResult:
                self.model_provider = model_provider
                self.prompt = prompt
                command = tools.workspace_run(  # type: ignore[attr-defined]
                    "git clone https://github.com/example/repo.git repo"
                )
                tools.aci_replace("repo/app.py", "return 'old'", "return 'new'")  # type: ignore[attr-defined]
                tools.aci_submit_patch()  # type: ignore[attr-defined]
                tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
                tools.aci_submit_patch()  # type: ignore[attr-defined]
                return AgentFinalResult(
                    status="completed",
                    repo=RepoSummary(
                        owner="example",
                        name="repo",
                        url="https://github.com/example/repo",
                    ),
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
                    problem_statement_summary="Configured issue asks for old marker to become new.",
                    reproduction_notes="Inspected repo/app.py and found the old marker.",
                    verification_summary="python3 -m compileall . passed.",
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeRetrySubmitAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            report = (result.run_dir / "quality_report.md").read_text()
            self.assertEqual("completed", result.status)
            self.assertIn("## Submit-Time Review", report)
            self.assertIn("submit-time review passed", report)
            self.assertIn("## Prior Submit-Time Review Failures", report)
            self.assertIn("requires successful focused verification after the last edit", report)

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

    def test_owned_live_run_blocks_at_governance_when_live_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _owned_live_config(tmp_path / "runs", live_enabled=False),
                tmp_path,
            )

            self.assertEqual("blocked", result.status)
            self.assertEqual("governance_blocked", result.terminal_reason)
            self.assertEqual("governance", result.terminal_layer)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertEqual("block", decision["status"])
            self.assertIn("governance live_enabled is false", decision["reasons"])
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            self.assertIn('"mode": "owned_live"', live_action_log)
            self.assertIn('"status": "blocked"', live_action_log)
            self.assertIn('"external_write": false', live_action_log)
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("completed", terminal["agent_status"])
            self.assertEqual("blocked", terminal["harness_status"])

    def test_owned_live_run_pushes_fork_branch_and_records_opened_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=pr_client,
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual(1, pr_client.calls)
            self.assertEqual(1, pr_client.ensure_fork_calls)
            self.assertEqual("Fix old marker", pr_client.last_title)
            self.assertEqual(
                ["contribarena-live", "issue-solving", "risk-low"],
                pr_client.last_labels,
            )
            self.assertEqual(
                "contribarena-bot:contribarena/fix-configured-problem",
                pr_client.last_head,
            )
            self.assertIn("Live PR Notice", pr_client.last_body)
            self.assertNotIn("Dry-Run Notice", pr_client.last_body)
            pr_description = (result.run_dir / "pr_description.md").read_text()
            self.assertIn("contribarena-live", pr_description)
            self.assertIn("Live PR Notice", pr_description)
            self.assertNotIn("contribarena-dry-run", pr_description)
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            live_action_entries = [
                json.loads(line) for line in live_action_log.splitlines() if line.strip()
            ]
            self.assertEqual(
                [
                    "github.ensure_fork",
                    "github.push_fork_branch",
                    "github.open_pr",
                    "github.ensure_labels",
                    "github.set_pr_labels",
                    "github.observe_checks",
                ],
                [entry["action"] for entry in live_action_entries],
            )
            self.assertEqual("ready", live_action_entries[0]["status"])
            self.assertEqual("pushed", live_action_entries[1]["status"])
            self.assertEqual("opened", live_action_entries[2]["status"])
            self.assertEqual("ready", live_action_entries[3]["status"])
            self.assertEqual("set", live_action_entries[4]["status"])
            self.assertEqual(
                ["contribarena-live", "issue-solving", "risk-low"],
                live_action_entries[4]["labels"],
            )
            self.assertEqual("success", live_action_entries[5]["status"])
            self.assertEqual("github", live_action_entries[5]["ci_source"])
            self.assertEqual(["fake check passed"], live_action_entries[5]["ci_details"])
            self.assertEqual("contribarena-bot", live_action_entries[0]["requested_fork_owner"])
            self.assertEqual("contribarena-bot/repo", live_action_entries[0]["fork_repo"])
            self.assertEqual(
                "contribarena-bot:contribarena/fix-configured-problem",
                live_action_entries[2]["head"],
            )
            self.assertIn('"status": "opened"', live_action_log)
            self.assertIn('"external_write": true', live_action_log)
            self.assertIn('"pr_url": "https://github.com/example/repo/pull/42"', live_action_log)
            self.assertNotIn("test-token", live_action_log)
            command_log = (result.run_dir / "test_log.txt").read_text()
            self.assertIn("${GITHUB_TOKEN}", command_log)
            self.assertIn(
                "x-access-token:${GITHUB_TOKEN}@github.com/contribarena-bot/repo.git",
                command_log,
            )
            self.assertNotIn(
                "x-access-token:${GITHUB_TOKEN}@github.com/example/repo.git",
                command_log,
            )
            self.assertNotIn("test-token", command_log)
            ci_status = json.loads((result.run_dir / "ci_status.json").read_text())
            self.assertEqual("github", ci_status["source"])
            self.assertEqual("success", ci_status["status"])

    def test_owned_live_upstream_branch_strategy_remains_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(
                tmp_path / "runs",
                live_enabled=True,
                strategy="upstream_branch",
            )
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=pr_client,
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual(0, pr_client.ensure_fork_calls)
            self.assertEqual("contribarena/fix-configured-problem", pr_client.last_head)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [
                    "github.push_upstream_branch",
                    "github.open_pr",
                    "github.ensure_labels",
                    "github.set_pr_labels",
                    "github.observe_checks",
                ],
                [entry["action"] for entry in live_action_entries],
            )
            command_log = (result.run_dir / "test_log.txt").read_text()
            self.assertIn(
                "x-access-token:${GITHUB_TOKEN}@github.com/example/repo.git",
                command_log,
            )

    def test_owned_live_label_permission_failure_keeps_opened_pr_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(
                        actor="contribarena-bot",
                        label_error="The user is not allowed to label this issue.",
                        label_status_code=404,
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual("run_completed", result.terminal_reason)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [
                    "github.ensure_fork",
                    "github.push_fork_branch",
                    "github.open_pr",
                    "github.ensure_labels",
                    "github.observe_checks",
                ],
                [entry["action"] for entry in live_action_entries],
            )
            self.assertEqual("opened", live_action_entries[2]["status"])
            self.assertEqual("permission_denied", live_action_entries[3]["status"])
            self.assertTrue(live_action_entries[3]["nonfatal"])
            self.assertEqual(
                "The user is not allowed to label this issue.",
                live_action_entries[3]["label_error"],
            )
            self.assertEqual(404, live_action_entries[3]["label_status_code"])
            self.assertEqual("success", live_action_entries[4]["status"])

    def test_owned_live_non_permission_label_failure_after_open_stays_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(
                        actor="contribarena-bot",
                        label_error="malformed label response",
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual("run_completed", result.terminal_reason)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual("failed", live_action_entries[3]["status"])
            self.assertTrue(live_action_entries[3]["nonfatal"])
            self.assertTrue(live_action_entries[3]["retryable"])

    def test_owned_live_push_fetches_existing_branch_for_explicit_lease(self) -> None:
        command = _owned_live_push_command(
            owner="northline-lab",
            repo="ContribArena",
            branch="contribarena/example",
            title="Example change",
            actor="northline-lab",
            token_env="GITHUB_TOKEN",
        )

        self.assertIn("remote add contribarena-submit", command)
        self.assertIn(
            "+refs/heads/contribarena/example:refs/remotes/contribarena-submit/contribarena/example",
            command,
        )
        self.assertIn(
            "--force-with-lease=refs/heads/contribarena/example:",
            command,
        )
        self.assertIn("git -C repo push contribarena-submit", command)

    def test_owned_live_run_blocks_when_authenticated_actor_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(actor="wrong-bot"),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("blocked", result.status)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertEqual("block", decision["status"])
            self.assertIn(
                "authenticated actor wrong-bot does not match expected contribarena-bot",
                decision["reasons"],
            )

    def test_owned_live_fork_failure_records_requested_fork_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(
                        actor="contribarena-bot",
                        fork_error="authentication missing or forbidden",
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("failed", result.status)
            self.assertEqual("pr_fork_prepare_failed", result.terminal_reason)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual("github.ensure_fork", live_action_entries[0]["action"])
            self.assertEqual("failed", live_action_entries[0]["status"])
            self.assertEqual("contribarena-bot", live_action_entries[0]["requested_fork_owner"])
            self.assertEqual("", live_action_entries[0]["fork_owner"])
            self.assertIn("authentication missing", live_action_entries[0]["fork_error"])

    def test_external_live_run_uses_fork_only_and_records_lifecycle_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with (
                    patch(
                        "contribarena.engine.runner.repo_check_eligibility",
                        return_value=EligibilityResult(
                            eligible=True,
                            reasons=["eligible"],
                            checks_performed=["fixture"],
                        ),
                    ),
                    patch(
                        "contribarena.engine.runner.repo_get_metadata",
                        return_value=RepoMetadata(
                            owner="example",
                            repo="repo",
                            full_name="example/repo",
                            url="https://github.com/example/repo",
                            default_branch="develop",
                        ),
                    ),
                ):
                    result = _run_with_fake_docker(
                        FakeIssueAgent(repo_default_branch="develop"),
                        config,
                        tmp_path,
                        pr_client=pr_client,
                    )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual(1, pr_client.ensure_fork_calls)
            self.assertEqual("contribarena-bot:contribarena/fix-configured-problem", pr_client.last_head)
            self.assertEqual("develop", pr_client.last_base)
            self.assertIn("External Live PR Notice", pr_client.last_body)
            self.assertIn("AI-assisted", pr_client.last_body)
            self.assertEqual(
                ["contribarena-external-live", "risk-low"],
                pr_client.last_labels,
            )
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual("external_live", live_action_entries[0]["mode"])
            self.assertEqual("github.ensure_fork", live_action_entries[0]["action"])
            self.assertEqual("github.push_fork_branch", live_action_entries[1]["action"])
            command_log = (result.run_dir / "test_log.txt").read_text()
            self.assertIn(
                "x-access-token:${GITHUB_TOKEN}@github.com/contribarena-bot/repo.git",
                command_log,
            )
            self.assertNotIn(
                "x-access-token:${GITHUB_TOKEN}@github.com/example/repo.git",
                command_log,
            )
            lifecycle_state = json.loads((result.run_dir / "pr_lifecycle_state.json").read_text())
            self.assertEqual("external_live", lifecycle_state["mode"])
            self.assertEqual(1, len(lifecycle_state["records"]))
            self.assertEqual("tracking", lifecycle_state["records"][0]["lifecycle_status"])
            self.assertTrue((result.run_dir / "eligibility_report.json").exists())
            self.assertTrue((result.run_dir / "maintainer_fit.md").exists())
            self.assertTrue((result.run_dir / "spam_risk.md").exists())
            self.assertTrue((result.run_dir / "pr_review_log.jsonl").exists())
            state = load_governance_state(config)
            self.assertEqual(1, len(state.lifecycle_records))
            self.assertEqual("example/repo", state.lifecycle_records[0].repository)
            self.assertEqual("develop", state.lifecycle_records[0].base)

    def test_external_live_blocks_when_independent_eligibility_rejects_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with patch(
                    "contribarena.engine.runner.repo_check_eligibility",
                    return_value=EligibilityResult(
                        eligible=False,
                        reasons=["repository policy appears to prohibit bot or AI contributions"],
                        checks_performed=["bot_policy"],
                    ),
                ):
                    result = _run_with_fake_docker(
                        FakeIssueAgent(repo_default_branch="main"),
                        config,
                        tmp_path,
                        pr_client=pr_client,
                    )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, pr_client.calls)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertEqual("block", decision["status"])
            self.assertIn("eligibility: repository policy", "; ".join(decision["reasons"]))

    def test_external_live_blocks_when_independent_eligibility_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with patch(
                    "contribarena.engine.runner.repo_check_eligibility",
                    side_effect=RuntimeError("network unavailable"),
                ):
                    result = _run_with_fake_docker(
                        FakeIssueAgent(repo_default_branch="main"),
                        config,
                        tmp_path,
                        pr_client=pr_client,
                    )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, pr_client.calls)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertIn("eligibility check failed: network unavailable", decision["reasons"])

    def test_live_push_failure_redacts_token_from_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            token = "fake-token-123"
            os.environ["GITHUB_TOKEN"] = token
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(actor="contribarena-bot"),
                    push_failure_stderr=(
                        "fatal: could not read from "
                        f"https://x-access-token:{token}@github.com/contribarena-bot/repo.git"
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("failed", result.status)
            self.assertEqual("pr_branch_push_failed", result.terminal_reason)
            test_log = (result.run_dir / "test_log.txt").read_text()
            workspace_command = (result.run_dir / "workspace_command.json").read_text()
            self.assertNotIn(token, test_log)
            self.assertNotIn(token, workspace_command)
            self.assertIn("https://x-access-token:***@github.com", test_log)

    def test_live_push_success_stdout_redacts_token_from_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            token = "fake-token-stdout"
            os.environ["GITHUB_TOKEN"] = token
            try:
                result = _run_with_fake_docker(
                    FakeIssueAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(actor="contribarena-bot"),
                    push_success_stdout=(
                        "pushing to "
                        f"https://x-access-token:{token}@github.com/contribarena-bot/repo.git"
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            workspace_command = (result.run_dir / "workspace_command.json").read_text()
            self.assertNotIn(token, workspace_command)
            self.assertIn("https://x-access-token:***@github.com", workspace_command)

    def test_agent_exception_writes_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.workspace.cleanup_policy = "retain_on_failure"

            with self.assertRaises(AgentError):
                _run_with_fake_docker(FakeFailingAgent(), config, tmp_path)

            run_dirs = list(config.artifacts.output_root.iterdir())
            self.assertEqual(1, len(run_dirs))
            terminal = json.loads((run_dirs[0] / "terminal_state.json").read_text())
            self.assertEqual("failed", terminal["status"])
            self.assertEqual("agent_error", terminal["reason"])
            self.assertEqual("agent", terminal["layer"])
            manifest = json.loads((run_dirs[0] / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("terminal_state.json", manifest_names)
            self.assertIn("quality_report.md", manifest_names)
            trace_states = {
                json.loads(line)["state"]
                for line in (run_dirs[0] / "trace.jsonl").read_text().splitlines()
            }
            self.assertIn("workspace_retained", trace_states)

    def test_runner_passes_tracing_model_provider_to_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            agent = FakeIssueAgent()

            _run_with_fake_docker(agent, _issue_config(tmp_path / "runs"), tmp_path)

            self.assertIsInstance(agent.model_provider, TracingModelProvider)

    def test_runner_writes_operator_progress_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            events = [
                json.loads(line)
                for line in (result.run_dir / "operator_events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            phases = {event["phase"] for event in events}
            self.assertIn("run", phases)
            self.assertIn("task_discovery", phases)
            self.assertIn("coding", phases)
            self.assertIn("verification", phases)
            self.assertIn("quality_gate", phases)
            self.assertTrue(all("summary" in event for event in events))
            self.assertTrue(all("source" in event for event in events))
            self.assertIn("harness", {event["source"] for event in events})
            manifest = json.loads((result.run_dir / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("operator_events.jsonl", manifest_names)

    def test_agent_reported_progress_tool_writes_agent_source_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(report_progress=True),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            events = [
                json.loads(line)
                for line in (result.run_dir / "operator_events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            agent_events = [event for event in events if event["source"] == "agent"]
            self.assertEqual(1, len(agent_events))
            self.assertEqual("task_discovery", agent_events[0]["phase"])
            self.assertEqual("working", agent_events[0]["status"])
            self.assertIn("repo/app.py", agent_events[0]["evidence"])
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            self.assertIn("operator_report_progress", {step["tool"] for step in trajectory})


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


def _owned_live_config(
    output_root: Path,
    live_enabled: bool,
    strategy: Literal["fork", "upstream_branch"] = "fork",
) -> RunConfig:
    config = _issue_config(output_root)
    config.run.mode = "owned_live"
    config.governance = GovernanceConfig(
        live_enabled=live_enabled,
        owned_repositories=[
            OwnedRepositoryPolicy(
                owner="example",
                repo="repo",
                default_branch="main",
                pr_submission=PrSubmissionConfig(
                    strategy=strategy,
                    fork_owner="contribarena-bot",
                ),
            )
        ],
        bot_identity=BotIdentityConfig(kind="pat", actor="contribarena-bot"),
    )
    return config


def _external_live_config(output_root: Path, live_enabled: bool) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="external_live", model="local-stub"),
        discovery=DiscoveryConfig(query="language:Python low risk"),
        workspace=WorkspaceConfig(command_timeout_seconds=10),
        artifacts=ArtifactConfig(output_root=output_root),
        governance=GovernanceConfig(
            live_enabled=live_enabled,
            bot_identity=BotIdentityConfig(kind="pat", actor="contribarena-bot"),
            rate_limits=GovernanceRateLimits(
                max_open_prs_per_repo=1,
                max_prs_per_repo_per_day=1,
                min_minutes_between_prs_per_repo=0,
                max_open_prs_per_org=2,
                max_prs_per_org_per_day=2,
                min_minutes_between_prs_per_org=0,
                max_open_prs_global=3,
                max_prs_global_per_day=3,
                min_minutes_between_prs_global=0,
            ),
        ),
    )


class FakePrClient:
    def __init__(
        self,
        actor: str = "",
        fork_error: str = "",
        label_error: str = "",
        label_status_code: int | None = None,
    ) -> None:
        self.calls = 0
        self.ensure_fork_calls = 0
        self.last_title = ""
        self.last_body = ""
        self.last_head = ""
        self.last_base = ""
        self.last_labels: list[str] = []
        self.actor = actor
        self.fork_error = fork_error
        self.label_error = label_error
        self.label_status_code = label_status_code

    def authenticated_actor(self) -> str:
        return self.actor

    def ensure_fork(self, *, owner: str, repo: str, fork_owner: str) -> ForkEnsureResult:
        self.ensure_fork_calls += 1
        if self.fork_error:
            return ForkEnsureResult(
                ok=False,
                error=self.fork_error,
                source="fake",
            )
        return ForkEnsureResult(
            ok=True,
            owner=fork_owner,
            repo=repo,
            full_name=f"{fork_owner}/{repo}",
            url=f"https://github.com/{fork_owner}/{repo}",
            created=False,
            source="fake",
        )

    def open_pr(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestCreateResult:
        self.calls += 1
        self.last_title = title
        self.last_body = body
        self.last_head = head
        self.last_base = base
        return PullRequestCreateResult(
            ok=True,
            number=42,
            url=f"https://github.com/{owner}/{repo}/pull/42",
            head_sha="abc123",
            source="fake",
        )

    def ensure_labels(self, *, owner: str, repo: str, labels: list[str]) -> LabelOperationResult:
        if self.label_error:
            return LabelOperationResult(
                ok=False,
                labels=labels,
                error=self.label_error,
                source="fake",
                status_code=self.label_status_code,
            )
        return LabelOperationResult(ok=True, labels=labels, source="fake")

    def set_pr_labels(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> LabelOperationResult:
        self.last_labels = labels
        return LabelOperationResult(ok=True, labels=labels, source="fake")

    def get_check_runs(self, *, owner: str, repo: str, ref: str) -> CiStatus:
        return CiStatus(
            status="success",
            source="github",
            checks=[
                CiCheck(
                    name=f"{owner}/{repo}:{ref}",
                    status="success",
                    details="fake check passed",
                )
            ],
        )


def _run_with_fake_docker(
    agent: object,
    config: RunConfig,
    tmp_path: Path,
    diff_path: str = "repo/app.py",
    pr_client: object | None = None,
    push_failure_stderr: str = "",
    push_success_stdout: str = "",
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    push_failure_case = (
        '    *"git -C repo push contribarena-submit"*) '
        f'printf %s {json.dumps(push_failure_stderr)} >&2; exit 1 ;;\n'
        if push_failure_stderr
        else ""
    )
    push_success_case = (
        '    *"git -C repo push contribarena-submit"*) '
        f'printf %s {json.dumps(push_success_stdout)}; exit 0 ;;\n'
        if push_success_stdout
        else ""
    )
    docker.write_text(
        "#!/usr/bin/env sh\n"
        'args="$*"\n'
        'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
        'if [ "$1" = "rm" ]; then exit 0; fi\n'
        'if [ "$1" = "exec" ]; then\n'
        '  case "$args" in\n'
        '    *"cat -- repo/app.py"*) printf "def marker():\\n    return \'old\'\\n"; exit 0 ;;\n'
        '    *"nl -ba repo/app.py"*) printf "     1\\tdef marker():\\n     2\\t    return \'old\'\\n"; exit 0 ;;\n'
        '    *"python3 -m compileall ."*) printf "compile ok\\n"; exit 0 ;;\n'
        f"{push_failure_case}"
        f"{push_success_case}"
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
        return Runner(agent=agent, pr_client=pr_client).run(
            config,
            output_dir=config.artifacts.output_root,
        )
    finally:
        os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
