from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contribarena.config.schema import (
    BotIdentityConfig,
    DiscoveryConfig,
    GovernanceConfig,
    MemoryConfig,
    OwnedRepositoryPolicy,
    PrSubmissionConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.budget import BudgetTracker
from contribarena.engine.goals import GoalService
from contribarena.engine.maintainer_review import run_maintainer_prereview
from contribarena.memory.history_index import HistoryIndex
from contribarena.memory.service import MemoryService
from contribarena.models import AciResult, CommandResult
from contribarena.tools.aci import AciExecution
from contribarena.tools.registry import ToolRegistry
from contribarena.trace import TraceWriter


class ToolRegistryMemoryTest(unittest.TestCase):
    def test_aci_memory_get_context_run_returns_guidance_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior_run = root / "prior"
            prior_run.mkdir()
            (prior_run / "repo_guidance.json").write_text(
                '{"summary": "Prior CONTRIBUTING guidance verification exists"}\n',
                encoding="utf-8",
            )
            HistoryIndex(root / "memory").index_run_dir(
                prior_run,
                run_id="prior-run",
                repo_full_name="example/repo",
            )
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            memory = MemoryService(
                config.memory,
                run_id="run-1",
                repo_full_name="example/repo",
            )
            goals = GoalService(config, run_id="run-1")
            memory.set_goal_context(goals.context)
            memory.start_run_context("example/repo")
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                memory=memory,
                goals=goals,
            )

            runtime = registry.aci_runtime_get_context("run")
            runtime_payload = json.loads(runtime.output)
            result = registry.aci_memory_get_context("run")
            payload = json.loads(result.output)

            self.assertTrue(runtime.success)
            self.assertEqual("shadow", runtime_payload["run_mode"])
            self.assertEqual("run-1", runtime_payload["run_id"])
            self.assertTrue(runtime_payload["guidance"]["available"])
            self.assertIn(
                "meaningful engineering contributions",
                runtime_payload["goals"]["long_term_objective"],
            )
            self.assertEqual("repo_context", runtime_payload["memory_hints"][0]["category"])
            self.assertTrue(result.success)
            self.assertTrue(payload["guidance"]["available"])
            self.assertEqual(
                ".contribarena/guidance/guidance_entry.md",
                payload["guidance"]["entry_path"],
            )
            self.assertEqual(
                ".contribarena/guidance/guidance_manifest.json",
                payload["guidance"]["manifest_path"],
            )
            self.assertEqual("workspace_root", payload["guidance"]["path_relative_to"])
            self.assertEqual("repo_context", payload["memory_hints"][0]["category"])
            self.assertEqual("repo_context", payload["memory_hints"][0]["suggested_intent"])
            self.assertIn("suggested_query", payload["memory_hints"][0])
            self.assertFalse(payload["memory_capabilities"]["repo_scope_persistent"])
            self.assertFalse(payload["memory_capabilities"]["global_scope_persistent"])
            self.assertIn("meaningful engineering contributions", payload["goals"]["long_term_objective"])
            self.assertEqual("aci_goal_update", payload["goals"]["update_tool"])

            update = registry.aci_goal_update("Submit a verified small code PR.", "active", "")
            complete_without_evidence = registry.aci_goal_update(status="complete")
            complete = registry.aci_goal_update(
                status="complete",
                evidence="Submitted patch and focused verification passed.",
                evidence_refs_json='["tool_call:aci_submit_patch:1"]',
            )

            self.assertTrue(update.success)
            self.assertFalse(complete_without_evidence.success)
            self.assertEqual("missing_goal_evidence", complete_without_evidence.recovery_kind)
            self.assertTrue(complete.success)
            completed_payload = json.loads(complete.output)
            self.assertEqual("complete", completed_payload["goals"]["short_term"]["status"])
            refreshed = json.loads(registry.aci_memory_get_context("run").output)
            self.assertEqual("complete", refreshed["goals"]["short_term"]["status"])
            refreshed_runtime = json.loads(registry.aci_runtime_get_context("run").output)
            self.assertEqual("complete", refreshed_runtime["goals"]["short_term"]["status"])

    def test_runtime_context_exposes_only_whitelisted_shared_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            season_root = root / "seasons"
            config = RunConfig(
                run=RunSection(
                    id="run-1",
                    mode="shadow",
                    season_id="season_0",
                    participant_id="season_0:qwen36plus",
                ),
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
                season=SeasonConfig(
                    id="season_0",
                    state_root=season_root,
                    participants=[
                        SeasonParticipantConfig(
                            id="season_0:qwen36plus",
                            model="compatible/qwen36plus",
                        )
                    ],
                ),
            )
            shared_dir = season_root / "season_0" / "shared"
            shared_dir.mkdir(parents=True)
            (shared_dir / "repository_guidance.json").write_text(
                json.dumps({"example/repo": {"verification": "run pytest"}}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            (shared_dir / "maintainer_signals.json").write_text(
                json.dumps([{"repository": "example/repo", "kind": "process_feedback"}], ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            other_participant = season_root / "season_0" / "participants" / "season_0:gpt55"
            other_participant.mkdir(parents=True)
            (other_participant / "participant_state.json").write_text(
                json.dumps({"secret_raw_history": "must not leak"}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                goals=GoalService(config, run_id="run-1"),
            )

            payload = json.loads(registry.aci_runtime_get_context("run").output)

            self.assertEqual(
                {"example/repo": {"verification": "run pytest"}},
                payload["shared_signals"]["repository_guidance"],
            )
            self.assertEqual(
                [{"repository": "example/repo", "kind": "process_feedback"}],
                payload["shared_signals"]["maintainer_signals"],
            )
            self.assertNotIn("secret_raw_history", json.dumps(payload, ensure_ascii=True))

    def test_aci_goal_update_third_abandon_marks_run_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            memory = MemoryService(config.memory, run_id="run-1", repo_full_name="example/repo")
            goals = GoalService(config, run_id="run-1")
            memory.set_goal_context(goals.context)
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                memory=memory,
                goals=goals,
            )

            first = _abandon_goal(registry, "Try task one.")
            second = _abandon_goal(registry, "Try task two.")
            third = _abandon_goal(registry, "Try task three.")
            fourth = registry.aci_goal_update("Try task four.", "active", "")

            self.assertIsNone(first.terminal_status)
            self.assertIsNone(second.terminal_status)
            self.assertEqual("goal_abandon_limit", third.terminal_status)
            self.assertEqual("goal_abandon_limit", fourth.terminal_status)
            self.assertEqual("goal_abandon_limit", fourth.recovery_kind)

    def test_goal_update_enforces_scope_switch_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            config.run.budget.work.max_repo_switches = 1
            config.run.budget.work.max_opportunity_switches = 1
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                goals=GoalService(config, run_id="run-1"),
            )

            first_repo_abandon = _abandon_goal(registry, "Try repo one.", scope="repo")
            second_repo_abandon = registry.aci_goal_update(
                "Try repo two.",
                "active",
                "",
                scope="repo",
            )
            first_opportunity_abandon = _abandon_goal(
                registry,
                "Try opportunity one.",
                scope="opportunity",
            )
            second_opportunity_abandon = registry.aci_goal_update(
                "Try opportunity two.",
                "active",
                "",
                scope="opportunity",
            )

            self.assertEqual("repo_abandoned", first_repo_abandon.terminal_status)
            self.assertEqual("repo_switch_limit", second_repo_abandon.terminal_status)
            self.assertEqual("opportunity_abandoned", first_opportunity_abandon.terminal_status)
            self.assertEqual(
                "opportunity_switch_limit",
                second_opportunity_abandon.terminal_status,
            )

    def test_phase_gate_blocks_editing_in_scout_and_records_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            capture = ArtifactCapture()
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=capture,
                goals=GoalService(config, run_id="run-1"),
            )

            blocked = registry.aci_apply_patch(
                [{"type": "update_file", "path": "repo/file.txt", "content": "new"}]
            )

            self.assertFalse(blocked.success)
            self.assertEqual("phase_violation", blocked.recovery_kind)
            self.assertEqual("aci_apply_patch", capture.tool_violations[0]["tool"])

    def test_phase_gate_allows_pr_tools_after_opportunity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            goals = GoalService(config, run_id="run-1")
            goals.update(objective="Find opportunities.", status="active", scope="opportunity")
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                goals=goals,
            )

            with patch("contribarena.tools.registry.repo_get_open_prs", return_value=[]):
                prs = registry.repo_get_open_prs(config.discovery.candidates[0])
            blocked_edit = registry.aci_apply_patch(
                [{"type": "update_file", "path": "repo/file.txt", "content": "new"}]
            )

            self.assertEqual([], prs)
            self.assertFalse(blocked_edit.success)
            self.assertEqual("phase_violation", blocked_edit.recovery_kind)

    def test_scout_budget_exhaustion_records_goal_event_and_phase_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            config.run.budget.scout.max_duplicate_checks = 1
            goals = GoalService(config, run_id="run-1")
            goals.update(objective="Find opportunities.", status="active", scope="opportunity")
            capture = ArtifactCapture()
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=capture,
                goals=goals,
            )

            with patch("contribarena.tools.registry.repo_get_open_prs", return_value=[]):
                allowed = registry.repo_get_open_prs(config.discovery.candidates[0])
                blocked = registry.repo_get_open_prs(config.discovery.candidates[0])

            self.assertEqual([], allowed)
            self.assertFalse(blocked.success)
            self.assertEqual("scout_budget_exhausted", blocked.recovery_kind)
            self.assertIn("scout_budget_exhausted", goals.events_text())
            self.assertEqual(
                "scout_budget_exhausted",
                capture.phase_scout_duplicate_rows[-1]["result_summary"].split(":")[0],
            )

    def test_review_round_limit_allows_finalize_and_dispute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            config.run.budget.review.max_review_rounds = 1
            goals = GoalService(
                config,
                run_id="run-1",
                evidence_ref_validator=lambda ref: ref == "tool_call:aci_submit_patch",
            )
            goals.update(
                objective="Implement a contribution.",
                status="active",
                scope="contribution",
            )
            capture = ArtifactCapture()
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=capture,
                goals=goals,
            )
            capture.record_aci_result(
                AciResult(tool="aci_apply_patch", success=True, files_modified=["repo/app.py"])
            )
            capture.record_aci_result(AciResult(tool="aci_verify", success=True))

            patch = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            with patch_registry_submit(patch):
                draft = registry.aci_submit_patch()
                first_review_resubmit = registry.aci_submit_patch()
                blocked_resubmit = registry.aci_submit_patch()
            blocked_review_edit = registry.aci_apply_patch(
                [{"type": "update_file", "path": "repo/app.py", "content": "new"}]
            )
            dispute = registry.aci_dispute_review(
                "concern-1",
                "The concern is addressed by the same focused diff and verification evidence.",
                '["tool_call:aci_submit_patch"]',
            )
            finalize = registry.aci_submit_patch_finalize()

            self.assertTrue(draft.success)
            self.assertEqual("review", goals.context.current_phase)
            self.assertTrue(first_review_resubmit.success)
            self.assertFalse(blocked_resubmit.success)
            self.assertEqual("review_round_limit", blocked_resubmit.recovery_kind)
            self.assertFalse(blocked_review_edit.success)
            self.assertEqual("review_round_limit", blocked_review_edit.recovery_kind)
            self.assertTrue(dispute.success)
            self.assertTrue(finalize.success)
            self.assertEqual(2, len(capture.phase_review_maintainer_rows))
            self.assertTrue(
                any(row.get("action") == "dispute" for row in capture.phase_review_response_rows)
            )

    def test_live_finalize_surfaces_required_pr_submission_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="owned_live"),
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
                memory=MemoryConfig(root=root / "memory"),
                governance=_test_live_governance(),
            )
            goals = GoalService(config, run_id="run-1")
            goals.update(
                objective="Implement a contribution.",
                status="active",
                scope="contribution",
            )
            capture = ArtifactCapture()
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=capture,
                goals=goals,
            )
            capture.record_aci_result(
                AciResult(tool="aci_apply_patch", success=True, files_modified=["repo/app.py"])
            )
            capture.record_aci_result(AciResult(tool="aci_verify", success=True))

            patch_text = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            with patch_registry_submit(patch_text):
                draft = registry.aci_submit_patch()
            finalize = registry.aci_submit_patch_finalize()
            payload = _last_json_object(finalize.output)

            self.assertTrue(draft.success)
            self.assertTrue(finalize.success)
            self.assertFalse(payload["run_complete"])
            self.assertIn("github_open_pr", payload["next_required_tools"])
            self.assertEqual(
                "github_open_pr status opened or existing",
                payload["completion_requires"],
            )
            self.assertIn("Live run is not complete yet", finalize.review_notes)

    def test_live_goal_complete_without_pr_warns_submission_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="owned_live"),
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
                memory=MemoryConfig(root=root / "memory"),
                governance=_test_live_governance(),
            )
            goals = GoalService(config, run_id="run-1")
            goals.update(
                objective="Implement a contribution.",
                status="active",
                scope="contribution",
            )
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=ArtifactCapture(),
                goals=goals,
            )

            result = registry.aci_goal_update(
                status="complete",
                evidence="Patch finalized but no governed PR has been opened.",
                evidence_refs_json='["tool_call:aci_submit_patch_finalize"]',
            )
            payload = json.loads(result.output)
            required = payload["live_submission_required"]

            self.assertTrue(result.success)
            self.assertFalse(required["run_complete"])
            self.assertIn("github_open_pr", required["next_required_tools"])
            self.assertIn("Live run is not complete yet", result.review_notes)

    def test_submit_patch_surfaces_maintainer_prereview_notes_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow"),
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
                memory=MemoryConfig(root=root / "memory"),
            )
            goals = GoalService(config, run_id="run-1")
            goals.update(
                objective="Implement a contribution.",
                status="active",
                scope="contribution",
            )
            capture = ArtifactCapture()
            registry = ToolRegistry(
                config=config,
                workspace=object(),  # type: ignore[arg-type]
                trace=TraceWriter(root / "trace.jsonl", "run-1"),
                budget=BudgetTracker(config.run.budget),
                capture=capture,
                goals=goals,
            )
            capture.record_aci_result(
                AciResult(tool="aci_apply_patch", success=True, files_modified=["repo/app.py"])
            )
            capture.record_aci_result(AciResult(tool="aci_verify", success=True))

            patch_text = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            review_row = {
                "schema_version": "1",
                "tool_call_id": "maintainer_prereview:1",
                "phase": "review",
                "status": "completed",
                "round": 1,
                "severity": "request_changes",
                "concerns": ["verification is too weak"],
                "suggested_changes": ["run a targeted test"],
                "summary": "Needs stronger verification before finalizing.",
            }
            with patch_registry_submit(patch_text), patch(
                "contribarena.tools.registry.run_maintainer_prereview",
                return_value=type("Review", (), {"row": review_row, "unavailable": False})(),
            ):
                draft = registry.aci_submit_patch()

            self.assertTrue(draft.success)
            self.assertEqual(patch_text, draft.output)
            self.assertIn("Maintainer pre-review", draft.review_notes)
            self.assertIn("verification is too weak", draft.review_notes)
            self.assertEqual(draft.review_notes, capture.aci_results[-1].review_notes)

    def test_maintainer_prereview_marks_self_review_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow", model="compatible/qwen36plus"),
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
                memory=MemoryConfig(root=root / "memory"),
            )

            review = run_maintainer_prereview(
                config=config,
                model_provider=None,
                capture=ArtifactCapture(),
                patch="diff --git a/app.py b/app.py\n",
                round_number=1,
            )

            self.assertEqual("self", review.row["review_mode"])
            self.assertEqual("unavailable", review.row["reviewer_model"])
            self.assertEqual("self_pre_submission_review", review.row["reviewer_role"])

    def test_maintainer_prereview_parses_provider_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(
                run=RunSection(id="run-1", mode="shadow", model="responses/test"),
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
                memory=MemoryConfig(root=root / "memory"),
            )

            class FakeAgent:
                def __init__(self, **kwargs):
                    pass

            class FakeModelSettings:
                def __init__(self, **kwargs):
                    pass

            class FakeRunConfig:
                def __init__(self, **kwargs):
                    pass

            class FakeRunner:
                @staticmethod
                def run_sync(*args, **kwargs):
                    class Result:
                        final_output = json.dumps(
                            {
                                "severity": "request_changes",
                                "concerns": ["verification is too weak"],
                                "suggested_changes": ["add focused verification"],
                                "summary": "Needs stronger evidence.",
                            }
                        )

                    return Result()

            with patch.dict(
                "sys.modules",
                {
                    "agents": type(
                        "AgentsModule",
                        (),
                        {
                            "Agent": FakeAgent,
                            "ModelSettings": FakeModelSettings,
                            "RunConfig": FakeRunConfig,
                            "Runner": FakeRunner,
                        },
                    )(),
                },
            ):
                review = run_maintainer_prereview(
                    config=config,
                    model_provider=object(),  # type: ignore[arg-type]
                    capture=ArtifactCapture(),
                    patch="diff --git a/app.py b/app.py\n",
                    round_number=1,
                )

            self.assertFalse(review.unavailable)
            self.assertEqual("completed", review.row["status"])
            self.assertEqual("request_changes", review.row["severity"])
            self.assertEqual(["verification is too weak"], review.row["concerns"])


