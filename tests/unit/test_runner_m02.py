from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    BotIdentityConfig,
    DiscoveryConfig,
    GovernanceConfig,
    IssueConfig,
    OwnedRepositoryPolicy,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.agent.contributor import build_agent_instructions
from contribarena.engine.runner import Runner
from contribarena.errors import AgentError
from contribarena.models import AgentFinalResult, OpportunitySummary, RepoSummary, SelectedTask
from contribarena.models.lifecycle import CiCheck, CiStatus
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.tools.github_pr import PullRequestCreateResult


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

    def test_owned_live_agent_instructions_keep_writes_harness_owned(self) -> None:
        instructions = build_agent_instructions(
            _owned_live_config(Path("runs"), live_enabled=True)
        )

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

    def test_owned_live_run_pushes_branch_and_records_opened_pr(self) -> None:
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
            self.assertEqual("Fix old marker", pr_client.last_title)
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            live_action_entries = [
                json.loads(line) for line in live_action_log.splitlines() if line.strip()
            ]
            self.assertEqual(
                ["github.push_branch", "github.open_pr"],
                [entry["action"] for entry in live_action_entries],
            )
            self.assertEqual("pushed", live_action_entries[0]["status"])
            self.assertEqual("opened", live_action_entries[1]["status"])
            self.assertIn('"status": "opened"', live_action_log)
            self.assertIn('"external_write": true', live_action_log)
            self.assertIn('"pr_url": "https://github.com/example/repo/pull/42"', live_action_log)
            self.assertNotIn("test-token", live_action_log)
            command_log = (result.run_dir / "test_log.txt").read_text()
            self.assertIn("${GITHUB_TOKEN}", command_log)
            self.assertNotIn("test-token", command_log)
            ci_status = json.loads((result.run_dir / "ci_status.json").read_text())
            self.assertEqual("github", ci_status["source"])
            self.assertEqual("success", ci_status["status"])

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


def _owned_live_config(output_root: Path, live_enabled: bool) -> RunConfig:
    config = _issue_config(output_root)
    config.run.mode = "owned_live"
    config.governance = GovernanceConfig(
        live_enabled=live_enabled,
        owned_repositories=[
            OwnedRepositoryPolicy(owner="example", repo="repo", default_branch="main")
        ],
        bot_identity=BotIdentityConfig(kind="pat", actor="contribarena-bot"),
    )
    return config


class FakePrClient:
    def __init__(self, actor: str = "") -> None:
        self.calls = 0
        self.last_title = ""
        self.actor = actor

    def authenticated_actor(self) -> str:
        return self.actor

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
        return PullRequestCreateResult(
            ok=True,
            number=42,
            url=f"https://github.com/{owner}/{repo}/pull/42",
            head_sha="abc123",
            source="fake",
        )

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
        return Runner(agent=agent, pr_client=pr_client).run(
            config,
            output_dir=config.artifacts.output_root,
        )
    finally:
        os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
