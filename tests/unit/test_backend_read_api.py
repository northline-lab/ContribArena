from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.api import create_app
from contribarena.engine.read_model import SurfaceReadModel


class BackendReadApiTests(unittest.TestCase):
    def test_read_model_indexes_runs_and_public_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            db_path = root / "read.sqlite"
            _write_run(runs_dir / "run-a", run_id="run-a", agent_handle="agent-a")

            model = SurfaceReadModel(db_path)
            result = model.refresh_from_artifacts(runs_dir)

            self.assertEqual(1, result.runs_indexed)
            bundle = model.surface_bundle()
            self.assertEqual(1, bundle["stats"]["runs"])
            self.assertEqual("agent-a", bundle["leaderboard"][0]["agent_handle"])
            artifact = model.public_artifact_path("run-a", "patch.diff")
            self.assertIsNotNone(artifact)
            self.assertEqual("work", model.phase_history("run-a")[0]["phase"])
            self.assertEqual("aci_submit_patch", model.tool_violations("run-a")[0]["tool"])

    def test_api_registers_read_routes_and_model_serves_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            _write_run(runs_dir / "run-a", run_id="run-a", agent_handle="agent-a")
            config = _config(root)
            app = create_app(config, input_dir=runs_dir, watch=False)
            routes = {getattr(route, "path", "") for route in app.routes}

            self.assertIn("/api/health", routes)
            self.assertIn("/api/surface", routes)
            self.assertIn("/api/runs/{run_id}", routes)
            self.assertIn("/api/artifacts/{run_id}/{artifact_name}", routes)

            model = SurfaceReadModel(root / "read.sqlite")
            model.refresh_from_artifacts(runs_dir)
            self.assertEqual(1, model.status(runs_dir).runs)
            self.assertEqual("run-a", model.surface_bundle()["runs"][0]["run_id"])
            self.assertEqual("agent-a", model.run("run-a")["agent"]["handle"])  # type: ignore[index]
            self.assertEqual("run-a", model.runs(query="example/repo")[0]["run_id"])
            self.assertEqual([], model.runs(query="missing-string"))
            self.assertIsNotNone(model.public_artifact_path("run-a", "patch.diff"))
            self.assertIsNone(model.public_artifact_path("run-a", "trace.jsonl"))


def _config(root: Path) -> RunConfig:
    return RunConfig(
        run=RunSection(id="run-1", mode="shadow", model="local-stub"),
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
        backend={"read_model_path": root / "read.sqlite"},
    )


def _write_run(path: Path, *, run_id: str, agent_handle: str) -> None:
    path.mkdir(parents=True)
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "run_mode": "shadow",
        "model": "local-stub",
        "agent": {"name": "Agent A", "handle": agent_handle},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "season": {"id": "season_0", "name": "Season 0", "phase": "owned_repo_calibration"},
        "opportunity_source": "none",
        "opportunity_source_ref": "",
        "started_at": "2026-05-15T00:00:00Z",
        "completed_at": "2026-05-15T00:01:00Z",
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "complete",
        "terminal_layer": "agent",
        "contribution_class": "low_risk_code",
        "pipeline": [],
        "quality_gate": {"status": "pass", "warnings": []},
        "pull_request": {"url": "https://github.com/example/repo/pull/1", "number": 1, "state": "open"},
        "maintainer_outcome": {"status": "pending", "observed_at": "", "source": "none"},
        "judgement": {
            "status": "judged",
            "judge_score": 72,
            "real_world_adjustment": 2,
            "arena_score": 74,
            "rubric_summary": [],
            "source_artifacts": ["judgement.json"],
        },
        "artifacts": [
            {
                "name": "patch.diff",
                "kind": "diff",
                "visibility": "public",
                "url": "",
                "size_bytes": 120,
                "redacted": False,
            },
            {
                "name": "trace.jsonl",
                "kind": "jsonl",
                "visibility": "internal",
                "url": "",
                "size_bytes": 1200,
                "redacted": False,
            },
        ],
    }
    (path / "run_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (path / "patch.diff").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    (path / "trace.jsonl").write_text("", encoding="utf-8")
    (path / "phase_transition.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "event_id": "event-a",
                "source": "goal_events.jsonl",
                "run_id": run_id,
                "goal_id": "goal-a",
                "event_type": "goal_created",
                "scope": "contribution",
                "status": "active",
                "phase": "work",
                "sub_phase": None,
                "created_at": "2026-05-15T00:00:10Z",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "tool_violation_log.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "tool": "aci_submit_patch",
                "phase": "scout",
                "sub_phase": "project",
                "recovery_kind": "phase_violation",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
