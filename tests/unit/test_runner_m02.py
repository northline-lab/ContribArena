from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from contribarena.config.schema import (
    ArtifactConfig,
    BotIdentityConfig,
    ContributionClassesConfig,
    DiscoveryConfig,
    GuidanceConfig,
    GovernanceConfig,
    GovernanceRateLimits,
    IssueConfig,
    JudgementJudgeConfig,
    MemoryConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonDiscoveryProfileConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.agent import AgentInvocationResult
from contribarena.agent.contributor import build_agent_instructions
from contribarena.agent.prompts import build_goal_prompt
from contribarena.engine.guidance import install_guidance_sidecar
from contribarena.engine.goals import GoalService, goal_state_path
from contribarena.engine.runner import (
    Runner,
    _build_assistant_update,
    _owned_live_push_command,
    _provider_infrastructure_message,
    _replacement_due_terminal,
    _transient_runtime_message,
)
from contribarena.engine.agent_loop import (
    AgentLoopState,
    TerminalState,
    agent_loop_progress,
    capture_cursor,
    render_continuation_context,
    review_invocation,
)
from contribarena.engine.seasons import SeasonStore, derive_participant_id, normalize_model_identity
from contribarena.engine.seasons import load_participant_state
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.governance import load_governance_state, save_governance_state
from contribarena.errors import AgentError
from contribarena.models import (
    AciResult,
    AgentFinalResult,
    CommandResult,
    EligibilityResult,
    OpportunitySummary,
    GovernanceState,
    PrLifecycleRecord,
    RepoMetadata,
    RepoSummary,
    SelectedTask,
)
from contribarena.models.lifecycle import CiCheck, CiStatus
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.providers.turns import ProviderTurn, VisibleSegment
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
        **kwargs: object,
    ) -> AgentFinalResult:
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Submit a verified low-risk patch.",
            "active",
            scope="contribution",
        )
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
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]

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
        **kwargs: object,
    ) -> AgentFinalResult:
        raise AgentError("synthetic agent failure")


class FakeInterruptedAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Investigate a low-risk contribution.",
            "active",
            scope="contribution",
        )
        raise KeyboardInterrupt()


class FakeProviderErrorAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            content="Provider invocation failed.",
            stopped_reason="provider_error",
            error_message="HTTP 400",
        )


class FakeTransientProviderErrorAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            content="Provider invocation failed: APIConnectionError",
            stopped_reason="provider_error",
            error_message="APIConnectionError: Connection error.",
        )


class FakeProviderServerErrorAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            content="Provider invocation failed: empty request.",
            stopped_reason="provider_error",
            error_message="Error code: 500 - {'code': 500, 'message': '请求参数不能为空', 'success': False}",
        )


class FakeProviderBillingErrorAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            content="Provider invocation failed: insufficient balance.",
            stopped_reason="provider_error",
            error_message="Error code: 403 - {'code': 'INSUFFICIENT_BALANCE'}",
        )


class FakeBlankProviderErrorAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        return AgentInvocationResult(
            content="Provider invocation failed: ReadTimeout",
            stopped_reason="provider_error",
            error_message="ReadTimeout",
        )


class FakeMemoryAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        self.prompt = prompt
        memory_context = tools.aci_runtime_get_context("run")  # type: ignore[attr-defined]
        self.memory_context = (
            json.loads(memory_context.output) if memory_context.success else {}
        )
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Submit a verified small code patch for example/repo.",
            "active",
            "",
        )
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/CONTRIBUTING.md")  # type: ignore[attr-defined]
        tools.aci_memory_note(  # type: ignore[attr-defined]
            "run",
            "CONTRIBUTING.md was inspected before editing.",
            '["contributing"]',
            "medium",
        )
        tools.aci_replace("repo/app.py", "old", "new")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "",
            "complete",
            "Submitted patch and compileall verification succeeded.",
            evidence_refs_json='["tool_call:aci_submit_patch_finalize"]',
        )
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
                rationale="Exercise M0.6 memory path.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=True,
                notes="M0.6 memory path submitted.",
            ),
        )


class FakeGoalHalfRunAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        self.prompt = prompt
        runtime_context = tools.aci_runtime_get_context("run")  # type: ignore[attr-defined]
        self.runtime_context = (
            json.loads(runtime_context.output) if runtime_context.success else {}
        )
        tools.aci_memory_note(  # type: ignore[attr-defined]
            "run",
            "Saw the active goal, but this run stopped before editing.",
            '["goal-continuation"]',
            "medium",
        )
        return AgentFinalResult(
            status="blocked",
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall test repository.",
            opportunities=[
                OpportunitySummary(
                    title="Replace old marker",
                    rationale="Goal continuation candidate.",
                    risk="low",
                    source="test",
                )
            ],
            selected_task=SelectedTask(
                title="Replace old marker",
                rationale="The active goal still needs a code change.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                notes="Stopped before patching to simulate an incomplete run.",
            ),
            blockers=["continuation required"],
        )


class FakeGoalCompletingAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        self.prompt = prompt
        runtime_context = tools.aci_runtime_get_context("run")  # type: ignore[attr-defined]
        self.runtime_context = (
            json.loads(runtime_context.output) if runtime_context.success else {}
        )
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/CONTRIBUTING.md")  # type: ignore[attr-defined]
        tools.aci_replace("repo/app.py", "old", "new")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        goal = tools.aci_goal_update(  # type: ignore[attr-defined]
            "",
            "complete",
            "Submitted patch and compileall verification succeeded.",
            evidence_refs_json='["tool_call:aci_submit_patch_finalize"]',
        )
        self.goal_update = json.loads(goal.output) if goal.success else {}
        return AgentFinalResult(
            status="completed",
            repo=RepoSummary(owner="example", name="repo", url="https://github.com/example/repo"),
            repo_profile="# Repo Profile\n\nSmall test repository.",
            opportunities=[
                OpportunitySummary(
                    title="Replace old marker",
                    rationale="Goal continuation candidate.",
                    risk="low",
                    source="test",
                )
            ],
            selected_task=SelectedTask(
                title="Replace old marker",
                rationale="Complete the active short-term goal.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[command],
                patch_applied=True,
                notes="Goal continuation completed.",
            ),
            problem_statement_summary="Return old marker now becomes new marker.",
            reproduction_notes="Inspected repo/app.py and confirmed the old marker.",
            verification_summary="python3 -m compileall . passed.",
        )


class FakeSessionContinuationAgent:
    def __init__(self) -> None:
        self.context_ids: list[int] = []

    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        invocation_context = kwargs.get("invocation_context")
        self.context_ids.append(id(invocation_context))
        if len(self.context_ids) == 1:
            tools.aci_goal_update(  # type: ignore[attr-defined]
                "Submit a verified patch.",
                "active",
                scope="contribution",
            )
            tools.workspace_run(  # type: ignore[attr-defined]
                "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
            )
            tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
            tools.aci_replace("repo/app.py", "old", "new")  # type: ignore[attr-defined]
            tools.aci_goal_update(  # type: ignore[attr-defined]
                "Submit a verified patch.",
                "active",
                "Edited repo/app.py; verification still pending.",
                scope="contribution",
            )
            return AgentInvocationResult(content="Edited; continue for verification.")

        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "",
            "complete",
            "Verified and submitted patch.",
            evidence_refs_json='["tool_call:aci_submit_patch_finalize"]',
        )
        return AgentInvocationResult(content="Verified and submitted.")


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
        edit_path: str = "repo/app.py",
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
        self.edit_path = edit_path

    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        self.model_provider = model_provider
        self.prompt = prompt
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view(self.edit_path)  # type: ignore[attr-defined]
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
                        "path": self.edit_path,
                        "diff": (
                            "*** Begin Patch\n"
                            f"*** Update File: {self.edit_path}\n"
                            "@@\n"
                            " def marker():\n"
                            "-    return 'old'\n"
                            "+    return 'new'\n"
                            "*** End Patch"
                        ),
                    }
                ],
                "fix configured marker",
                [self.edit_path],
            )
        else:
            tools.aci_replace(self.edit_path, "return 'old'", "return 'new'")  # type: ignore[attr-defined]
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


class FakeLiveSubmittingAgent(FakeIssueAgent):
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        result = super().run(config, tools, prompt, model_provider, **kwargs)
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        fork = tools.github_prepare_fork("example", "repo")  # type: ignore[attr-defined]
        push_owner = "contribarena-bot"
        if fork.success:
            payload = json.loads(fork.output or "{}")
            push_owner = str(payload.get("push_owner") or push_owner)
        else:
            return result
        branch = "contribarena/fix-configured-problem"
        prepared = tools.github_prepare_branch("example", "repo", "main", branch)  # type: ignore[attr-defined]
        if not prepared.success:
            return result
        tools.github_commit("Fix old marker", "Replace the old marker with the new marker.")  # type: ignore[attr-defined]
        push = tools.github_push_branch(push_owner, "repo", branch)  # type: ignore[attr-defined]
        if not push.success:
            return result
        tools.github_open_pr(  # type: ignore[attr-defined]
            "example",
            "repo",
            f"{push_owner}:{branch}",
            "main",
            "Fix old marker",
            "Replace the old marker with the new marker.",
        )
        return result


