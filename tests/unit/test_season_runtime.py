from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    BackendConfig,
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    SeasonConfig,
    SeasonParticipantConfig,
    WorkspaceConfig,
)
from contribarena.engine.controller import ControllerTickResult
from contribarena.engine.season_runtime import SeasonRuntime
from contribarena.engine.seasons import SeasonStore


class SeasonRuntimeTests(unittest.TestCase):
    def test_start_activates_draft_and_records_bounded_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root, status="draft")
            controller = _ControllerStub()

            result = SeasonRuntime(controller=controller).start(config, max_heartbeats=1)

            self.assertEqual("ok", result.status)
            self.assertEqual(1, controller.calls)
            state = json.loads(
                (root / "seasons" / "season_0" / "season_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("active", state["status"])
            self.assertEqual("ok", state["heartbeat"]["last_status"])
            events = [event["event"] for event in state["runtime_events"]]
            self.assertIn("heartbeat_started", events)
            self.assertIn("heartbeat_completed", events)

    def test_paused_tick_does_not_wake_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root, status="active")
            SeasonStore.from_config(config).transition("season_0", "active", config.season)
            SeasonStore.from_config(config).set_paused("season_0", True, config.season)
            controller = _ControllerStub()

            result = SeasonRuntime(controller=controller).tick(config)

            self.assertEqual("ok", result.status)
            self.assertTrue(result.paused)
            self.assertEqual(0, controller.calls)


class _ControllerStub:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self, *args, **kwargs) -> ControllerTickResult:  # noqa: ANN002, ANN003
        self.calls += 1
        return ControllerTickResult(status="season_no_eligible_participant")

    def _run_external_lifecycle_tick(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


def _config(root: Path, *, status: str) -> RunConfig:
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
        workspace=WorkspaceConfig(),
        artifacts=ArtifactConfig(output_root=root / "runs"),
        backend=BackendConfig(read_model_path=root / "read.sqlite"),
        season=SeasonConfig(
            id="season_0",
            status=status,  # type: ignore[arg-type]
            state_root=root / "seasons",
            participants=[SeasonParticipantConfig(model="local-stub")],
        ),
    )
