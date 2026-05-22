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
    _write_gateway_state,
    load_gateway_state,
    resolve_gateway_paths,
    restart_gateway,
    start_gateway,
    stop_gateway,
)
from contribarena.config import load_run_config
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


if __name__ == "__main__":
    unittest.main()