def _abandon_goal(registry: ToolRegistry, objective: str, scope: str = ""):
    registry.aci_goal_update(objective, "active", "", scope=scope)
    return registry.aci_goal_update(
        "",
        "abandoned",
        f"{objective} is not suitable for this run.",
        scope=scope,
        evidence_refs_json='["tool_call:aci_view:1"]',
    )


def patch_registry_submit(patch_text: str):
    return patch(
        "contribarena.tools.registry.aci_submit_patch",
        return_value=AciExecution(
            result=AciResult(tool="aci_submit_patch", success=True, output=patch_text),
            commands=[
                CommandResult(
                    command="git diff",
                    stdout=patch_text,
                    exit_code=0,
                    duration_seconds=0.01,
                )
            ],
        ),
    )


def _last_json_object(output: str) -> dict[str, object]:
    start = output.rfind("{")
    if start == -1:
        raise AssertionError(f"output did not contain a JSON object: {output!r}")
    payload = json.loads(output[start:])
    if not isinstance(payload, dict):
        raise AssertionError(f"output JSON was not an object: {output!r}")
    return payload


def _test_live_governance() -> GovernanceConfig:
    return GovernanceConfig(
        live_enabled=True,
        owned_repositories=[
            OwnedRepositoryPolicy(
                owner="example",
                repo="repo",
                default_branch="main",
                pr_submission=PrSubmissionConfig(strategy="fork", fork_owner="contribarena-bot"),
            )
        ],
        bot_identity=BotIdentityConfig(kind="pat", actor="contribarena-bot"),
    )


if __name__ == "__main__":
    unittest.main()
