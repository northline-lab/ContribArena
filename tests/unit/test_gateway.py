from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contribarena.engine.gateway import (
    DoctorResult,
    GatewayCommandResult,
    GatewayPaths,
    _participant_ranking_state,
    _doctor_runtime_state,
    _run_log_path,
    _write_gateway_state,
    load_gateway_state,
    resolve_gateway_paths,
    restart_gateway,
    start_gateway,
    stop_gateway,
)
from contribarena.config import load_run_config
from contribarena.config.schema import ArtifactConfig, DiscoveryConfig, RepoCandidate, RunConfig, RunSection, WorkspaceConfig
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.errors import ContribArenaError


class GatewayLifecycleTests(unittest.TestCase):
    def test_restart_does_not_start_when_stop_times_out(self) -> None:
        paths = _paths(Path("/tmp/contribarena-test/config.yaml"))
        stopping = GatewayCommandResult("stopping", "still stopping", paths, pid=123)

        with (
            patch("contribarena.engine.gateway.stop_gateway", return_value=stopping),
            patch("contribarena.engine.gateway.start_gateway") as start,
        ):
            with self.assertRaisesRegex(ContribArenaError, "still stopping"):
                restart_gateway(config_path=paths.config_path)

        start.assert_not_called()

    def test_stop_stale_gateway_cleans_recorded_api_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            config = load_run_config(config_path)
            paths = resolve_gateway_paths(config_path, config)
            paths.control_root.mkdir(parents=True)
            paths.log_root.mkdir(parents=True)
            paths.pid_path.write_text("111\n", encoding="utf-8")
            paths.state_path.write_text(
                json.dumps({"status": "running", "pid": 111, "api_pid": 222}),
                encoding="utf-8",
            )

            with (
                patch("contribarena.engine.gateway._pid_alive", return_value=False),
                patch("contribarena.engine.gateway._terminate_pid", return_value=True) as terminate,
            ):
                result = stop_gateway(config_path=config_path)

            self.assertEqual("stale_cleaned", result.status)
            terminate.assert_called_once_with(222, timeout_seconds=5)
            self.assertFalse(paths.pid_path.exists())
            self.assertIsNone(load_gateway_state(paths).get("api_pid"))

    def test_state_write_is_atomic_json_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp) / "config.yaml")

            _write_gateway_state(paths, {"status": "starting", "pid": 123})
            _write_gateway_state(paths, {"status": "sleeping", "heartbeat_count": 2})

            state = load_gateway_state(paths)
            self.assertEqual("sleeping", state["status"])
            self.assertEqual(123, state["pid"])
            self.assertEqual(2, state["heartbeat_count"])
            self.assertEqual([], list(paths.state_path.parent.glob("gateway_state.json.*.tmp")))

    def test_load_gateway_state_tolerates_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp) / "config.yaml")
            paths.state_path.parent.mkdir(parents=True)
            paths.state_path.write_text("{not json", encoding="utf-8")

            self.assertEqual({}, load_gateway_state(paths))

    def test_start_gateway_runs_doctor_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))

            class FakeProcess:
                pid = 4321

            with (
                patch("contribarena.engine.gateway._pid_alive", return_value=False),
                patch("contribarena.engine.gateway.run_doctor", return_value=DoctorResult([])) as doctor,
                patch("contribarena.engine.gateway.subprocess.Popen", return_value=FakeProcess()),
            ):
                result = start_gateway(config_path=config_path)

            self.assertEqual("started", result.status)
            self.assertEqual(4321, result.pid)
        self.assertEqual(False, doctor.call_args.kwargs["repair"])

    def test_participant_ranking_state_matches_judgement_retry_exclusion_statuses(self) -> None:
        self.assertEqual(
            "excluded:judgement_retry_due",
            _participant_ranking_state("WAITING", {}, {"status": "due"}),
        )
        self.assertEqual(
            "excluded:judgement_retry_running",
            _participant_ranking_state("WAITING", {}, {"status": "running"}),
        )
        self.assertEqual(
            "none",
            _participant_ranking_state("WAITING", {}, {"status": "failed"}),
        )

    def test_doctor_exhausted_work_warns_without_blocking_startup(self) -> None:
        checks = []
        snapshot = {
            "participants": [
                {
                    "display_name": "mimov25pro",
                    "state": "EXHAUSTED",
                    "replacement": {
                        "status": "exhausted",
                        "attempts": 6,
                        "max_attempts": 5,
                    },
                }
            ]
        }

        with patch("contribarena.engine.gateway._season_status", return_value=snapshot):
            _doctor_runtime_state(checks, _minimal_config(), "season_0")

        exhausted = next(check for check in checks if check.name == "exhausted_work")
        self.assertEqual("warning", exhausted.status)
        self.assertEqual([], DoctorResult(checks).failed)

    def test_run_log_path_prefers_read_model_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _paths(root / "config.yaml")
            indexed_run = root / "outside-artifact-root" / "run-a"
            indexed_run.mkdir(parents=True)
            (indexed_run / "operator_events.jsonl").write_text("indexed\n", encoding="utf-8")
            (indexed_run / "run_summary.json").write_text(
                json.dumps(_run_summary_payload("run-a", indexed_run), ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            paths.artifact_root.mkdir(parents=True)

            SurfaceReadModel(paths.read_model_path).refresh_from_artifacts(indexed_run.parent)

            self.assertEqual(indexed_run / "operator_events.jsonl", _run_log_path(paths, "run-a"))


def _paths(config_path: Path) -> GatewayPaths:
    root = config_path.parent
    return GatewayPaths(
        config_path=config_path,
        artifact_root=root / "runs",
        read_model_path=root / "read_model.sqlite",
        control_root=root / "control",
        log_root=root / "logs",
        pid_path=root / "control" / "gateway.pid",
        state_path=root / "control" / "gateway_state.json",
        events_path=root / "control" / "gateway_events.jsonl",
        gateway_log_path=root / "logs" / "gateway.log",
        season_log_path=root / "logs" / "season.log",
        api_log_path=root / "logs" / "api.log",
    )


def _write_config(root: Path) -> Path:
    config_path = root / "config.json"
    payload = {
        "run": {"mode": "shadow", "model": "local-stub"},
        "discovery": {
            "candidates": [
                {
                    "owner": "example",
                    "repo": "repo",
                    "url": "https://github.com/example/repo",
                    "branch": "main",
                }
            ]
        },
        "workspace": {"backend": "docker", "image": "contribarena/workspace:latest"},
        "artifacts": {"output_root": str(root / "runs")},
        "backend": {"read_model_path": str(root / "read_model.sqlite")},
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def _minimal_config() -> RunConfig:
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
        artifacts=ArtifactConfig(output_root=Path("runs")),
    )


def _run_summary_payload(run_id: str, run_dir: Path) -> dict[str, object]:
    return {
        "schema_version": "1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "season": {"id": "season_0", "name": "Season 0", "phase": "active"},
        "agent": {"name": "agent-a", "handle": "agent-a", "participant_id": "season_0:agent-a"},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "started_at": "2026-05-15T00:00:00Z",
        "completed_at": "2026-05-15T00:01:00Z",
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "run_completed",
        "terminal_layer": "run",
        "judgement": {"status": "not_judged"},
        "artifacts": [],
    }


if __name__ == "__main__":
    unittest.main()