class FakeLiveSubmissionContextAgent(FakeIssueAgent):
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        context = tools.aci_runtime_get_context("run")  # type: ignore[attr-defined]
        self.runtime_context = json.loads(context.output) if context.success else {}
        return super().run(config, tools, prompt, model_provider, **kwargs)


class FakeLiveOpeningOnlyAgent(FakeIssueAgent):
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        result = super().run(config, tools, prompt, model_provider, **kwargs)
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        tools.github_open_pr(  # type: ignore[attr-defined]
            "example",
            "repo",
            "contribarena-bot:contribarena/fix-configured-problem",
            "main",
            "Fix old marker",
            "Replace the old marker with the new marker.",
        )
        return result


class FakePhasedExternalAgent(FakeIssueAgent):
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/README.md")  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Find a low-risk opportunity in example/repo.",
            "active",
            "Selected example/repo after configured external discovery audit.",
            scope="opportunity",
            evidence_refs_json='["tool_call:aci_view"]',
            next_objective="Find a non-duplicate low-risk issue.",
        )
        tools.repo_get_open_prs(  # type: ignore[attr-defined]
            RepoCandidate(owner="example", repo="repo", url="https://github.com/example/repo"),
            10,
        )
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Implement the selected low-risk marker fix.",
            "active",
            "Duplicate check found no matching open PR.",
            scope="contribution",
            evidence_refs_json='["tool_call:repo.open_prs"]',
            next_objective="Implement and verify the marker fix.",
        )
        return super().run(config, tools, prompt, model_provider, **kwargs)


class FakeM010Agent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentInvocationResult:
        candidate = RepoCandidate(owner="example", repo="repo", url="https://github.com/example/repo")
        tools.repo_search()  # type: ignore[attr-defined]
        tools.repo_check_eligibility(candidate)  # type: ignore[attr-defined]
        tools.repo_get_metadata(candidate)  # type: ignore[attr-defined]
        tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/README.md")  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Find a low-risk opportunity in example/repo.",
            "active",
            "Project audit selected example/repo.",
            scope="opportunity",
            evidence_refs_json='["tool_call:repo.metadata"]',
            next_objective="Find a non-duplicate low-risk opportunity.",
        )
        tools.repo_get_issues(candidate)  # type: ignore[attr-defined]
        tools.repo_get_open_prs(candidate, 10)  # type: ignore[attr-defined]
        tools.repo_search_prs_by_title(candidate, "marker", 10)  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "Replace the old marker with the new marker.",
            "active",
            "Issue and duplicate checks found no conflicting PR.",
            scope="contribution",
            evidence_refs_json='["tool_call:repo.open_prs"]',
            next_objective="Edit, verify, review, and finalize the patch.",
        )
        tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
        tools.aci_replace("repo/app.py", "return 'old'", "return 'new'")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m compileall .", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch()  # type: ignore[attr-defined]
        tools.aci_dispute_review(  # type: ignore[attr-defined]
            "concern-1",
            "The draft is intentionally minimal and verified by compileall evidence.",
            '["tool_call:aci_verify"]',
        )
        tools.aci_submit_patch_finalize()  # type: ignore[attr-defined]
        tools.aci_goal_update(  # type: ignore[attr-defined]
            "",
            "complete",
            "Review completed and patch finalized.",
            evidence_refs_json='["tool_call:aci_submit_patch_finalize"]',
        )
        return AgentInvocationResult(content="M0.10 path complete.")


class FakeActionRecoveryAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
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


class FakePipeVerificationAgent:
    def run(
        self,
        config: RunConfig,
        tools: object,
        prompt: str,
        model_provider: object = None,
        **kwargs: object,
    ) -> AgentFinalResult:
        command = tools.workspace_run(  # type: ignore[attr-defined]
            "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
        )
        tools.aci_view("repo/app.py")  # type: ignore[attr-defined]
        tools.aci_replace("repo/app.py", "return 'old'", "return 'new'")  # type: ignore[attr-defined]
        tools.aci_verify("python3 -m pytest missing_test.py 2>&1 | head -10", "repo")  # type: ignore[attr-defined]
        tools.aci_submit_patch(  # type: ignore[attr-defined]
            no_command_verification_rationale="No command verifier exists for this text-only generated fixture; reviewed the exact diff."
        )
        return AgentFinalResult(
            status="completed",
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
            problem_statement_summary="Configured issue asks for old marker to become new.",
            reproduction_notes="Inspected repo/app.py and found the old marker.",
            verification_summary="No command verifier exists; reviewed the exact diff.",
        )


