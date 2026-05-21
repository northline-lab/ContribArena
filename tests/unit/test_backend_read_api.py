from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from contribarena.config.schema import (
    ArtifactConfig,
    DiscoveryConfig,
    RepoCandidate,
    RunConfig,
    RunSection,
    WorkspaceConfig,
)
from contribarena.engine.api import _default_season_id, _surface_bundle_for_season, create_app
from contribarena.engine.read_model import SurfaceReadModel


class BackendReadApiTests(unittest.TestCase):
    def test_read_model_indexes_runs_and_public_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            db_path = root / "read.sqlite"
            _write_run(runs_dir / "run-a", run_id="run-a", agent_handle="agent-a")
            state_dir = root / "seasons" / "season_0"
            state_dir.mkdir(parents=True)
            (state_dir / "season_state.json").write_text(
                json.dumps(
                    {
                        "season_id": "season_0",
                        "name": "Season 0",
                        "status": "active",
                        "paused": True,
                        "heartbeat": {"count": 1, "last_status": "ok"},
                        "runtime_events": [
                            {"ts": "2026-05-20T00:00:00Z", "event": "heartbeat_completed", "heartbeat_status": "ok"}
                        ],
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )

            model = SurfaceReadModel(db_path)
            result = model.refresh_from_artifacts(runs_dir)

            self.assertEqual(1, result.runs_indexed)
            bundle = model.surface_bundle()
            self.assertEqual(1, bundle["stats"]["runs"])
            self.assertEqual("agent-a", bundle["leaderboard"][0]["agent_handle"])
            artifact = model.public_artifact_path("run-a", "patch.diff")
            self.assertIsNotNone(artifact)
            self.assertEqual("season_0:agent-a", bundle["leaderboard"][0]["participant_id"])
            self.assertTrue(bundle["seasons"][0]["paused"])
            self.assertEqual("ok", model.season_runtime("season_0")["season"]["heartbeat"]["last_status"])
            self.assertEqual("season_0:agent-a", model.participants("season_0")[0]["participant_id"])
            self.assertEqual("agent framework", model.discovery_calls("run-a")[0]["query"])
            self.assertEqual("open", model.pr_lifecycle(season_id="season_0")[0]["state"])
            self.assertEqual("container-1", model.season_workspaces("season_0")[0]["container_id"])
            self.assertEqual("auto", model.scheduler_events("season_0")[0]["wake_source"])
            self.assertEqual("self_review", model.self_review("run-a")[0]["reviewer_role"])
            self.assertEqual("I will inspect the repo.", model.assistant_updates("run-a")[0]["text"])
            self.assertEqual("work", model.phase_history("run-a")[0]["phase"])
            self.assertEqual("aci_submit_patch", model.tool_violations("run-a")[0]["tool"])

    def test_read_model_migrates_existing_runs_table_before_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "read.sqlite"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute(
                    """
                    create table runs (
                        run_id text primary key,
                        season_id text not null,
                        agent_handle text not null,
                        agent_name text not null,
                        run_status text not null,
                        started_at text not null,
                        payload_json text not null
                    )
                    """
                )
                db.commit()

            model = SurfaceReadModel(db_path)
            model.initialize()

            with closing(sqlite3.connect(db_path)) as db:
                columns = {str(row[1]) for row in db.execute("pragma table_info(runs)").fetchall()}
                indexes = {str(row[1]) for row in db.execute("pragma index_list(runs)").fetchall()}
            self.assertIn("participant_id", columns)
            self.assertIn("wake_source", columns)
            self.assertIn("repo_slug", columns)
            self.assertIn("idx_runs_participant", indexes)

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
            self.assertIn("/api/seasons/{season_id}/participants", routes)
            self.assertIn("/api/participants/{participant_id}", routes)
            self.assertIn("/api/seasons/{season_id}/pr-lifecycle", routes)
            self.assertIn("/api/seasons/{season_id}/scheduler", routes)
            self.assertIn("/api/seasons/{season_id}/workspaces", routes)
            self.assertIn("/api/seasons/{season_id}/runtime", routes)
            self.assertIn("/api/runs/{run_id}", routes)
            self.assertIn("/api/runs/{run_id}/discovery", routes)
            self.assertIn("/api/runs/{run_id}/self-review", routes)
            self.assertIn("/api/runs/{run_id}/assistant-updates", routes)
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

    def test_default_api_scope_prefers_active_season_over_old_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            _write_run(
                runs_dir / "old-run",
                run_id="old-run",
                agent_handle="old-agent",
                season_id="old_test",
            )
            _write_run(
                runs_dir / "season-run",
                run_id="season-run",
                agent_handle="gpt-5.5",
                season_id="season_0",
            )
            state_dir = root / "seasons" / "season_0"
            state_dir.mkdir(parents=True)
            (state_dir / "season_state.json").write_text(
                json.dumps(
                    {"season_id": "season_0", "name": "Season 0", "status": "active"},
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
            old_state_dir = root / "seasons" / "old_test"
            old_state_dir.mkdir(parents=True)
            (old_state_dir / "season_state.json").write_text(
                json.dumps(
                    {"season_id": "old_test", "name": "Old Test", "status": "unknown"},
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )

            model = SurfaceReadModel(root / "read.sqlite")
            model.refresh_from_artifacts(runs_dir)

            self.assertEqual("season_0", _default_season_id(model))
            self.assertEqual(1, model.stats(_default_season_id(model))["runs"])
            self.assertEqual("season-run", model.runs(season_id=_default_season_id(model))[0]["run_id"])
            scoped = _surface_bundle_for_season(model, _default_season_id(model))
            self.assertEqual(["season-run"], [run["run_id"] for run in scoped["runs"]])
            self.assertEqual(["season_0"], [season["id"] for season in scoped["seasons"]])
            self.assertEqual(["season_0:gpt-5.5"], [row["participant_id"] for row in scoped["participants"]])

    def test_deferred_judgement_retry_is_excluded_from_default_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            _write_run(
                runs_dir / "run-a",
                run_id="run-a",
                agent_handle="qwen-3.6-plus",
                season_id="season_0",
            )
            _write_run(
                runs_dir / "run-b",
                run_id="run-b",
                agent_handle="gpt-5.5",
                season_id="season_0",
            )
            summary_path = runs_dir / "run-b" / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["judgement"]["status"] = "deferred"
            summary["judgement"]["judge_score"] = None
            summary["judgement"]["arena_score"] = None
            summary["judgement_retry"] = {"status": "due", "reason": "transient_judge_failure"}
            summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            model = SurfaceReadModel(root / "read.sqlite")
            model.refresh_from_artifacts(runs_dir)

            self.assertEqual(1, model.stats("season_0")["runs"])
            self.assertEqual(["qwen-3.6-plus"], [row["agent_name"] for row in model.leaderboard("season_0")])
            runs = model.runs(season_id="season_0")
            excluded = next(run for run in runs if run["run_id"] == "run-b")
            self.assertTrue(excluded["ranking_excluded"])
            self.assertEqual("judgement_retry_due", excluded["ranking_exclusion_reason"])

    def test_builtin_agent_name_is_normalized_from_participant_for_read_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            _write_run(
                runs_dir / "run-a",
                run_id="run-a",
                agent_handle="builtin",
                season_id="season_0",
            )
            summary_path = runs_dir / "run-a" / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["model"] = "responses/gpt55"
            summary["agent"] = {
                "name": "builtin",
                "handle": "builtin",
                "participant_id": "season_0:gpt-5.5",
            }
            summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            model = SurfaceReadModel(root / "read.sqlite")
            model.refresh_from_artifacts(runs_dir)

            self.assertEqual(["gpt-5.5"], [row["agent_name"] for row in model.leaderboard("season_0")])
            self.assertEqual("gpt-5.5", model.runs(season_id="season_0")[0]["agent"]["name"])


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


def _write_run(
    path: Path,
    *,
    run_id: str,
    agent_handle: str,
    season_id: str = "season_0",
) -> None:
    path.mkdir(parents=True)
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "run_mode": "shadow",
        "model": "local-stub",
        "wake_source": "auto",
        "agent": {"name": agent_handle, "handle": agent_handle, "participant_id": f"{season_id}:{agent_handle}"},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "season": {"id": season_id, "name": season_id, "phase": "owned_repo_calibration"},
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
            {
                "name": "phase_review_maintainer_review.jsonl",
                "kind": "jsonl",
                "visibility": "internal",
                "url": "",
                "size_bytes": 120,
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
    (path / "discovery_log.jsonl").write_text(
        json.dumps(
            {
                "season_id": season_id,
                "participant_id": f"{season_id}:{agent_handle}",
                "query": "agent framework",
                "filters_resolved": {"language": "Python"},
                "github_query_string": "agent framework language:Python",
                "total_hits": 3,
                "returned_count": 1,
                "candidates": ["example/repo"],
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "operator_events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-15T00:00:00Z",
                "phase": "run",
                "status": "started",
                "payload": {},
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "pr_review_log.jsonl").write_text(
        json.dumps(
            {
                "repository": "example/repo",
                "number": 1,
                "state": "open",
                "lifecycle_status": "tracking",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "phase_review_maintainer_review.jsonl").write_text(
        json.dumps({"reviewer_role": "self_review", "severity": "low"}, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    (path / "assistant_updates.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "season_id": season_id,
                "participant_id": f"{season_id}:{agent_handle}",
                "phase": "scout",
                "sub_phase": "project",
                "kind": "intent",
                "text": "I will inspect the repo.",
                "tool_name": "repo_get_readme",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace_dir = path.parent / "seasons" / season_id / "participants" / f"{season_id}:{agent_handle}" / "workspaces" / "example-repo"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "container_id").write_text("container-1\n", encoding="utf-8")
    (workspace_dir / "last_used_at").write_text("2026-05-15T00:00:00Z\n", encoding="utf-8")
    (workspace_dir / "clone_state.json").write_text(
        json.dumps({"repo_slug": "example/repo", "run_id": run_id}, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    config = {
        "workspace": {
            "persistent_metadata_path": str(workspace_dir / "container_id"),
        }
    }
    (path / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
