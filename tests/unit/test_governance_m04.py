from __future__ import annotations
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contribarena.config.schema import (
    ArtifactConfig,
    BotIdentityConfig,
    ControllerConfig,
    DiscoveryConfig,
    GovernanceConfig,
    GovernanceRateLimits,
    OwnedRepositoryPolicy,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.engine.controller import LocalController
from contribarena.engine import controller as controller_module
from contribarena.engine.external_lifecycle import lifecycle_record_for_opened_pr
from contribarena.engine.goals import GoalService
from contribarena.engine.judge_refresh import JudgeRefreshResult
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_pr,
    save_governance_state,
)
from contribarena.engine.seasons import mark_participant_run_finished
from contribarena.engine.runner import RunResult
from contribarena.models import GovernanceAttempt, GovernancePrRef, GovernanceState, QualityGateResult
from contribarena.models.governance import MaintainerSignal, PrLifecycleRecord
from contribarena.tools.github_pr import PullRequestStatusResult


class GovernanceM04Test(unittest.TestCase):
    def test_lifecycle_record_carries_season_identity(self) -> None:
        record = lifecycle_record_for_opened_pr(
            repository="example/repo",
            number=42,
            url="https://github.com/example/repo/pull/42",
            branch="contribarena/test",
            head="contribarena-bot:contribarena/test",
            base="main",
            head_sha="abc123",
            ci_status=None,
            poll_interval_seconds=3600,
            season_id="season_0",
            participant_id="season_0:local-stub",
        )

        self.assertEqual("season_0", record.season_id)
        self.assertEqual("season_0:local-stub", record.participant_id)

    def test_governance_blocks_when_live_disabled(self) -> None:
        config = _owned_config(live_enabled=False)
        decision = GovernanceMiddleware().evaluate_pr_open(
            config=config,
            quality_gate=QualityGateResult(status="pass"),
            target_owner="example",
            target_repo="repo",
            base_branch="main",
            contribution_class="low_risk_code",
        )

        self.assertEqual("block", decision.status)
        self.assertIn("governance live_enabled is false", decision.reasons)
        self.assertFalse(decision.external_write)

    def test_governance_passes_owned_repo_when_policy_and_state_allow(self) -> None:
        config = _owned_config(live_enabled=True)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            decision = GovernanceMiddleware().evaluate_pr_open(
                config=config,
                quality_gate=QualityGateResult(status="pass"),
                target_owner="example",
                target_repo="repo",
                base_branch="main",
                contribution_class="low_risk_code",
                actor="contribarena-bot",
            )

        self.assertEqual("pass", decision.status)
        self.assertEqual([], decision.reasons)
        self.assertTrue(decision.external_write)

    def test_governance_blocks_open_pr_limit(self) -> None:
        config = _owned_config(live_enabled=True)
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="example/repo", number=1, branch="contribarena/x")
            ]
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            decision = GovernanceMiddleware().evaluate_run_start(
                config=config,
                target_owner="example",
                target_repo="repo",
                state=state,
                actor="contribarena-bot",
            )

        self.assertEqual("block", decision.status)
        self.assertIn("open PR limit reached", "; ".join(decision.reasons))

    def test_controller_block_does_not_launch_run_and_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _owned_config(live_enabled=False, output_root=Path(tmp) / "runs")
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run(config)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, launcher.calls)
            state = load_governance_state(config)
            self.assertEqual(1, len(state.attempts))
            self.assertEqual("skipped", state.attempts[0].status)

    def test_controller_output_dir_controls_default_governance_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=False, output_root=tmp_path / "config-runs")
            output_dir = tmp_path / "cli-runs"
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run(config, output_dir=output_dir)

            self.assertEqual("blocked", result.status)
            self.assertTrue((output_dir / "governance_state.json").exists())
            self.assertFalse((config.artifacts.output_root / "governance_state.json").exists())

    def test_controller_disabled_does_not_evaluate_or_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _owned_config(
                live_enabled=True,
                output_root=Path(tmp) / "runs",
                controller_enabled=False,
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run(config)

            self.assertEqual("disabled", result.status)
            self.assertEqual(0, launcher.calls)
            self.assertFalse((config.artifacts.output_root / "governance_state.json").exists())

    def test_controller_pass_launches_one_run_and_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _owned_config(live_enabled=True, output_root=Path(tmp) / "runs")
            launcher = FakeLauncher()

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(launcher=launcher).run(config)

            self.assertEqual("completed", result.status)
            self.assertEqual(1, launcher.calls)
            state = load_governance_state(config)
            self.assertEqual(1, len(state.attempts))
            self.assertEqual("prepared", state.attempts[0].status)

    def test_controller_preserves_state_written_by_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=root / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=root / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
            )
            launcher = FakeLauncher(write_open_pr=True)

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(launcher=launcher).run(config)

            self.assertEqual("completed", result.status)
            state = load_governance_state(config)
            self.assertEqual(1, len(state.pull_requests))
            self.assertEqual("example/repo", state.pull_requests[0].repository)
            self.assertEqual("season_0", state.pull_requests[0].season_id)
            self.assertEqual("season_0:local-stub", state.pull_requests[0].participant_id)

    def test_external_live_governance_passes_without_owned_allowlist(self) -> None:
        config = _external_config(live_enabled=True)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            decision = GovernanceMiddleware().evaluate_pr_open(
                config=config,
                quality_gate=QualityGateResult(status="pass"),
                target_owner="external",
                target_repo="repo",
                base_branch="main",
                contribution_class="low_risk_code",
                actor="contribarena-bot",
                external_review_passed=True,
            )

        self.assertEqual("pass", decision.status)
        self.assertEqual("github.external_open_pr", decision.action)
        self.assertTrue(decision.external_write)

    def test_external_live_blocks_org_kill_switch_and_global_limit(self) -> None:
        config = _external_config(live_enabled=True)
        config.governance.kill_switches.organizations = ["external"]
        config.governance.rate_limits.max_open_prs_global = 1
        state = GovernanceState(
            pull_requests=[
                GovernancePrRef(repository="other/repo", number=1, branch="contribarena/x")
            ]
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            decision = GovernanceMiddleware().evaluate_pr_open(
                config=config,
                quality_gate=QualityGateResult(status="pass"),
                target_owner="external",
                target_repo="repo",
                base_branch="main",
                contribution_class="low_risk_code",
                state=state,
                actor="contribarena-bot",
            )

        reasons = "; ".join(decision.reasons)
        self.assertEqual("block", decision.status)
        self.assertIn("organization kill switch is active: external", reasons)
        self.assertIn("global open PR limit reached", reasons)

    def test_external_live_high_severity_maintainer_signal_blocks(self) -> None:
        config = _external_config(live_enabled=True)
        state = GovernanceState(
            maintainer_signals=[
                MaintainerSignal(
                    repository="external/repo",
                    organization="external",
                    kind="opt_out",
                    severity="high",
                    message="Please do not send automated PRs.",
                )
            ]
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            decision = GovernanceMiddleware().evaluate_pr_open(
                config=config,
                quality_gate=QualityGateResult(status="pass"),
                target_owner="external",
                target_repo="repo",
                base_branch="main",
                contribution_class="low_risk_code",
                state=state,
                actor="contribarena-bot",
            )

        self.assertEqual("block", decision.status)
        self.assertIn("high-severity maintainer signal", "; ".join(decision.reasons))

    def test_external_lifecycle_tick_updates_due_pr_without_launching_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            (run_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "url": "https://github.com/external/repo/pull/7",
                            "number": 7,
                            "state": "open",
                        },
                        "maintainer_outcome": {
                            "status": "pending",
                            "observed_at": "",
                            "source": "none",
                        },
                        "judgement": {
                            "judge_score": 70,
                            "real_world_adjustment": 0,
                            "arena_score": 70,
                        },
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            state = GovernanceState(
                pull_requests=[
                    GovernancePrRef(
                        repository="external/repo",
                        number=7,
                        url="https://github.com/external/repo/pull/7",
                        branch="contribarena/test",
                    )
                ],
                lifecycle_records=[
                    PrLifecycleRecord(
                        repository="external/repo",
                        number=7,
                        url="https://github.com/external/repo/pull/7",
                        originating_run_dir=str(run_dir),
                        branch="contribarena/test",
                        head_sha="abc123",
                        next_poll_at="2000-01-01T00:00:00+00:00",
                    )
                ]
            )
            save_governance_state(config, state)
            launcher = FakeLauncher()

            result = LocalController(
                launcher=launcher,
                pr_client=FakeLifecycleClient(merged=True),
            ).run_once(config)

            self.assertEqual("lifecycle_terminal", result.status)
            self.assertEqual(0, launcher.calls)
            updated = load_governance_state(config).lifecycle_records[0]
            self.assertEqual("merged", updated.lifecycle_status)
            self.assertEqual("merged", updated.state)
            self.assertEqual(str(run_dir), updated.originating_run_dir)
            updated_state = load_governance_state(config)
            self.assertEqual("merged", updated_state.pull_requests[0].state)
            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                follow_up = GovernanceMiddleware().evaluate_pr_open(
                    config=config,
                    quality_gate=QualityGateResult(status="pass"),
                    target_owner="external",
                    target_repo="repo",
                    base_branch="main",
                    contribution_class="low_risk_code",
                    state=updated_state,
                    actor="contribarena-bot",
                    external_review_passed=True,
                )
            self.assertEqual("pass", follow_up.status)
            review_log = (run_dir / "pr_review_log.jsonl").read_text()
            self.assertIn('"event": "lifecycle_observed"', review_log)
            self.assertIn('"lifecycle_status": "merged"', review_log)
            memory_events = (run_dir / "memory_events.jsonl").read_text()
            self.assertIn("lifecycle_observed", memory_events)
            self.assertFalse((run_dir / "resume_context.json").exists())
            self.assertFalse((config.artifacts.output_root / "pr_review_log.jsonl").exists())
            summary = json.loads((run_dir / "run_summary.json").read_text())
            self.assertEqual("merged", summary["pull_request"]["state"])
            self.assertEqual("merged", summary["maintainer_outcome"]["status"])
            self.assertGreater(summary["judgement"]["arena_score"], 70)

    def test_season_tick_observes_participant_owned_pr_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "owned-run"
            run_dir.mkdir(parents=True)
            (run_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "season": {"id": "season_0"},
                        "agent": {"participant_id": "season_0:local-stub"},
                        "repository": {"full_name": "example/repo"},
                        "pull_request": {
                            "url": "https://github.com/example/repo/pull/42",
                            "number": 42,
                            "state": "open",
                        },
                        "maintainer_outcome": {
                            "status": "pending",
                            "observed_at": "",
                            "source": "none",
                        },
                        "judgement": {
                            "judge_score": 70,
                            "real_world_adjustment": 0,
                            "arena_score": 70,
                        },
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            config = _owned_config(live_enabled=True, output_root=root / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=root / "seasons",
                defaults={"wake_interval": "6h", "max_concurrent_runs": 1},
                participants=[SeasonParticipantConfig(model="local-stub")],
            )
            participant_id = "season_0:local-stub"
            participant_config = config.model_copy(
                update={
                    "run": config.run.model_copy(
                        update={
                            "season_id": "season_0",
                            "participant_id": participant_id,
                        }
                    )
                },
                deep=True,
            )
            save_governance_state(
                participant_config,
                GovernanceState(
                    pull_requests=[
                        GovernancePrRef(
                            season_id="season_0",
                            participant_id=participant_id,
                            repository="example/repo",
                            number=42,
                            url="https://github.com/example/repo/pull/42",
                            branch="contribarena/test",
                        )
                    ]
                ),
            )
            participant_dir = root / "seasons" / "season_0" / "participants" / participant_id
            participant_dir.mkdir(parents=True, exist_ok=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps({"active_runs": 1}) + "\n",
                encoding="utf-8",
            )

            result = LocalController(
                launcher=FakeLauncher(),
                pr_client=FakeLifecycleClient(merged=True),
            ).run_once(config)

            self.assertEqual("lifecycle_terminal", result.status)
            state = load_governance_state(participant_config)
            self.assertEqual("merged", state.pull_requests[0].state)
            self.assertEqual(1, len(state.lifecycle_records))
            self.assertEqual("merged", state.lifecycle_records[0].state)
            self.assertEqual(participant_id, state.lifecycle_records[0].participant_id)
            self.assertEqual(str(run_dir), state.lifecycle_records[0].originating_run_dir)
            participant_state = json.loads((participant_dir / "participant_state.json").read_text())
            self.assertEqual(1, participant_state["prs_opened"])
            self.assertEqual(1, participant_state["merged_prs"])
            summary = json.loads((run_dir / "run_summary.json").read_text())
            self.assertEqual("merged", summary["pull_request"]["state"])
            self.assertEqual("merged", summary["maintainer_outcome"]["status"])
            self.assertGreater(summary["judgement"]["arena_score"], 70)

    def test_completed_season_records_post_completion_outcome_without_rewriting_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            config = _external_config(live_enabled=True, output_root=root / "runs")
            config.run.season_id = "season_0"
            config.run.participant_id = "season_0:local-stub"
            config.season = SeasonConfig(
                id="season_0",
                status="completed",
                state_root=root / "seasons",
                participants=[SeasonParticipantConfig(model="local-stub")],
            )
            season_dir = root / "seasons" / "season_0"
            season_dir.mkdir(parents=True)
            snapshot_path = season_dir / "leaderboard_snapshot.json"
            snapshot_payload = {
                "schema_version": "1",
                "season_id": "season_0",
                "leaderboard": [{"participant_id": "season_0:local-stub", "mean_arena_score": 80}],
            }
            snapshot_path.write_text(
                json.dumps(snapshot_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            original_snapshot = snapshot_path.read_text(encoding="utf-8")
            state = GovernanceState(
                lifecycle_records=[
                    PrLifecycleRecord(
                        repository="external/repo",
                        number=7,
                        url="https://github.com/external/repo/pull/7",
                        originating_run_dir=str(run_dir),
                        branch="contribarena/test",
                        head_sha="abc123",
                        next_poll_at="2000-01-01T00:00:00+00:00",
                    )
                ]
            )
            save_governance_state(config, state)

            result = LocalController(
                launcher=FakeLauncher(),
                pr_client=FakeLifecycleClient(merged=True),
            ).run_once(config)

            self.assertEqual("lifecycle_terminal", result.status)
            self.assertEqual(original_snapshot, snapshot_path.read_text(encoding="utf-8"))
            outcomes = (season_dir / "post_completion_outcomes.jsonl").read_text(encoding="utf-8")
            self.assertIn('"repository": "external/repo"', outcomes)
            self.assertIn('"lifecycle_status": "merged"', outcomes)

    def test_external_lifecycle_tick_allows_active_goal_continuation_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            GoalService(config, run_id="seed").update(
                objective="Continue the current PR until it is resolved.",
                status="active",
            )
            state = GovernanceState(
                lifecycle_records=[
                    PrLifecycleRecord(
                        repository="external/repo",
                        number=7,
                        url="https://github.com/external/repo/pull/7",
                        originating_run_dir=str(run_dir),
                        branch="contribarena/test",
                        head_sha="abc123",
                        next_poll_at="2000-01-01T00:00:00+00:00",
                    )
                ]
            )
            save_governance_state(config, state)
            launcher = FakeLauncher()

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(
                    launcher=launcher,
                    pr_client=FakeLifecycleClient(actor="contribarena-bot"),
                ).run_once(config)

            self.assertEqual("run_completed", result.status)
            self.assertEqual(1, launcher.calls)
            review_log = (run_dir / "pr_review_log.jsonl").read_text()
            self.assertIn('"event": "lifecycle_observed"', review_log)

    def test_external_lifecycle_tick_backs_off_transient_observe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "fake-run"
            run_dir.mkdir(parents=True)
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            config.governance.external_live.poll_interval_seconds = 60
            state = GovernanceState(
                lifecycle_records=[
                    PrLifecycleRecord(
                        repository="external/repo",
                        number=7,
                        url="https://github.com/external/repo/pull/7",
                        originating_run_dir=str(run_dir),
                        branch="contribarena/test",
                        head_sha="abc123",
                        next_poll_at="2000-01-01T00:00:00+00:00",
                    )
                ]
            )
            save_governance_state(config, state)
            launcher = FakeLauncher()

            result = LocalController(
                launcher=launcher,
                pr_client=FakeLifecycleClient(error=RuntimeError("HTTP 503 unavailable")),
            ).run_once(config)

            self.assertEqual("lifecycle_tracked", result.status)
            self.assertEqual(0, launcher.calls)
            updated = load_governance_state(config).lifecycle_records[0]
            self.assertEqual("tracking", updated.lifecycle_status)
            self.assertEqual(1, updated.lifecycle_retry_count)
            self.assertNotEqual("2000-01-01T00:00:00+00:00", updated.next_poll_at)
            review_log = (run_dir / "pr_review_log.jsonl").read_text()
            self.assertIn('"event": "lifecycle_observe_failed"', review_log)
            self.assertIn('"retry_count": 1', review_log)

    def test_external_live_controller_preflight_blocks_without_launching_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            config.governance.kill_switches.global_switch = True
            launcher = FakeLauncher()

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(
                    launcher=launcher,
                    pr_client=FakeLifecycleClient(actor="contribarena-bot"),
                ).run_once(config)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, launcher.calls)
            self.assertIsNotNone(result.decision)
            self.assertIn("global kill switch is active", result.decision.reasons)

    def test_external_live_controller_preflight_blocks_missing_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            launcher = FakeLauncher()

            with patch.dict(os.environ, {}, clear=True):
                result = LocalController(
                    launcher=launcher,
                    pr_client=FakeLifecycleClient(actor="contribarena-bot"),
                ).run_once(config)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, launcher.calls)
            self.assertIn("bot token env is missing: GITHUB_TOKEN", result.decision.reasons)

    def test_external_live_controller_preflight_blocks_daily_global_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            config.governance.rate_limits.max_prs_global_per_day = 1
            save_governance_state(
                config,
                GovernanceState(
                    attempts=[
                        GovernanceAttempt(
                            repository="other/repo",
                            action="github.external_open_pr",
                            status="opened",
                        )
                    ]
                ),
            )
            launcher = FakeLauncher()

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(
                    launcher=launcher,
                    pr_client=FakeLifecycleClient(actor="contribarena-bot"),
                ).run_once(config)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, launcher.calls)
            self.assertIn("global daily PR limit reached", "; ".join(result.decision.reasons))

    def test_external_live_controller_preflight_blocks_actor_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _external_config(live_enabled=True, output_root=Path(tmp) / "runs")
            launcher = FakeLauncher()

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(
                    launcher=launcher,
                    pr_client=FakeLifecycleClient(actor="someone-else"),
                ).run_once(config)

            self.assertEqual("blocked", result.status)
            self.assertEqual(0, launcher.calls)
            self.assertIn(
                "authenticated actor someone-else does not match expected contribarena-bot",
                result.decision.reasons,
            )

    def test_active_season_auto_wakes_agent_participant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(model="compatible/qwen36plus"),
                    SeasonParticipantConfig(model="responses/gpt55", role=["judge"]),
                ],
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("run_completed", result.status)
            self.assertEqual(1, launcher.calls)
            launched = launcher.configs[0]
            self.assertEqual("compatible/qwen36plus", launched.run.model)
            self.assertEqual("season_0", launched.run.season_id)
            self.assertEqual("season_0:qwen36plus", launched.run.participant_id)
            self.assertEqual("auto", launched.run.wake_source)
            state = load_governance_state(launched)
            self.assertEqual(1, len(state.attempts))
            self.assertEqual("prepared", state.attempts[0].status)
            self.assertEqual("season.auto_wake", state.attempts[0].action)
            self.assertEqual(
                "participant=season_0:qwen36plus;wake_dispatched",
                state.attempts[0].decision_id,
            )
            self.assertEqual("example/repo", state.attempts[0].repository)
            self.assertTrue(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:qwen36plus"
                    / "pr_history.json"
                ).exists()
            )
            participant_state = json.loads(
                (
                    tmp_path
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:qwen36plus"
                    / "participant_state.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("example/repo", participant_state["last_repo_slug"])
            self.assertEqual("auto", participant_state["last_wake_source"])

    def test_active_season_dispatches_one_due_agent_per_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(model="compatible/qwen36plus"),
                    SeasonParticipantConfig(model="responses/gpt55"),
                    SeasonParticipantConfig(model="anthropic/claudeopus47", role=["judge"]),
                ],
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("run_completed", result.status)
            self.assertEqual(1, launcher.calls)
            self.assertEqual(
                ["season_0:qwen36plus"],
                [item.run.participant_id for item in launcher.configs],
            )
            self.assertEqual(["compatible/qwen36plus"], [item.run.model for item in launcher.configs])
            for launched in launcher.configs:
                state = load_governance_state(launched)
                self.assertEqual(1, len(state.attempts))
                self.assertEqual("prepared", state.attempts[0].status)

    def test_active_season_replacement_due_participant_runs_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(model="compatible/qwen36plus"),
                    SeasonParticipantConfig(model="responses/gpt55"),
                ],
            )
            participant_dir = tmp_path / "seasons" / "season_0" / "participants" / "season_0:gpt55"
            participant_dir.mkdir(parents=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps(
                    {
                        "replacement": {
                            "status": "due",
                            "source_run_id": "failed-run",
                            "reason": "model_runtime",
                            "layer": "model_runtime",
                        },
                        "replacement_due": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("run_completed", result.status)
            self.assertEqual(1, launcher.calls)
            launched = launcher.configs[0]
            self.assertEqual("season_0:gpt55", launched.run.participant_id)
            state = json.loads((participant_dir / "participant_state.json").read_text(encoding="utf-8"))
            self.assertEqual("replaced", state["replacement"]["status"])
            self.assertEqual("fake", state["replacement"]["replacement_run_id"])
            self.assertEqual("fake", state["replacement"]["completed_run_id"])

    def test_active_season_refreshes_due_judgement_before_waking_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[SeasonParticipantConfig(model="compatible/qwen36plus")],
            )
            calls: list[str] = []

            def fake_refresh_due_judgements(**kwargs: object) -> object:
                calls.append(str(kwargs.get("season_id")))
                return JudgeRefreshResult(runs_judged=1, skipped=[])

            launcher = FakeLauncher()
            with patch.object(
                controller_module,
                "refresh_due_judgements",
                fake_refresh_due_judgements,
            ):
                result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("judgement_refreshed", result.status)
            self.assertEqual(["season_0"], calls)
            self.assertEqual(0, launcher.calls)

    def test_active_season_without_agent_participant_does_not_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                participants=[
                    SeasonParticipantConfig(model="responses/gpt55", role=["judge"]),
                ],
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("season_no_eligible_participant", result.status)
            self.assertEqual(0, launcher.calls)

    def test_active_season_skips_participant_until_wake_interval_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                defaults={"wake_interval": "6h", "max_concurrent_runs": 1},
                participants=[SeasonParticipantConfig(model="compatible/qwen36plus")],
            )
            participant_dir = tmp_path / "seasons" / "season_0" / "participants" / "season_0:qwen36plus"
            participant_dir.mkdir(parents=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps({"last_wake_at": "2999-01-01T00:00:00+00:00"}) + "\n",
                encoding="utf-8",
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("season_no_eligible_participant", result.status)
            self.assertEqual(0, launcher.calls)
            state = load_governance_state(config)
            self.assertEqual("skipped", state.attempts[0].status)
            self.assertIn("wake_interval_not_elapsed", state.attempts[0].decision_id)

    def test_active_season_skips_participant_at_concurrency_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = _owned_config(live_enabled=True, output_root=tmp_path / "runs")
            config.season = SeasonConfig(
                id="season_0",
                status="active",
                state_root=tmp_path / "seasons",
                defaults={"wake_interval": "1s", "max_concurrent_runs": 1},
                participants=[SeasonParticipantConfig(model="compatible/qwen36plus")],
            )
            participant_dir = tmp_path / "seasons" / "season_0" / "participants" / "season_0:qwen36plus"
            participant_dir.mkdir(parents=True)
            (participant_dir / "participant_state.json").write_text(
                json.dumps({"active_runs": 1}) + "\n",
                encoding="utf-8",
            )
            launcher = FakeLauncher()

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("season_no_eligible_participant", result.status)
            self.assertEqual(0, launcher.calls)
            state = load_governance_state(config)
            self.assertEqual("skipped", state.attempts[0].status)
            self.assertIn("participant_at_concurrency_limit", state.attempts[0].decision_id)


class FakeLauncher:
    def __init__(self, write_open_pr: bool = False) -> None:
        self.calls = 0
        self.write_open_pr = write_open_pr
        self.configs: list[RunConfig] = []

    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> RunResult:
        self.calls += 1
        self.configs.append(config)
        if self.write_open_pr:
            state = load_governance_state(config)
            record_governance_pr(
                state,
                repository="example/repo",
                number=42,
                url="https://github.com/example/repo/pull/42",
                branch="contribarena/test",
                season_id=config.run.season_id or "",
                participant_id=config.run.participant_id or "",
            )
            save_governance_state(config, state)
        mark_participant_run_finished(
            config,
            run_id="fake",
            status="completed",
            repo_slug="example/repo",
        )
        return RunResult(
            run_id="fake",
            run_dir=Path("runs/fake"),
            status="completed",
            tool_calls=0,
            terminal_reason="run_completed",
            terminal_layer="run",
        )


def _owned_config(
    *,
    live_enabled: bool,
    output_root: Path | None = None,
    controller_enabled: bool = True,
) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="owned_live", model="local-stub"),
        discovery=DiscoveryConfig(
            candidates=[
                RepoCandidate(
                    owner="example",
                    repo="repo",
                    url="https://github.com/example/repo",
                )
            ]
        ),
        controller=ControllerConfig(enabled=controller_enabled),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=output_root or Path("runs")),
        governance=GovernanceConfig(
            live_enabled=live_enabled,
            owned_repositories=[
                OwnedRepositoryPolicy(owner="example", repo="repo", default_branch="main")
            ],
            bot_identity=BotIdentityConfig(kind="pat", actor="contribarena-bot"),
            rate_limits=GovernanceRateLimits(
                max_open_prs_per_repo=1,
                max_prs_per_repo_per_day=3,
                min_minutes_between_prs_per_repo=0,
            ),
        ),
    )