@contextmanager
def _legacy_runner_auto_submit():
    previous = os.environ.get("CONTRIBARENA_LEGACY_RUNNER_AUTO_SUBMIT")
    os.environ["CONTRIBARENA_LEGACY_RUNNER_AUTO_SUBMIT"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CONTRIBARENA_LEGACY_RUNNER_AUTO_SUBMIT", None)
        else:
            os.environ["CONTRIBARENA_LEGACY_RUNNER_AUTO_SUBMIT"] = previous


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
        self.assertIn("GitHub write tools", instructions)
        self.assertIn("open the PR yourself", instructions)
        self.assertIn("patch review completes with aci_submit_patch_finalize", instructions)
        self.assertIn("live contribution completes only when github_open_pr returns opened or existing", instructions)
        self.assertIn("github_open_pr", instructions)

    def test_live_goal_prompt_spells_out_post_finalize_pr_recipe(self) -> None:
        prompt = build_goal_prompt(_owned_live_config(Path("runs"), live_enabled=True))

        self.assertIn("patch review completion and live contribution completion are separate", prompt)
        self.assertIn("After finalize, stay in the same workspace", prompt)
        self.assertIn("github_prepare_fork", prompt)
        self.assertIn("github_prepare_branch", prompt)
        self.assertIn("github_commit", prompt)
        self.assertIn("github_push_branch", prompt)
        self.assertIn("github_open_pr", prompt)
        self.assertIn("opened or existing", prompt)

    def test_shadow_goal_prompt_does_not_include_live_pr_recipe(self) -> None:
        prompt = build_goal_prompt(_config(Path("runs")))

        self.assertIn("Shadow mode", prompt)
        self.assertIn("do not open a live PR", prompt)
        self.assertNotIn("github_prepare_fork", prompt)
        self.assertNotIn("github_push_branch", prompt)
        self.assertNotIn("opened or existing", prompt)

    def test_guidance_sidecar_adds_live_pr_recipe_only_for_live_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            live_workspace = _GuidanceWorkspace(tmp_path / "live")
            shadow_workspace = _GuidanceWorkspace(tmp_path / "shadow")

            live = install_guidance_sidecar(
                live_workspace,
                _owned_live_config(tmp_path / "runs", live_enabled=True),
                run_id="run-live",
                repo_full_name="example/repo",
            )
            shadow = install_guidance_sidecar(
                shadow_workspace,
                _config(tmp_path / "shadow-runs"),
                run_id="run-shadow",
                repo_full_name="example/repo",
            )

            self.assertTrue(live.installed)
            self.assertTrue(shadow.installed)
            live_entry = (tmp_path / "live" / ".contribarena/guidance/guidance_entry.md").read_text()
            shadow_entry = (
                tmp_path / "shadow" / ".contribarena/guidance/guidance_entry.md"
            ).read_text()
            self.assertIn("Live PR submission recipe", live_entry)
            self.assertIn("aci_submit_patch_finalize means the reviewed patch is ready", live_entry)
            self.assertIn("github_prepare_fork", live_entry)
            self.assertIn("github_open_pr returns", live_entry)
            self.assertIn("opened or existing", live_entry)
            self.assertNotIn("Live PR submission recipe", shadow_entry)
            self.assertNotIn("github_prepare_fork", shadow_entry)

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
            typed_commands = {
                item["command"]: item.get("command_type")
                for item in workspace_command["commands"]
            }
            self.assertEqual(
                "setup",
                typed_commands[
                    "git clone https://github.com/example/repo.git repo && cd repo && git status --short"
                ],
            )
            self.assertEqual(
                "verification",
                typed_commands["cd repo && python3 -m compileall ."],
            )
            self.assertEqual(
                "setup",
                next(
                    command_type
                    for command, command_type in typed_commands.items()
                    if "git diff --binary" in command
                ),
            )
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
                **kwargs: object,
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
                **kwargs: object,
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
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("completed", result.status)
            self.assertIn("## Submit-Time Review", report)
            self.assertIn("submit-time review passed", report)
            self.assertIn("## Prior Submit-Time Review Failures", report)
            self.assertIn("requires successful focused verification after the last edit", report)
            self.assertIn("aci_verify passed", terminal["message"])
            self.assertIn("compile ok", terminal["message"])
            self.assertNotIn(
                "requires successful focused verification after the last edit",
                terminal["message"],
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
                FakeLiveSubmittingAgent(),
                _owned_live_config(tmp_path / "runs", live_enabled=False),
                tmp_path,
                pr_client=FakePrClient(actor="contribarena-bot"),
            )

            self.assertEqual("failed", result.status)
            self.assertEqual("live_pr_governance_blocked", result.terminal_reason)
            self.assertEqual("pr", result.terminal_layer)
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            self.assertIn('"status": "blocked"', live_action_log)
            self.assertIn('"external_write": false', live_action_log)
            self.assertIn("governance live_enabled is false", live_action_log)
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("completed", terminal["agent_status"])
            self.assertEqual("failed", terminal["harness_status"])

    def test_owned_live_run_pushes_fork_branch_and_records_opened_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeLiveSubmittingAgent(),
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
            self.assertEqual([], pr_client.last_labels)
            self.assertEqual(
                "contribarena-bot:contribarena/fix-configured-problem",
                pr_client.last_head,
            )
            self.assertIn("Replace the old marker", pr_client.last_body)
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
                    "github.prepare_branch",
                    "github.commit",
                    "github.push_fork_branch",
                    "github.open_pr",
                ],
                [entry["action"] for entry in live_action_entries],
            )
            self.assertEqual("ready", live_action_entries[0]["status"])
            self.assertEqual("prepared", live_action_entries[1]["status"])
            self.assertIn("patch_restored", live_action_entries[1])
            self.assertEqual("committed", live_action_entries[2]["status"])
            self.assertEqual("pushed", live_action_entries[3]["status"])
            self.assertEqual("opened", live_action_entries[4]["status"])
            self.assertEqual("contribarena-bot", live_action_entries[0]["requested_fork_owner"])
            self.assertEqual("contribarena-bot/repo", live_action_entries[0]["push_repository"])
            self.assertEqual(
                "contribarena-bot:contribarena/fix-configured-problem",
                live_action_entries[4]["head"],
            )
            self.assertIn('"status": "opened"', live_action_log)
            self.assertIn('"external_write": true', live_action_log)
            self.assertIn('"url": "https://github.com/example/repo/pull/42"', live_action_log)
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
            self.assertEqual("dry_run", ci_status["source"])
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
                    FakeLiveSubmittingAgent(),
                    config,
                    tmp_path,
                    pr_client=pr_client,
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            self.assertEqual(0, pr_client.ensure_fork_calls)
            self.assertEqual("example:contribarena/fix-configured-problem", pr_client.last_head)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [
                    "github.prepare_upstream_branch",
                    "github.prepare_branch",
                    "github.commit",
                    "github.push_upstream_branch",
                    "github.open_pr",
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
                with _legacy_runner_auto_submit():
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
                with _legacy_runner_auto_submit():
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
        self.assertIn("git -c http.version=HTTP/1.1 -C repo fetch", command)
        self.assertIn("git -c http.version=HTTP/1.1 -C repo push contribarena-submit", command)

    def test_owned_live_run_blocks_when_authenticated_actor_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeLiveSubmittingAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(actor="wrong-bot"),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("failed", result.status)
            live_action_log = (result.run_dir / "live_action_log.jsonl").read_text()
            self.assertIn(
                "authenticated actor wrong-bot does not match expected contribarena-bot",
                live_action_log,
            )

    def test_owned_live_transient_prepare_branch_failure_schedules_agent_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(id="season_0:gpt-5.5", model="responses/gpt55")],
            )
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeLiveSubmittingAgent(),
                    config,
                    tmp_path,
                    pr_client=FakePrClient(actor="contribarena-bot"),
                    prepare_branch_failure_stderr=(
                        "fatal: unable to access 'https://github.com/example/repo.git/': "
                        "Failed to connect to github.com port 443"
                    ),
                )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("failed", result.status)
            self.assertEqual("live_pr_infrastructure_failed", result.terminal_reason)
            self.assertEqual("pr", result.terminal_layer)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            prepare_branch = next(
                entry for entry in live_action_entries if entry["action"] == "github.prepare_branch"
            )
            self.assertEqual("failed", prepare_branch["status"])
            self.assertTrue(prepare_branch["retryable"])
            self.assertEqual("git_prepare_branch_transient", prepare_branch["error_kind"])
            retry = json.loads((result.run_dir / "live_submission_retry_state.json").read_text())
            self.assertEqual("due", retry["status"])
            self.assertEqual(result.run_id, retry["source_run_id"])
            self.assertEqual("github.prepare_branch", retry["action"])
            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("due", summary["live_submission_retry"]["status"])
            self.assertFalse((result.run_dir / "replacement_state.json").exists())
            state = load_participant_state(
                SeasonStore.from_config(config),
                "season_0",
                "season_0:gpt-5.5",
            )
            self.assertTrue(state["live_submission_retry_due"])
            self.assertEqual("due", state["live_submission_retry"]["status"])
            self.assertEqual(1, state["live_submission_retry"]["attempts"])

    def test_runtime_context_exposes_live_submission_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(id="season_0:gpt-5.5", model="responses/gpt55")],
            )
            store = SeasonStore.from_config(config)
            participant_dir = store.participant_dir("season_0", "season_0:gpt-5.5")
            participant_dir.mkdir(parents=True, exist_ok=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps(
                    {
                        "live_submission_retry": {
                            "status": "running",
                            "attempts": 1,
                            "max_attempts": 3,
                            "source_run_id": "previous-run",
                            "action": "github.prepare_branch",
                            "reason": "git_prepare_branch_transient",
                            "message": "Failed to connect to github.com port 443",
                            "run_dir": "/tmp/previous-run",
                        }
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            agent = FakeLiveSubmissionContextAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            self.assertEqual("failed", result.status)
            retry = agent.runtime_context["live_submission_retry"]
            self.assertEqual("running", retry["status"])
            self.assertEqual("previous-run", retry["source_run_id"])
            self.assertEqual("github.prepare_branch", retry["action"])
            self.assertIn("agent-owned live submission continuation", retry["instruction"])
            state = load_participant_state(
                SeasonStore.from_config(config),
                "season_0",
                "season_0:gpt-5.5",
            )
            self.assertEqual("failed", state["live_submission_retry"]["status"])
            self.assertFalse(state["live_submission_retry_due"])

    def test_manual_run_consumes_due_live_submission_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(id="season_0:gpt-5.5", model="responses/gpt55")],
            )
            store = SeasonStore.from_config(config)
            participant_dir = store.participant_dir("season_0", "season_0:gpt-5.5")
            participant_dir.mkdir(parents=True, exist_ok=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps(
                    {
                        "live_submission_retry": {
                            "status": "due",
                            "attempts": 1,
                            "max_attempts": 3,
                            "source_run_id": "previous-run",
                            "action": "github.prepare_branch",
                            "reason": "git_prepare_branch_transient",
                            "message": "Failed to connect to github.com port 443",
                            "run_dir": "/tmp/previous-run",
                        },
                        "live_submission_retry_due": True,
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )

            result = _run_with_fake_docker(FakeIssueAgent(), config, tmp_path)

            self.assertEqual("failed", result.status)
            state = load_participant_state(store, "season_0", "season_0:gpt-5.5")
            self.assertEqual("failed", state["live_submission_retry"]["status"])
            self.assertEqual(result.run_id, state["live_submission_retry"]["retry_run_id"])
            self.assertFalse(state["live_submission_retry_due"])

    def test_owned_live_fork_failure_records_requested_fork_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                result = _run_with_fake_docker(
                    FakeLiveSubmittingAgent(),
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
            self.assertEqual("live_pr_infrastructure_failed", result.terminal_reason)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual("github.ensure_fork", live_action_entries[0]["action"])
            self.assertEqual("failed", live_action_entries[0]["status"])
            self.assertEqual("contribarena-bot", live_action_entries[0]["requested_fork_owner"])
            self.assertIn("authentication missing", live_action_entries[0]["error"])

    def test_external_live_run_uses_fork_only_and_records_lifecycle_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with (
                    _legacy_runner_auto_submit(),
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
                        FakePhasedExternalAgent(repo_default_branch="develop"),
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
            self.assertEqual([], pr_client.last_labels)
            live_action_entries = [
                json.loads(line)
                for line in (result.run_dir / "live_action_log.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual("external_live", live_action_entries[0]["mode"])
            self.assertEqual("github.ensure_fork", live_action_entries[0]["action"])
            self.assertEqual("github.push_fork_branch", live_action_entries[1]["action"])
            self.assertEqual("github.open_pr", live_action_entries[2]["action"])
            self.assertEqual("github.ensure_labels", live_action_entries[3]["action"])
            self.assertEqual("skipped", live_action_entries[3]["status"])
            self.assertEqual("policy", live_action_entries[3]["source"])
            self.assertEqual("github.observe_checks", live_action_entries[4]["action"])
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
                with _legacy_runner_auto_submit(), patch(
                    "contribarena.engine.runner.repo_check_eligibility",
                    return_value=EligibilityResult(
                        eligible=False,
                        reasons=["repository policy appears to prohibit bot or AI contributions"],
                        checks_performed=["bot_policy"],
                    ),
                ):
                    result = _run_with_fake_docker(
                        FakePhasedExternalAgent(repo_default_branch="main"),
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

    def test_external_live_code_only_config_blocks_docs_only_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with (
                    _legacy_runner_auto_submit(),
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
                            default_branch="main",
                        ),
                    ),
                ):
                    result = _run_with_fake_docker(
                        FakePhasedExternalAgent(
                            repo_default_branch="main",
                            edit_path="repo/docs/guide.md",
                        ),
                        config,
                        tmp_path,
                        diff_path="repo/docs/guide.md",
                        pr_client=pr_client,
                    )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, pr_client.calls)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertEqual("block", decision["status"])
            self.assertEqual("docs", decision["contribution_class"])
            self.assertIn("contribution class is not allowed: docs", decision["reasons"])
            self.assertIn(
                "code-only run requires at least one code or test path",
                "; ".join(decision["reasons"]),
            )

    def test_external_live_code_only_config_allows_test_fixture_text_when_tests_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with (
                    _legacy_runner_auto_submit(),
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
                            default_branch="main",
                        ),
                    ),
                ):
                    result = _run_with_fake_docker(
                        FakePhasedExternalAgent(
                            repo_default_branch="main",
                            edit_path="repo/tests/fixtures/sample.txt",
                        ),
                        config,
                        tmp_path,
                        diff_path="repo/tests/fixtures/sample.txt",
                        pr_client=pr_client,
                    )
            finally:
                os.environ.pop("GITHUB_TOKEN", None)

            self.assertEqual("completed", result.status)
            decision = json.loads((result.run_dir / "governance_decision.json").read_text())
            self.assertEqual("tests", decision["contribution_class"])

    def test_external_live_blocks_when_independent_eligibility_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _external_live_config(tmp_path / "runs", live_enabled=True)
            pr_client = FakePrClient(actor="contribarena-bot")
            os.environ["GITHUB_TOKEN"] = "test-token"
            try:
                with _legacy_runner_auto_submit(), patch(
                    "contribarena.engine.runner.repo_check_eligibility",
                    side_effect=RuntimeError("network unavailable"),
                ):
                    result = _run_with_fake_docker(
                        FakePhasedExternalAgent(repo_default_branch="main"),
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
                    FakeLiveSubmittingAgent(),
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
            self.assertEqual("live_pr_infrastructure_failed", result.terminal_reason)
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
                    FakeLiveSubmittingAgent(),
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

    def test_live_push_transient_failure_retries_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_live_config(tmp_path / "runs", live_enabled=True)
            result = _run_with_fake_docker(
                FakeLiveSubmittingAgent(),
                config,
                tmp_path,
                pr_client=FakePrClient(actor="contribarena-bot"),
                push_transient_failures_before_success=2,
            )

            self.assertEqual("completed", result.status)
            self.assertFalse((result.run_dir / "replacement_state.json").exists())
            workspace_command = json.loads((result.run_dir / "workspace_command.json").read_text())
            push_commands = [
                command
                for command in workspace_command["commands"]
                if "git -c http.version=HTTP/1.1 -C repo push contribarena-submit"
                in command["command"]
            ]
            self.assertEqual(3, len(push_commands))
            self.assertEqual([1, 1, 0], [command["exit_code"] for command in push_commands])

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

    def test_provider_error_writes_model_runtime_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            result = _run_with_fake_docker(FakeProviderErrorAgent(), config, tmp_path)

            self.assertEqual("failed", result.status)
            self.assertEqual("model_runtime", result.terminal_reason)
            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("model_runtime", terminal["reason"])
            self.assertEqual("model_runtime", terminal["layer"])
            trace_events = [
                json.loads(line)
                for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertIn("agent.invocation_failed", {event["event"] for event in trace_events})
            self.assertTrue((result.run_dir / "judgement.json").exists())
            self.assertFalse((result.run_dir / "replacement_state.json").exists())

    def test_transient_provider_error_is_replacement_and_not_judged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            result = _run_with_fake_docker(FakeTransientProviderErrorAgent(), config, tmp_path)

            self.assertEqual("failed", result.status)
            self.assertEqual("model_runtime", result.terminal_layer)
            replacement = json.loads((result.run_dir / "replacement_state.json").read_text())
            self.assertEqual("due", replacement["status"])
            self.assertEqual("model_runtime", replacement["layer"])
            self.assertFalse((result.run_dir / "judgement.json").exists())
            self.assertFalse((result.run_dir / "judgement_retry_state.json").exists())
            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("due", summary["replacement"]["status"])
            self.assertEqual("not_judged", summary["judgement"]["status"])
            manifest = json.loads((result.run_dir / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("replacement_state.json", manifest_names)
            self.assertNotIn("judgement.json", manifest_names)

    def test_provider_server_error_is_replacement_and_not_judged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            result = _run_with_fake_docker(FakeProviderServerErrorAgent(), config, tmp_path)

            self.assertEqual("failed", result.status)
            self.assertEqual("model_runtime", result.terminal_layer)
            replacement = json.loads((result.run_dir / "replacement_state.json").read_text())
            self.assertEqual("due", replacement["status"])
            self.assertEqual("model_runtime", replacement["layer"])
            self.assertFalse((result.run_dir / "judgement.json").exists())
            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("due", summary["replacement"]["status"])
            self.assertEqual("not_judged", summary["judgement"]["status"])

    def test_provider_infrastructure_failure_does_not_advance_auto_wake_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "auto"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(id="season_0:gpt-5.5", model="responses/gpt55")],
            )
            store = SeasonStore.from_config(config)
            participant_dir = store.participant_dir("season_0", "season_0:gpt-5.5")
            participant_dir.mkdir(parents=True)
            state_path = participant_dir / "participant_state.json"
            state_path.write_text(
                json.dumps({"last_wake_at": "2026-01-01T00:00:00+00:00"}) + "\n",
                encoding="utf-8",
            )

            _run_with_fake_docker(FakeProviderBillingErrorAgent(), config, tmp_path)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-01-01T00:00:00+00:00", state["last_wake_at"])
            self.assertEqual(0, state["active_runs"])
            self.assertEqual(state["last_run_id"], state["wake_cooldown_suppressed_run_id"])
            self.assertEqual("completed", state["pending_run"]["status"])
            self.assertNotIn("previous_last_wake_at", state)

    def test_transient_runtime_message_markers_are_conservative(self) -> None:
        self.assertTrue(_transient_runtime_message("APIConnectionError: Connection error."))
        self.assertTrue(_transient_runtime_message("Gateway timeout from provider"))
        self.assertTrue(_transient_runtime_message("HTTP 503 service unavailable"))
        self.assertTrue(_transient_runtime_message("HTTP 500 internal server error"))
        self.assertTrue(_transient_runtime_message("Error code: 500 - {'message': '请求参数不能为空'}"))
        self.assertTrue(_transient_runtime_message("GnuTLS recv error (-110)"))
        self.assertFalse(_transient_runtime_message("HTTP 400 bad request"))
        self.assertFalse(_transient_runtime_message("context_length_exceeded"))
        self.assertFalse(_transient_runtime_message("unsupported tool format"))

    def test_provider_infrastructure_message_matches_billing_and_auth_only(self) -> None:
        self.assertTrue(_provider_infrastructure_message("INSUFFICIENT_BALANCE"))
        self.assertTrue(_provider_infrastructure_message("PermissionDeniedError: invalid api key"))
        self.assertTrue(_provider_infrastructure_message("HTTP 429 rate limit exceeded"))
        self.assertFalse(_provider_infrastructure_message("HTTP 500 internal server error"))
        self.assertFalse(_provider_infrastructure_message("APIConnectionError: connection reset"))

    def test_pr_publish_failure_does_not_trigger_participant_replacement(self) -> None:
        terminal = TerminalState(
            status="failed",
            reason="pr_branch_push_failed",
            layer="pr",
            message="fatal: unable to access github: GnuTLS recv error (-110)",
        )

        self.assertFalse(_replacement_due_terminal(terminal))

    def test_provider_error_message_reaches_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            result = _run_with_fake_docker(FakeBlankProviderErrorAgent(), config, tmp_path)

            terminal = json.loads((result.run_dir / "terminal_state.json").read_text())
            self.assertEqual("ReadTimeout", terminal["message"])

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

    def test_runner_writes_surface_run_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("1", summary["schema_version"])
            self.assertEqual(result.run_id, summary["run_id"])
            self.assertEqual("completed", summary["run_status"])
            self.assertEqual("run_completed", summary["terminal_reason"])
            self.assertEqual("example/repo", summary["repository"]["full_name"])
            self.assertEqual("discovery_event_id", summary["opportunity_source"])
            self.assertEqual("low_risk_code", summary["contribution_class"])
            self.assertEqual("pass", summary["quality_gate"]["status"])
            stage_statuses = {
                stage["stage_id"]: stage["status"] for stage in summary["pipeline"]
            }
            self.assertEqual("passed", stage_statuses["agent"])
            self.assertEqual("passed", stage_statuses["patch_diff"])
            self.assertEqual("passed", stage_statuses["quality_gate"])
            self.assertEqual("skipped", stage_statuses["pull_request"])
            artifacts = {artifact["name"]: artifact for artifact in summary["artifacts"]}
            self.assertEqual("operator", artifacts["agent_final_result.json"]["visibility"])
            self.assertEqual("public", artifacts["run_summary.json"]["visibility"])
            self.assertEqual("public", artifacts["patch.diff"]["visibility"])
            self.assertEqual("public", artifacts["judgement.json"]["visibility"])
            self.assertEqual("public", artifacts["judge_packet.json"]["visibility"])
            self.assertEqual("public", artifacts["judge_dimension_packets.json"]["visibility"])
            self.assertEqual("internal", artifacts["trace.jsonl"]["visibility"])
            self.assertEqual("season_0", summary["season"]["id"])
            self.assertEqual("fallback", summary["judgement"]["status"])
            self.assertGreater(summary["judgement"]["judge_score"], 0)
            self.assertEqual(0, summary["judgement"]["real_world_adjustment"])
            self.assertEqual(
                summary["judgement"]["judge_score"],
                summary["judgement"]["arena_score"],
            )
            manifest = json.loads((result.run_dir / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("agent_final_result.json", manifest_names)
            self.assertIn("run_summary.json", manifest_names)
            self.assertIn("judge_packet.json", manifest_names)
            self.assertIn("judge_dimension_packets.json", manifest_names)
            self.assertIn("judgement.json", manifest_names)

    def test_runner_writes_anonymized_judgement_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.run.model = "compatible/worker-model"
            config.judgement.judges = [
                JudgementJudgeConfig(id="judge_a", model="compatible/judge-a"),
                JudgementJudgeConfig(id="judge_b", model="compatible/judge-b"),
            ]
            with patch("contribarena.engine.judgement._sleep_before_retry"):
                result = _run_with_fake_docker(FakeIssueAgent(), config, tmp_path)

            packet = json.loads((result.run_dir / "judge_packet.json").read_text())
            self.assertEqual(result.run_id, packet["run_id"])
            self.assertNotIn("model", packet)
            self.assertNotIn("agent", packet)
            self.assertEqual("example/repo", packet["repository"]["full_name"])
            dimension_packets = json.loads(
                (result.run_dir / "judge_dimension_packets.json").read_text()
            )
            self.assertIn("execution_correctness", dimension_packets)
            self.assertIn("patch_excerpt", dimension_packets["execution_correctness"])
            self.assertNotIn("model", dimension_packets["execution_correctness"])

            judgement = json.loads((result.run_dir / "judgement.json").read_text())
            self.assertEqual("fallback", judgement["status"])
            self.assertEqual("season_0", judgement["season_id"])
            self.assertEqual("judge_dimension_packets.json", judgement["judge_dimension_packets"])
            self.assertEqual("mean", judgement["judge_panel"]["aggregation"])
            self.assertEqual(["judge_a", "judge_b"], [j["judge_id"] for j in judgement["judges"]])
            self.assertEqual(9, len(judgement["aggregate_rubric"]))
            self.assertIn(
                "submission_discipline",
                {score["dimension"] for score in judgement["aggregate_rubric"]},
            )
            self.assertAlmostEqual(
                1.0,
                sum(score["weight"] for score in judgement["aggregate_rubric"]),
            )
            operator_events = [
                json.loads(line)
                for line in (result.run_dir / "operator_events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            self.assertIn(
                "judgement",
                {event["phase"] for event in operator_events},
            )
            self.assertEqual(
                0.25,
                next(
                    score["weight"]
                    for score in judgement["aggregate_rubric"]
                    if score["dimension"] == "execution_correctness"
                ),
            )
            self.assertGreater(judgement["judge_score"], 0)
            self.assertGreaterEqual(judgement["arena_score"], 0)
            self.assertTrue(
                all(
                    0 <= score["score"] <= 5
                    and "weight" in score
                    and score["source"] == "fallback"
                    for judge in judgement["judges"]
                    for score in judge["rubric"]
                )
            )
            self.assertIn("judge_packet.json", judgement["evidence"])
            self.assertIn("judge_dimension_packets.json", judgement["evidence"])

    def test_runner_defers_transient_judgement_fallback_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )
            judgement_path = result.run_dir / "judgement.json"
            judgement = json.loads(judgement_path.read_text(encoding="utf-8"))
            judgement["status"] = "partial_fallback"
            judgement["judge_score"] = 12
            judgement["arena_score"] = 12
            judgement["judges"] = [
                {
                    "judge_id": "responses_gpt55",
                    "model": "responses/gpt55",
                    "error": "project_fit: APIConnectionError: Connection error.",
                    "rubric": [],
                }
            ]
            judgement_path.write_text(
                json.dumps(judgement, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            from contribarena.engine.judge_refresh import mark_transient_judgement_retry_due

            self.assertTrue(mark_transient_judgement_retry_due(result.run_dir))

            summary = json.loads((result.run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("deferred", summary["judgement"]["status"])
            self.assertEqual("due", summary["judgement_retry"]["status"])

    def test_workspace_cleanup_runs_before_judgement_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(FakeIssueAgent(), _issue_config(tmp_path / "runs"), tmp_path)

            events = [
                json.loads(line)
                for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
                if line.strip()
            ]
            event_names = [event["event"] for event in events]
            self.assertLess(
                event_names.index("workspace.stopping"),
                event_names.index("artifacts.written"),
            )
            self.assertLess(
                event_names.index("workspace.stopped"),
                event_names.index("artifacts.written"),
            )

    def test_pipefail_prevents_piped_verification_from_masking_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakePipeVerificationAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            quality_gate = json.loads((result.run_dir / "quality_gate.json").read_text())
            test_log = (result.run_dir / "test_log.txt").read_text()
            self.assertEqual("pass", quality_gate["status"])
            self.assertIn("python3 -m pytest missing_test.py 2>&1 | head -10", test_log)
            self.assertIn("- Exit code: 1", test_log)

    def test_surface_run_summary_written_for_agent_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.workspace.cleanup_policy = "retain_on_failure"

            with self.assertRaises(AgentError):
                _run_with_fake_docker(FakeFailingAgent(), config, tmp_path)

            run_dirs = list(config.artifacts.output_root.iterdir())
            self.assertEqual(1, len(run_dirs))
            summary = json.loads((run_dirs[0] / "run_summary.json").read_text())
            self.assertEqual("failed", summary["run_status"])
            self.assertEqual("agent_error", summary["terminal_reason"])
            self.assertEqual("agent", summary["terminal_layer"])
            stage_statuses = {
                stage["stage_id"]: stage["status"] for stage in summary["pipeline"]
            }
            self.assertEqual("failed", stage_statuses["agent"])
            self.assertEqual("skipped", stage_statuses["patch_diff"])
            self.assertTrue((run_dirs[0] / "judge_packet.json").exists())
            self.assertTrue((run_dirs[0] / "judge_dimension_packets.json").exists())
            self.assertTrue((run_dirs[0] / "judgement.json").exists())
            judgement = json.loads((run_dirs[0] / "judgement.json").read_text())
            self.assertEqual("fallback", judgement["status"])
            self.assertEqual("unknown", judgement["maintainer_outcome"]["status"])
            self.assertEqual(0, judgement["aggregate_rubric"][0]["mean_score"])
            manifest = json.loads((run_dirs[0] / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("run_summary.json", manifest_names)
            self.assertIn("judge_dimension_packets.json", manifest_names)
            self.assertIn("judgement.json", manifest_names)

    def test_new_agent_surface_retires_operator_progress_tool(self) -> None:
        instructions = build_agent_instructions(_issue_config(Path("runs")))

        self.assertNotIn("operator_report_progress", instructions)

    def test_runner_writes_empty_assistant_updates_artifact_for_legacy_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_with_fake_docker(
                FakeIssueAgent(),
                _issue_config(tmp_path / "runs"),
                tmp_path,
            )

            updates_path = result.run_dir / "assistant_updates.jsonl"
            self.assertTrue(updates_path.exists())
            self.assertEqual("", updates_path.read_text(encoding="utf-8").strip())
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            self.assertNotIn("operator_report_progress", {step["tool"] for step in trajectory})

    def test_assistant_update_uses_runtime_run_id(self) -> None:
        config = _issue_config(Path("runs"))
        goals = GoalService(config, run_id="runtime-run")
        invocation_context = type("InvocationContext", (), {"invocation_seq": 2})()
        tool_call = type("ToolCall", (), {"name": "repo_search", "call_id": "call-123"})()

        update = _build_assistant_update(
            config=config,
            run_id="runtime-run",
            goals=goals,
            invocation_context=invocation_context,
            turn=ProviderTurn(visible_segments=[VisibleSegment(text="I will search repos.")]),
            tool_call=tool_call,
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual("runtime-run", update.run_id)
        self.assertEqual(["tool_call:call-123"], update.evidence_refs)

    def test_runner_writes_guidance_and_memory_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.run.id = "goal-half"
            config.memory = MemoryConfig(root=tmp_path / "memory")
            agent = FakeMemoryAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            guidance = json.loads((result.run_dir / "repo_guidance.json").read_text())
            self.assertTrue(guidance["installed"])
            self.assertIn("AGENTS.md", guidance["manifest"]["expected_repo_sources"])
            self.assertIn("aci_runtime_get_context(scope='run')", agent.prompt)
            self.assertIn("aci_goal_update", agent.prompt)
            self.assertIn("AGENTS.md", agent.prompt)
            self.assertIn("meaningful engineering contributions", agent.memory_context["goals"]["long_term_objective"])
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                agent.memory_context["guidance"]["entry_path"],
            )
            self.assertEqual(
                "workspace_root",
                agent.memory_context["guidance"]["path_relative_to"],
            )
            memory_context = json.loads((result.run_dir / "memory_context.json").read_text())
            self.assertEqual("run_start_context", memory_context["snapshot_phase"])
            self.assertIn("Final goal state", memory_context["snapshot_note"])
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                memory_context["guidance"]["entry_path"],
            )
            working = json.loads((result.run_dir / "working_memory.json").read_text())
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                working["guidance"]["entry_path"],
            )
            self.assertEqual(
                "repo/CONTRIBUTING.md",
                working["facts"]["contributing_checked"]["value"],
            )
            self.assertEqual("agent", working["facts"]["contributing"]["source"])
            self.assertEqual("complete", working["goals"]["short_term"]["status"])
            goal_context = json.loads((result.run_dir / "goal_context.json").read_text())
            self.assertEqual("complete", goal_context["short_term"]["status"])
            goal_events = (result.run_dir / "goal_events.jsonl").read_text()
            self.assertIn("goal_created", goal_events)
            self.assertIn("goal_completed", goal_events)
            report = json.loads((result.run_dir / "memory_write_report.json").read_text())
            self.assertGreater(report["history_index_entries_written"], 0)
            manifest = json.loads((result.run_dir / "artifact_manifest.json").read_text())
            manifest_names = {entry["name"] for entry in manifest["artifacts"]}
            self.assertIn("memory_context.json", manifest_names)
            self.assertIn("working_memory.json", manifest_names)
            self.assertIn("memory_events.jsonl", manifest_names)
            self.assertIn("memory_write_report.json", manifest_names)
            self.assertIn("goal_context.json", manifest_names)
            self.assertIn("goal_events.jsonl", manifest_names)

    def test_goal_continuation_two_runs_finishes_active_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(root=tmp_path / "memory")
            GoalService(config, run_id="seed").update(
                objective="Finish a meaningful small code improvement in example/repo.",
                status="active",
            )
            first_agent = FakeGoalHalfRunAgent()

            first = _run_with_fake_docker(first_agent, config, tmp_path)

            self.assertEqual("blocked", first.status)
            self.assertEqual("active", first_agent.runtime_context["goals"]["short_term"]["status"])
            self.assertEqual(
                "active",
                json.loads(goal_state_path(config).read_text())["short_term"]["status"],
            )
            first_trajectory = json.loads((first.run_dir / "trajectory.json").read_text())
            self.assertIn("aci_runtime_get_context", {step["tool"] for step in first_trajectory})
            self.assertFalse((first.run_dir / "patch.diff").read_text().strip())

            second_agent = FakeGoalCompletingAgent()
            config.run.id = "goal-complete"
            config.run.model = "local-stub-continuation"
            second = _run_with_fake_docker(second_agent, config, tmp_path)

            self.assertEqual("completed", second.status)
            self.assertEqual("active", second_agent.runtime_context["goals"]["short_term"]["status"])
            self.assertEqual("complete", second_agent.goal_update["goals"]["short_term"]["status"])
            self.assertEqual(
                "complete",
                json.loads(goal_state_path(config).read_text())["short_term"]["status"],
            )
            second_trajectory = json.loads((second.run_dir / "trajectory.json").read_text())
            tools = {step["tool"] for step in second_trajectory}
            self.assertIn("aci_runtime_get_context", tools)
            self.assertIn("aci_goal_update", tools)
            self.assertIn("aci_submit_patch", tools)
            goal_events = (second.run_dir / "goal_events.jsonl").read_text()
            self.assertIn("goal_completed", goal_events)

    def test_invocation_continuation_reuses_same_sdk_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            agent = FakeSessionContinuationAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            self.assertEqual("completed", result.status)
            self.assertEqual(2, len(agent.context_ids))
            self.assertEqual(agent.context_ids[0], agent.context_ids[1])
            trace_events = [
                json.loads(line)
                for line in (result.run_dir / "trace.jsonl").read_text().splitlines()
            ]
            reviews = [
                event["payload"]
                for event in trace_events
                if event["event"] == "agent.invocation_reviewed"
            ]
            self.assertEqual("continue", reviews[0]["decision"])
            self.assertEqual("patch_submitted", reviews[1]["outcome"])

    def test_live_mode_continues_after_patch_until_pr_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _owned_live_config(Path(tmp) / "runs", live_enabled=True)
            goals = GoalService(config, run_id="run-live")
            goals.update(
                objective="Open a live PR.",
                status="active",
                scope="contribution",
                evidence_refs=["tool_call:repo.metadata"],
            )
            capture = ArtifactCapture()
            state = AgentLoopState()
            before_finalize = capture_cursor(capture, goals, None)
            capture.record_aci_result(AciResult(tool="aci_submit_patch_finalize", success=True))

            review = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before_finalize,
                state=state,
                invocation=AgentInvocationResult(content="Patch finalized."),
            )

            self.assertEqual("continue", review.decision)
            self.assertEqual("live_pr_required_after_patch", review.reason)
            self.assertIn("governed PR opened/existing record", state.recovery_warning)
            self.assertIn("live Review guidance", state.recovery_warning)

            before_pr = capture_cursor(capture, goals, None)
            capture.record_aci_result(
                AciResult(
                    tool="github_open_pr",
                    success=True,
                    output=json.dumps({"number": 23, "url": "https://github.com/example/repo/pull/23"}),
                )
            )
            opened = review_invocation(
                config=config,
                capture=capture,
                goals=goals,
                memory=None,
                before=before_pr,
                state=state,
                invocation=AgentInvocationResult(content="PR opened."),
            )

            self.assertEqual("terminal", opened.decision)
            self.assertEqual("opened_pr", opened.outcome)

    def test_live_continuation_prompt_directs_pr_submission_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _owned_live_config(Path(tmp) / "runs", live_enabled=True)
            goals = GoalService(config, run_id="run-live")
            goals.update(
                objective="Open a live PR.",
                status="active",
                scope="contribution",
                evidence_refs=["tool_call:repo.metadata"],
            )
            capture = ArtifactCapture()
            capture.record_aci_result(AciResult(tool="aci_submit_patch_finalize", success=True))
            state = AgentLoopState(recovery_warning="Previous invocation stopped after finalize.")

            prompt = render_continuation_context(
                config=config,
                goals=goals,
                state=state,
                progress=agent_loop_progress(capture, goals),
            )

            self.assertIn("no governed live PR action has been recorded", prompt)
            self.assertIn("Continue from the current workspace", prompt)
            self.assertIn("do not restart Scout or Work", prompt)
            self.assertIn("live Review guidance", prompt)

    def test_m010_shadow_path_emits_phase_review_and_judgement_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")

            result = _run_with_fake_docker(FakeM010Agent(), config, tmp_path)

            self.assertEqual("completed", result.status)
            phase_events = [
                json.loads(line)
                for line in (result.run_dir / "phase_transition.jsonl").read_text().splitlines()
            ]
            phases = {(event["phase"], event["sub_phase"]) for event in phase_events}
            self.assertIn(("scout", "opportunity"), phases)
            self.assertIn(("work", None), phases)
            self.assertIn(("review", None), phases)
            self.assertIn("draft_submitted", {event["event_type"] for event in phase_events})
            project_rows = (result.run_dir / "phase_scout_project_comparison.jsonl").read_text()
            opportunity_rows = (
                result.run_dir / "phase_scout_opportunity_comparison.jsonl"
            ).read_text()
            duplicate_rows = (result.run_dir / "phase_scout_duplicate_check.jsonl").read_text()
            review_rows = (result.run_dir / "phase_review_maintainer_review.jsonl").read_text()
            response_rows = (result.run_dir / "phase_review_response.jsonl").read_text()
            self.assertIn("repo.metadata", project_rows)
            discovery_log = (result.run_dir / "discovery_log.jsonl").read_text(encoding="utf-8")
            self.assertIn('"github_query_string"', discovery_log)
            self.assertIn('"returned_count"', discovery_log)
            self.assertIn("repo.issues", opportunity_rows)
            self.assertIn("repo.open_prs", duplicate_rows)
            self.assertIn("review_simulator_unavailable", review_rows)
            self.assertIn("severity", review_rows)
            self.assertIn("aci_dispute_review", response_rows)
            self.assertIn("aci_submit_patch_finalize", response_rows)
            judgement = json.loads((result.run_dir / "judgement.json").read_text())
            self.assertEqual("m0_10_default", judgement["judge_panel"]["panel_id"])
            dimension_names = {item["dimension"] for item in judgement["aggregate_rubric"]}
            self.assertIn("duplicate_avoidance", dimension_names)
            self.assertIn("review_readiness", dimension_names)

    def test_runner_exposes_tracked_prs_in_runtime_memory_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(root=tmp_path / "memory")
            save_governance_state(
                config,
                GovernanceState(
                    lifecycle_records=[
                        PrLifecycleRecord(
                            repository="example/repo",
                            number=17,
                            url="https://github.com/example/repo/pull/17",
                            lifecycle_status="needs_response",
                            ci_status="failure",
                            summary="tracked PR needs a small follow-up",
                        ),
                        PrLifecycleRecord(
                            repository="other/repo",
                            number=99,
                            lifecycle_status="needs_response",
                        ),
                    ]
                ),
            )
            agent = FakeMemoryAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            self.assertEqual(1, len(agent.memory_context["tracked_prs"]))
            tracked = agent.memory_context["tracked_prs"][0]
            self.assertEqual("example/repo", tracked["repository"])
            self.assertEqual(17, tracked["number"])
            self.assertEqual("needs_response", tracked["lifecycle_status"])
            self.assertEqual("external_write", tracked["detail_queries"][0]["category"])
            memory_context = json.loads((result.run_dir / "memory_context.json").read_text())
            self.assertEqual(agent.memory_context["tracked_prs"], memory_context["tracked_prs"])
            self.assertFalse((result.run_dir / "resume_context.json").exists())

    def test_guidance_disabled_skips_sidecar_and_marks_memory_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(root=tmp_path / "memory")
            config.guidance = GuidanceConfig(enabled=False)
            agent = FakeMemoryAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            guidance = json.loads((result.run_dir / "repo_guidance.json").read_text())
            self.assertFalse(guidance["enabled"])
            self.assertFalse(guidance["installed"])
            self.assertFalse(guidance["available"])
            self.assertEqual("guidance_disabled", guidance["skipped_reason"])
            self.assertEqual("guidance_disabled", agent.memory_context["guidance"]["skipped_reason"])
            self.assertFalse(agent.memory_context["guidance"]["available"])
            memory_context = json.loads((result.run_dir / "memory_context.json").read_text())
            self.assertFalse(memory_context["guidance"]["available"])

    def test_guidance_install_failure_is_nonfatal_and_marks_memory_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(root=tmp_path / "memory")
            agent = FakeIssueAgent()

            result = _run_with_fake_docker(
                agent,
                config,
                tmp_path,
                guidance_failure=True,
            )

            self.assertEqual("completed", result.status)
            guidance = json.loads((result.run_dir / "repo_guidance.json").read_text())
            self.assertTrue(guidance["enabled"])
            self.assertFalse(guidance["installed"])
            self.assertFalse(guidance["available"])
            self.assertTrue(guidance["degraded"])
            self.assertIn("guidance write failed", guidance["error"])
            memory_context = json.loads((result.run_dir / "memory_context.json").read_text())
            self.assertFalse(memory_context["guidance"]["available"])
            self.assertIn("guidance write failed", memory_context["guidance"]["error"])

    def test_memory_disabled_returns_tool_recovery_and_writes_no_memory_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _issue_config(tmp_path / "runs")
            config.memory = MemoryConfig(enabled=False, root=tmp_path / "memory")
            agent = FakeMemoryAgent()

            result = _run_with_fake_docker(agent, config, tmp_path)

            self.assertFalse((result.run_dir / "working_memory.json").exists())
            trajectory = json.loads((result.run_dir / "trajectory.json").read_text())
            memory_steps = [step for step in trajectory if step["tool"] == "aci_memory_note"]
            self.assertEqual(1, len(memory_steps))
            self.assertEqual("memory_disabled", memory_steps[0]["recovery_kind"])

    def test_season_admission_records_participant_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
                discovery_profile=SeasonDiscoveryProfileConfig(
                    scope="owned",
                    allowlist=["example/repo"],
                ),
            )

            result = _run_with_fake_docker(FakeM02Agent(), config, tmp_path)

            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("season_0", summary["season"]["id"])
            self.assertEqual("season_0:local-stub", summary["agent"]["participant_id"])
            self.assertEqual("manual", summary["wake_source"])
            self.assertTrue(
                (tmp_path / "seasons" / "season_0" / "participants" / "season_0:local-stub").is_dir()
            )
            self.assertEqual(
                tmp_path / "seasons" / "season_0" / "participants" / "season_0:local-stub" / "goal_state.json",
                goal_state_path(config),
            )
            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "memory"
                    / "events"
                    / f"{result.run_id}.jsonl"
                ).exists()
            )
            participant_state = json.loads(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "participant_state.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(1, participant_state["runs_count"])
            self.assertEqual(0, participant_state["active_runs"])
            self.assertEqual(result.run_id, participant_state["last_run_id"])
            self.assertEqual("completed", participant_state["last_run_status"])
            self.assertEqual(result.run_id, participant_state["pending_run"]["run_id"])
            self.assertEqual("completed", participant_state["pending_run"]["status"])
            self.assertEqual(0, participant_state["prs_opened"])
            self.assertEqual(0, participant_state["merged_prs"])
            self.assertEqual(0.0, participant_state["cumulative_cost"])
            self.assertEqual(
                "Submit a verified low-risk patch.",
                participant_state["latest_goal_summary"],
            )
            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "workspaces"
                    / "example-repo"
                    / "container_id"
                ).exists()
            )
            clone_state = json.loads(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "workspaces"
                    / "example-repo"
                    / "clone_state.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("example/repo", clone_state["repo_slug"])
            self.assertEqual(result.run_id, clone_state["run_id"])

    def test_manual_season_run_uses_selected_participant_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.model = "compatible/qwen36plus"
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(
                        id="season_0:qwen-3.6-plus",
                        model="compatible/qwen36plus",
                    ),
                    SeasonParticipantConfig(
                        id="season_0:gpt-5.5",
                        model="responses/gpt55",
                    ),
                ],
            )

            result = _run_with_fake_docker(FakeM02Agent(), config, tmp_path)

            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("season_0:gpt-5.5", summary["agent"]["participant_id"])
            self.assertEqual("responses/gpt55", summary["model"])

    def test_interrupted_ranked_run_finalizes_without_judgement_or_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
            )

            with self.assertRaises(KeyboardInterrupt):
                _run_with_fake_docker(FakeInterruptedAgent(), config, tmp_path)

            run_dirs = sorted((tmp_path / "runs").glob("*"))
            self.assertEqual(1, len(run_dirs))
            run_dir = run_dirs[0]
            terminal = json.loads((run_dir / "terminal_state.json").read_text())
            summary = json.loads((run_dir / "run_summary.json").read_text())
            participant_state = json.loads(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "participant_state.json"
                ).read_text(encoding="utf-8")
            )

            self.assertEqual("failed", terminal["status"])
            self.assertEqual("run_interrupted", terminal["reason"])
            self.assertEqual("run", terminal["layer"])
            self.assertEqual("run_interrupted", summary["submission_outcome"])
            self.assertFalse(summary["ranking_eligible"])
            self.assertEqual("submission_run_interrupted", summary["ranking_exclusion_reason"])
            self.assertFalse((run_dir / "judgement.json").exists())
            self.assertFalse((run_dir / "replacement_state.json").exists())
            self.assertEqual(1, participant_state["runs_count"])
            self.assertEqual(1, participant_state["failures"])
            self.assertEqual(0, participant_state["active_runs"])
            self.assertEqual("failed", participant_state["last_run_status"])
            self.assertEqual("interrupted", participant_state["pending_run"]["status"])
            self.assertEqual("interrupted", participant_state["interrupted_run"]["status"])

    def test_manual_season_run_repairs_empty_persisted_season_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.model = "compatible/qwen36plus"
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:gpt-5.5"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(
                        id="season_0:gpt-5.5",
                        model="responses/gpt55",
                    ),
                ],
            )
            persisted = tmp_path / "seasons" / "season_0" / "season_config.yaml"
            persisted.parent.mkdir(parents=True)
            persisted.write_text("", encoding="utf-8")

            result = _run_with_fake_docker(FakeM02Agent(), config, tmp_path)

            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("responses/gpt55", summary["model"])
            self.assertIn("responses/gpt55", persisted.read_text(encoding="utf-8"))

    def test_empty_persisted_season_config_without_fallback_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SeasonStore(Path(tmp))
            path = store.config_path("season_0")
            path.parent.mkdir(parents=True)
            path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(Exception, "empty_season_config"):
                store.load("season_0")

    def test_season_admission_backfills_default_participant_id_for_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                name="Season 0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
            )

            result = _run_with_fake_docker(FakeM02Agent(), config, tmp_path)

            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "goal_state.json"
                ).exists()
            )
            summary = json.loads((result.run_dir / "run_summary.json").read_text())
            self.assertEqual("season_0:local-stub", summary["agent"]["participant_id"])

    def test_season_admission_rejects_inactive_season(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.season = SeasonConfig(
                id="season_0",
                status="observing",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
            )

            with self.assertRaisesRegex(Exception, "season_not_active"):
                Runner(agent=FakeM02Agent()).run(config, output_dir=tmp_path / "runs")

    def test_manual_season_run_rejects_participant_at_concurrency_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.run.wake_source = "manual"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                defaults={"wake_interval": "1h", "max_concurrent_runs": 1},
                participants=[SeasonParticipantConfig(model="local-stub")],
            )
            participant_dir = (
                tmp_path / "seasons" / "season_0" / "participants" / "season_0:local-stub"
            )
            participant_dir.mkdir(parents=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps({"active_runs": 1}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "participant_at_concurrency_limit"):
                Runner(agent=FakeM02Agent()).run(config, output_dir=tmp_path / "runs")
            run_dirs = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
            self.assertEqual(1, len(run_dirs))
            events = [
                json.loads(line)
                for line in (run_dirs[0] / "operator_events.jsonl").read_text().splitlines()
            ]
            self.assertEqual("run", events[0]["phase"])
            self.assertEqual("rejected", events[0]["status"])
            self.assertIn("participant_at_concurrency_limit", events[0]["payload"]["reason"])

    def test_auto_season_run_rejects_participant_at_concurrency_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _config(tmp_path / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.run.wake_source = "auto"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                defaults={"wake_interval": "1h", "max_concurrent_runs": 1},
                participants=[SeasonParticipantConfig(model="local-stub")],
            )
            participant_dir = (
                tmp_path / "seasons" / "season_0" / "participants" / "season_0:local-stub"
            )
            participant_dir.mkdir(parents=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps({"active_runs": 1}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "participant_at_concurrency_limit"):
                Runner(agent=FakeM02Agent()).run(config, output_dir=tmp_path / "runs")

    def test_season_identity_normalization(self) -> None:
        self.assertEqual("gpt-5.5", normalize_model_identity("responses/openai/GPT-5.5"))
        self.assertEqual(
            "season_0:gpt-5.5",
            derive_participant_id("season_0", "responses/openai/GPT-5.5"),
        )

    def test_participant_scoped_pr_history_filters_tracked_prs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base = _issue_config(tmp_path / "runs")
            base.memory = MemoryConfig(root=tmp_path / "memory")
            base.run.season_id = "season_0"
            base.run.participant_id = "season_0:local-stub"
            base.run.wake_source = "manual"
            base.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(model="local-stub"),
                    SeasonParticipantConfig(model="other-model"),
                ],
            )
            other = base.model_copy(
                update={
                    "run": base.run.model_copy(
                        update={
                            "model": "other-model",
                            "participant_id": "season_0:other-model",
                        }
                    )
                },
                deep=True,
            )
            save_governance_state(
                base,
                GovernanceState(
                    lifecycle_records=[
                        PrLifecycleRecord(
                            repository="example/repo",
                            number=17,
                            lifecycle_status="needs_response",
                        )
                    ]
                ),
            )
            save_governance_state(
                other,
                GovernanceState(
                    lifecycle_records=[
                        PrLifecycleRecord(
                            repository="example/repo",
                            number=99,
                            lifecycle_status="needs_response",
                        )
                    ]
                ),
            )
            agent = FakeMemoryAgent()

            _run_with_fake_docker(agent, base, tmp_path)

            self.assertEqual([17], [item["number"] for item in agent.memory_context["tracked_prs"]])
            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:local-stub"
                    / "pr_history.json"
                ).exists()
            )
            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:other-model"
                    / "pr_history.json"
                ).exists()
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
        memory=MemoryConfig(root=output_root.parent / "memory"),
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
            contribution_classes=ContributionClassesConfig(
                allowed=["tests", "low_risk_code"]
            ),
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


