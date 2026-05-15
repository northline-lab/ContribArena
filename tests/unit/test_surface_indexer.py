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

            result = index_surface_data(input_dir=input_dir, output_dir=output_dir)

            self.assertEqual(2, result.runs_indexed)
            self.assertEqual(2, result.artifacts_copied)
            surface = _read_json(output_dir / "surface.json")
            self.assertEqual("1", surface["schema_version"])
            self.assertEqual(2, surface["stats"]["runs"])
            self.assertEqual(0.5, surface["stats"]["quality_gate_pass_rate"])
            self.assertEqual("agent-a", surface["leaderboard"][0]["agent_handle"])
            self.assertEqual(104.0, surface["leaderboard"][0]["mean_arena_score"])
            self.assertTrue((output_dir / "runs.json").exists())
            self.assertTrue((output_dir / "leaderboard.json").exists())
            self.assertTrue((output_dir / "stats.json").exists())
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
        "agent": {"name": agent_name, "handle": agent_handle},
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


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
