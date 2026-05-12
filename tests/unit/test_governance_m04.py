from __future__ import annotations

import os
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
    WorkspaceConfig,
)
from contribarena.engine.controller import LocalController
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_pr,
    save_governance_state,
)
from contribarena.engine.runner import RunResult
from contribarena.models import GovernancePrRef, GovernanceState, QualityGateResult


class GovernanceM04Test(unittest.TestCase):
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
            config = _owned_config(live_enabled=True, output_root=Path(tmp) / "runs")
            launcher = FakeLauncher(write_open_pr=True)

            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
                result = LocalController(launcher=launcher).run(config)

            self.assertEqual("completed", result.status)
            state = load_governance_state(config)
            self.assertEqual(1, len(state.pull_requests))
            self.assertEqual("example/repo", state.pull_requests[0].repository)


class FakeLauncher:
    def __init__(self, write_open_pr: bool = False) -> None:
        self.calls = 0
        self.write_open_pr = write_open_pr

    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> RunResult:
        self.calls += 1
        if self.write_open_pr:
            state = load_governance_state(config)
            record_governance_pr(
                state,
                repository="example/repo",
                number=42,
                url="https://github.com/example/repo/pull/42",
                branch="contribarena/test",
            )
            save_governance_state(config, state)
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


if __name__ == "__main__":
    unittest.main()