class _GuidanceWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, command: str) -> CommandResult:
        import subprocess

        completed = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return CommandResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=0.01,
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
    prepare_branch_failure_stderr: str = "",
    push_failure_stderr: str = "",
    push_transient_failures_before_success: int = 0,
    push_success_stdout: str = "",
    guidance_failure: bool = False,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    push_match = 'git -c http.version=HTTP/1.1 -C repo push contribarena-submit'
    prepare_branch_fetch_match = (
        "git -c http.version=HTTP/1.1 -C repo fetch --no-tags --depth 1 origin"
    )
    prepare_branch_failure_case = (
        f'    *"{prepare_branch_fetch_match}"*) '
        f'printf %s {json.dumps(prepare_branch_failure_stderr)} >&2; exit 1 ;;\n'
        if prepare_branch_failure_stderr
        else ""
    )
    push_failure_case = (
        f'    *"{push_match}"*) '
        f'printf %s {json.dumps(push_failure_stderr)} >&2; exit 1 ;;\n'
        if push_failure_stderr
        else ""
    )
    push_success_case = (
        f'    *"{push_match}"*) '
        f'printf %s {json.dumps(push_success_stdout)}; exit 0 ;;\n'
        if push_success_stdout
        else ""
    )
    transient_push_counter = tmp_path / "push_attempts"
    push_transient_case = (
        f'    *"{push_match}"*) '
        f'count="$(cat {transient_push_counter} 2>/dev/null || printf 0)"; '
        'next=$((count + 1)); '
        f'printf "%s" "$next" > {transient_push_counter}; '
        f'if [ "$next" -le {push_transient_failures_before_success} ]; then '
        'printf "fatal: unable to access github: GnuTLS recv error (-110)" >&2; exit 1; '
        'fi; '
        'printf "push ok"; exit 0 ;;\n'
        if push_transient_failures_before_success
        else ""
    )
    guidance_failure_case = (
        '    *".contribarena/guidance"*) '
        'printf "guidance write failed" >&2; exit 1 ;;\n'
        if guidance_failure
        else ""
    )
    docker.write_text(
        "#!/usr/bin/env sh\n"
        'args="$*"\n'
        'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
        'if [ "$1" = "inspect" ]; then exit 0; fi\n'
        'if [ "$1" = "start" ]; then exit 0; fi\n'
        'if [ "$1" = "rm" ]; then exit 0; fi\n'
        'if [ "$1" = "exec" ]; then\n'
        '  case "$args" in\n'
        f"{prepare_branch_failure_case}"
        '    *"git -C repo fetch --depth 1 origin"*) exit 0 ;;\n'
        '    *"git -C repo reset --hard FETCH_HEAD"*) exit 0 ;;\n'
        '    *"cat -- repo/app.py"*) printf "def marker():\\n    return \'old\'\\n"; exit 0 ;;\n'
        '    *"nl -ba repo/app.py"*) printf "     1\\tdef marker():\\n     2\\t    return \'old\'\\n"; exit 0 ;;\n'
        f"    *\"cat -- {diff_path}\"*) printf \"def marker():\\n    return 'old'\\n\"; exit 0 ;;\n"
        f"    *\"nl -ba {diff_path}\"*) printf \"     1\\tdef marker():\\n     2\\t    return 'old'\\n\"; exit 0 ;;\n"
        '    *"python3 -m compileall ."*) printf "compile ok\\n"; exit 0 ;;\n'
        '    *"missing_test.py"*"set -o pipefail"*) printf "pytest failed\\n"; exit 1 ;;\n'
        '    *"missing_test.py"*) printf "pytest failed\\n"; exit 0 ;;\n'
        f"{guidance_failure_case}"
        f"{push_failure_case}"
        f"{push_transient_case}"
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
