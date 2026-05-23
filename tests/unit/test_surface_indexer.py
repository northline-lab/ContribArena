from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contribarena.engine.surface_indexer import index_surface_data


class SurfaceIndexerTests(unittest.TestCase):
    def test_indexer_writes_frontend_bundle_and_leaderboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="Agent A",
                agent_handle="agent-a",
                season_id="season_0",
                qg_status="pass",
                pr_state="merged",
                maintainer_status="merged",
                judge_score=74.0,
                arena_score=104.0,
            )
            _write_run(
                input_dir / "run-b",
                run_id="run-b",
                agent_name="Agent B",
                agent_handle="agent-b",
                season_id="season_0",
                qg_status="fail",
                pr_state="none",
                maintainer_status="unknown",
                judge_score=44.0,
                arena_score=44.0,
            )
            state_dir = root / "seasons" / "season_0"
            state_dir.mkdir(parents=True)
            (state_dir / "season_state.json").write_text(
                json.dumps(
                    {
                        "season_id": "season_0",
                        "name": "Season 0",
                        "status": "active",
                        "paused": True,
                        "heartbeat": {
                            "count": 2,
                            "last_status": "ok",
                            "last_started_at": "2026-05-20T00:00:00Z",
                            "last_completed_at": "2026-05-20T00:00:01Z",
                        },
                        "runtime_events": [
                            {
                                "ts": "2026-05-20T00:00:00Z",
                                "event": "heartbeat_started",
                                "heartbeat_count": 2,
                            }
                        ],
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )

            result = index_surface_data(input_dir=input_dir, output_dir=output_dir)

            self.assertEqual(2, result.runs_indexed)
            self.assertEqual(2, result.artifacts_copied)
            surface = _read_json(output_dir / "surface.json")
            self.assertEqual("1", surface["schema_version"])
            self.assertEqual(2, surface["stats"]["runs"])
            self.assertEqual(0.5, surface["stats"]["quality_gate_pass_rate"])
            self.assertEqual("agent-a", surface["leaderboard"][0]["agent_handle"])
            self.assertEqual(104.0, surface["leaderboard"][0]["mean_arena_score"])
            self.assertEqual("season_0", surface["seasons"][0]["id"])
            self.assertTrue(surface["seasons"][0]["paused"])
            self.assertEqual("ok", surface["seasons"][0]["heartbeat"]["last_status"])
            self.assertTrue(
                any(event.get("event") == "heartbeat_started" for event in surface["scheduler"])
            )
            self.assertEqual("season_0:agent-a", surface["participants"][0]["participant_id"])
            self.assertEqual("agent framework", surface["discovery"]["run-a"][0]["query"])
            self.assertEqual("I will inspect the repo.", surface["assistant_updates"]["run-a"][0]["text"])
            self.assertEqual("self_review", surface["runs"][0]["self_review"][0]["reviewer_role"])
            self.assertEqual("I will inspect the repo.", surface["runs"][0]["assistant_updates"][0]["text"])
            self.assertEqual("work", surface["runs"][0]["phase_history"][0]["phase"])
            self.assertEqual("aci_submit_patch", surface["runs"][0]["tool_violations"][0]["tool"])
            self.assertEqual("open", surface["pr_lifecycle"][0]["state"])
            self.assertEqual("auto", surface["scheduler"][0]["wake_source"])
            self.assertEqual("container-1", surface["workspaces"][0]["container_id"])
            self.assertTrue((output_dir / "runs.json").exists())
            self.assertTrue((output_dir / "leaderboard.json").exists())
            self.assertTrue((output_dir / "stats.json").exists())
            self.assertTrue((output_dir / "seasons.json").exists())
            self.assertTrue((output_dir / "participants.json").exists())
            self.assertTrue((output_dir / "pr_lifecycle.json").exists())
            self.assertTrue((output_dir / "discovery_calls.json").exists())
            self.assertTrue((output_dir / "assistant_updates.json").exists())
            self.assertTrue((output_dir / "scheduler_events.json").exists())
            self.assertTrue((output_dir / "season_workspaces.json").exists())
            self.assertTrue((output_dir / "runs" / "run-a.discovery.json").exists())
            self.assertTrue((output_dir / "runs" / "run-a.self_review.json").exists())
            self.assertTrue((output_dir / "runs" / "run-a.assistant_updates.json").exists())
            self.assertTrue((output_dir / "runs" / "run-a.json").exists())
            self.assertTrue((output_dir / "runs" / "run-a" / "artifacts" / "patch.diff").exists())

    def test_indexer_clears_internal_artifact_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="Agent A",
                agent_handle="agent-a",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=70.0,
                arena_score=70.0,
            )

            index_surface_data(
                input_dir=input_dir,
                output_dir=output_dir,
                public_base_url="https://example.test/data",
            )

            run = _read_json(output_dir / "runs" / "run-a.json")
            artifacts = {artifact["name"]: artifact for artifact in run["artifacts"]}
            self.assertEqual(
                "https://example.test/data/runs/run-a/artifacts/patch.diff",
                artifacts["patch.diff"]["url"],
            )
            self.assertEqual("", artifacts["trace.jsonl"]["url"])

    def test_indexer_default_artifact_urls_match_static_surface_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "surface" / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="Agent A",
                agent_handle="agent-a",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=70.0,
                arena_score=70.0,
            )

            index_surface_data(input_dir=input_dir, output_dir=output_dir)

            run = _read_json(output_dir / "runs" / "run-a.json")
            artifacts = {artifact["name"]: artifact for artifact in run["artifacts"]}
            self.assertEqual(
                "data/runs/run-a/artifacts/patch.diff",
                artifacts["patch.diff"]["url"],
            )
            self.assertTrue((output_dir / "runs" / "run-a" / "artifacts" / "patch.diff").exists())

    def test_indexer_rewrites_public_artifact_urls_to_static_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "surface" / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="Agent A",
                agent_handle="agent-a",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=70.0,
                arena_score=70.0,
            )
            summary_path = input_dir / "run-a" / "run_summary.json"
            payload = _read_json(summary_path)
            payload["artifacts"][0]["url"] = "https://private.example/patch.diff"
            summary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            index_surface_data(input_dir=input_dir, output_dir=output_dir)

            run = _read_json(output_dir / "runs" / "run-a.json")
            artifacts = {artifact["name"]: artifact for artifact in run["artifacts"]}
            self.assertEqual(
                "data/runs/run-a/artifacts/patch.diff",
                artifacts["patch.diff"]["url"],
            )

    def test_indexer_does_not_expose_missing_public_artifact_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "surface" / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="Agent A",
                agent_handle="agent-a",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=70.0,
                arena_score=70.0,
            )
            summary_path = input_dir / "run-a" / "run_summary.json"
            payload = _read_json(summary_path)
            payload["artifacts"].append(
                {
                    "name": "missing.md",
                    "kind": "markdown",
                    "visibility": "public",
                    "url": "https://private.example/missing.md",
                    "size_bytes": 50,
                    "redacted": False,
                }
            )
            summary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            result = index_surface_data(input_dir=input_dir, output_dir=output_dir)

            run = _read_json(output_dir / "runs" / "run-a.json")
            artifacts = {artifact["name"]: artifact for artifact in run["artifacts"]}
            self.assertEqual("", artifacts["missing.md"]["url"])
            self.assertIn("missing public artifact 'missing.md'", result.skipped[0])

    def test_replacement_runs_do_not_count_in_leaderboard_or_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="gpt-5.5",
                agent_handle="gpt-5.5",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=70.0,
                arena_score=70.0,
            )
            _write_run(
                input_dir / "run-b",
                run_id="run-b",
                agent_name="gpt-5.5",
                agent_handle="gpt-5.5",
                season_id="season_0",
                qg_status="fail",
                pr_state="none",
                maintainer_status="unknown",
                judge_score=10.0,
                arena_score=10.0,
            )
            summary_path = input_dir / "run-b" / "run_summary.json"
            payload = _read_json(summary_path)
            payload["replacement"] = {"status": "replaced", "reason": "model_runtime", "layer": "model_runtime"}
            summary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            index_surface_data(input_dir=input_dir, output_dir=output_dir)

            surface = _read_json(output_dir / "surface.json")
            self.assertEqual(1, surface["stats"]["runs"])
            self.assertEqual(1, surface["seasons"][0]["runs_count"])
            self.assertEqual(1, surface["participants"][0]["runs_count"])
            self.assertEqual(1, surface["leaderboard"][0]["runs"])
            self.assertEqual(70.0, surface["leaderboard"][0]["mean_arena_score"])

    def test_judgement_retry_due_runs_do_not_count_in_leaderboard_or_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="qwen-3.6-plus",
                agent_handle="qwen-3.6-plus",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=75.0,
                arena_score=75.0,
            )
            _write_run(
                input_dir / "run-b",
                run_id="run-b",
                agent_name="gpt-5.5",
                agent_handle="gpt-5.5",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=10.0,
                arena_score=10.0,
            )
            summary_path = input_dir / "run-b" / "run_summary.json"
            payload = _read_json(summary_path)
            payload["judgement"]["status"] = "deferred"
            payload["judgement"]["judge_score"] = None
            payload["judgement"]["arena_score"] = None
            payload["judgement_retry"] = {
                "status": "due",
                "reason": "transient_judge_failure",
            }
            summary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            index_surface_data(input_dir=input_dir, output_dir=output_dir)

            surface = _read_json(output_dir / "surface.json")
            self.assertEqual(1, surface["stats"]["runs"])
            self.assertEqual(["qwen-3.6-plus"], [row["agent_name"] for row in surface["leaderboard"]])

    def test_builtin_agent_name_is_normalized_from_participant_for_public_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "runs"
            output_dir = root / "public" / "data"
            _write_run(
                input_dir / "run-a",
                run_id="run-a",
                agent_name="builtin",
                agent_handle="builtin",
                season_id="season_0",
                qg_status="pass",
                pr_state="open",
                maintainer_status="pending",
                judge_score=72.0,
                arena_score=72.0,
            )
            summary_path = input_dir / "run-a" / "run_summary.json"
            payload = _read_json(summary_path)
            payload["model"] = "responses/gpt55"
            payload["agent"]["participant_id"] = "season_0:gpt-5.5"
            summary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

            index_surface_data(input_dir=input_dir, output_dir=output_dir)

            surface = _read_json(output_dir / "surface.json")
            self.assertEqual(["gpt-5.5"], [row["agent_name"] for row in surface["leaderboard"]])
            self.assertEqual("gpt-5.5", surface["runs"][0]["agent"]["name"])