def _external_config(
    *,
    live_enabled: bool,
    output_root: Path | None = None,
) -> RunConfig:
    return RunConfig(
        run=RunSection(mode="external_live", model="local-stub"),
        discovery=DiscoveryConfig(query="language:Python low risk"),
        controller=ControllerConfig(enabled=True),
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=output_root or Path("runs")),
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


class FakeLifecycleClient:
    def __init__(
        self,
        merged: bool = False,
        actor: str = "",
        error: Exception | None = None,
    ) -> None:
        self.merged = merged
        self.actor = actor
        self.error = error

    def authenticated_actor(self) -> str:
        return self.actor

    def get_pr(self, *, owner: str, repo: str, number: int) -> PullRequestStatusResult:
        if self.error is not None:
            raise self.error
        return PullRequestStatusResult(
            ok=True,
            number=number,
            state="closed" if self.merged else "open",
            merged=self.merged,
            url=f"https://github.com/{owner}/{repo}/pull/{number}",
            head_sha="def456",
            head_ref="contribarena/test",
            base_ref="main",
            source="fake",
        )

    def get_check_runs(self, *, owner: str, repo: str, ref: str) -> object:
        from contribarena.models import CiCheck, CiStatus

        return CiStatus(
            status="success",
            source="github",
            checks=[CiCheck(name=f"{owner}/{repo}:{ref}", status="success")],
        )

    def list_reviews(self, *, owner: str, repo: str, number: int) -> list[object]:
        return []


if __name__ == "__main__":
    unittest.main()