def _write_run(
    path: Path,
    *,
    run_id: str,
    agent_name: str,
    agent_handle: str,
    season_id: str,
    qg_status: str,
    pr_state: str,
    maintainer_status: str,
    judge_score: float,
    arena_score: float,
) -> None:
    path.mkdir(parents=True)
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "run_mode": "shadow",
        "model": "local-stub",
        "wake_source": "auto",
        "agent": {"name": agent_name, "handle": agent_handle, "participant_id": f"season_0:{agent_handle}"},
        "repository": {"full_name": "example/repo", "url": "https://github.com/example/repo"},
        "season": {"id": season_id, "name": "Season 0", "phase": "owned_repo_calibration"},
        "opportunity_source": "issue_url",
        "opportunity_source_ref": "https://github.com/example/repo/issues/1",
        "started_at": f"2026-05-15T00:00:0{run_id[-1:] if run_id[-1:].isdigit() else 1}Z",
        "completed_at": "2026-05-15T00:01:00Z",
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "goal complete",
        "terminal_layer": "agent",
        "contribution_class": "low_risk_code",
        "pipeline": [
            {
                "stage_id": "quality_gate",
                "status": "passed" if qg_status == "pass" else "failed",
                "started_at": "",
                "completed_at": "",
                "summary": "",
                "source_artifacts": ["quality_gate.json"],
            }
        ],
        "quality_gate": {"status": qg_status, "warnings": []},
        "pull_request": {
            "url": "https://github.com/example/repo/pull/1" if pr_state != "none" else "",
            "number": 1 if pr_state != "none" else None,
            "state": pr_state,
        },
        "maintainer_outcome": {
            "status": maintainer_status,
            "observed_at": "2026-05-15T00:02:00Z",
            "source": "github_pr_state" if maintainer_status == "merged" else "none",
        },
        "judgement": {
            "status": "judged",
            "judge_score": judge_score,
            "real_world_adjustment": arena_score - judge_score,
            "arena_score": arena_score,
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
                "url": "https://private.example/trace.jsonl",
                "size_bytes": 12000,
                "redacted": False,
            },
        ],
    }
    (path / "run_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (path / "patch.diff").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    (path / "discovery_log.jsonl").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "season_id": season_id,
                "participant_id": f"season_0:{agent_handle}",
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
                "participant_id": f"season_0:{agent_handle}",
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
    (path / "phase_transition.jsonl").write_text(
        json.dumps({"phase": "work", "event_type": "goal_created"}, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    (path / "tool_violation_log.jsonl").write_text(
        json.dumps({"tool": "aci_submit_patch", "phase": "scout", "recovery_kind": "phase_violation"}, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    (path / "operator_events.jsonl").write_text(
        json.dumps({"ts": "2026-05-15T00:00:00Z", "phase": "run", "status": "started"}, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    (path / "pr_review_log.jsonl").write_text(
        json.dumps({"repository": "example/repo", "number": 1, "state": "open"}, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )
    workspace_dir = path.parent / "seasons" / season_id / "participants" / f"season_0:{agent_handle}" / "workspaces" / "example-repo"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "container_id").write_text("container-1\n", encoding="utf-8")
    (workspace_dir / "last_used_at").write_text("2026-05-15T00:00:00Z\n", encoding="utf-8")
    (workspace_dir / "clone_state.json").write_text(
        json.dumps({"repo_slug": "example/repo", "run_id": run_id}, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    config = {"workspace": {"persistent_metadata_path": str(workspace_dir / "container_id")}}
    (path / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
