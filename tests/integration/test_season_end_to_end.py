from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from contribarena.config import load_run_config
from contribarena.engine.controller import LocalController
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.runner import RunResult
from contribarena.engine.seasons import load_participant_state


class SeasonEndToEndTests(unittest.TestCase):
    def test_season_zero_owned_config_auto_wakes_five_isolated_participants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_run_config(Path("examples/season-0-owned.yaml"))
            assert config.season is not None
            config = config.model_copy(
                update={
                    "artifacts": config.artifacts.model_copy(update={"output_root": root / "runs"}),
                    "backend": config.backend.model_copy(update={"read_model_path": root / "read.sqlite"}),
                    "memory": config.memory.model_copy(update={"root": root / "memory"}),
                    "season": config.season.model_copy(
                        update={
                            "status": "active",
                            "state_root": root / "seasons",
                            "defaults": config.season.defaults.model_copy(
                                update={"wake_interval": "1s"}
                            ),
                        }
                    ),
                },
                deep=True,
            )
            launcher = _ArtifactLauncher(root)

            result = LocalController(launcher=launcher).run_once(config)

            self.assertEqual("run_completed", result.status)
            self.assertEqual(5, len(launcher.launched))
            self.assertEqual(
                [
                    "season_0:qwen36plus",
                    "season_0:deepseekv4pro",
                    "season_0:gpt55",
                    "season_0:gemini31pro",
                    "season_0:claudeopus47",
                ],
                [item.run.participant_id for item in launcher.launched],
            )
            for launched in launcher.launched:
                participant_id = launched.run.participant_id
                self.assertIsNotNone(participant_id)
                assert participant_id is not None
                state = load_participant_state(
                    store=launcher.store,
                    season_id="season_0",
                    participant_id=participant_id,
                )
                self.assertEqual("auto", state["last_wake_source"])
                self.assertEqual("wanjiedata/ContribArena", state["last_repo_slug"])
                self.assertEqual(1, state["prs_opened"])
                self.assertEqual(0, state["merged_prs"])
                self.assertEqual("submitted season fixture patch", state["latest_goal_summary"])
                participant_dir = root / "seasons" / "season_0" / "participants" / participant_id
                self.assertTrue((participant_dir / "participant_state.json").exists())
                self.assertTrue((participant_dir / "pr_history.json").exists())
                self.assertTrue((participant_dir / "memory" / "events").is_dir())
                self.assertTrue(
                    (
                        participant_dir
                        / "workspaces"
                        / "wanjiedata-ContribArena"
                        / "container_id"
                    ).exists()
                )
            self.assertFalse(
                (
                    root
                    / "seasons"
                    / "season_0"
                    / "participants"
                    / "season_0:qwen36plus"
                    / "workspaces"
                    / "wanjiedata-ContribArena"
                    / "foreign-history.json"
                ).exists()
            )

            model = SurfaceReadModel(root / "read.sqlite")
            refresh = model.refresh_from_artifacts(root / "runs")
            self.assertEqual(5, refresh.runs_indexed)
            self.assertEqual(5, len(model.participants("season_0")))
            self.assertEqual(5, len(model.scheduler_events("season_0")))
            self.assertEqual(5, len(model.season_workspaces("season_0")))
            self.assertEqual(5, len(model.pr_lifecycle(season_id="season_0")))
            self.assertEqual(5, len(model.discovery_calls()))
            qwen = model.participant("season_0:qwen36plus")
            self.assertIsNotNone(qwen)
            assert qwen is not None
            self.assertEqual("season_0:qwen36plus", qwen["participant_id"])
            self.assertEqual(
                [11],
                [row["number"] for row in qwen["pr_lifecycle"]],
            )


class _ArtifactLauncher:
    def __init__(self, root: Path) -> None:
        self.root = root
        from contribarena.engine.seasons import SeasonStore

        self.store = SeasonStore(root / "seasons")
        self.launched: list[object] = []

    def run(self, config, output_dir: Path | None = None, verbose: bool = False) -> RunResult:  # noqa: ANN001
        self.launched.append(config)
        participant_id = config.run.participant_id or "unknown"
        seq = len(self.launched)
        run_id = f"season0-{seq}"
        run_dir = config.artifacts.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = (
            self.root
            / "seasons"
            / "season_0"
            / "participants"
            / participant_id
            / "workspaces"
            / "wanjiedata-ContribArena"
        )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "container_id").write_text(
            f"container-{participant_id}\n",
            encoding="utf-8",
        )
        (workspace_dir / "last_used_at").write_text(_ts(seq), encoding="utf-8")
        (workspace_dir / "clone_state.json").write_text(
            json.dumps(
                {
                    "repo_slug": "wanjiedata/ContribArena",
                    "run_id": run_id,
                    "participant_id": participant_id,
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        participant_dir = self.root / "seasons" / "season_0" / "participants" / participant_id
        (participant_dir / "memory" / "events").mkdir(parents=True, exist_ok=True)
        (participant_dir / "memory" / "events" / f"{run_id}.jsonl").write_text(
            json.dumps(
                {
                    "participant_id": participant_id,
                    "summary": "participant-local memory only",
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (participant_dir / "pr_history.json").write_text(
            json.dumps(
                {
                    "lifecycle_records": [
                        {
                            "season_id": "season_0",
                            "participant_id": participant_id,
                            "repository": "wanjiedata/ContribArena",
                            "number": seq + 10,
                            "url": f"https://github.com/wanjiedata/ContribArena/pull/{seq + 10}",
                            "state": "open",
                            "lifecycle_status": "tracking",
                        }
                    ]
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        participant_state_path = participant_dir / "participant_state.json"
        participant_state = (
            json.loads(participant_state_path.read_text(encoding="utf-8"))
            if participant_state_path.exists()
            else {}
        )
        participant_state.update(
            {
                "prs_opened": 1,
                "merged_prs": 0,
                "latest_goal_summary": "submitted season fixture patch",
            }
        )
        participant_state_path.write_text(
            json.dumps(participant_state, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        _write_run_artifacts(
            run_dir,
            run_id=run_id,
            participant_id=participant_id,
            seq=seq,
            workspace_dir=workspace_dir,
        )
        return RunResult(run_id=run_id, run_dir=run_dir, status="completed", tool_calls=3)


def _write_run_artifacts(
    run_dir: Path,
    *,
    run_id: str,
    participant_id: str,
    seq: int,
    workspace_dir: Path,
) -> None:
    summary = {
        "schema_version": "1",
        "run_id": run_id,
        "run_mode": "owned_live",
        "model": participant_id.removeprefix("season_0:"),
        "wake_source": "auto",
        "agent": {
            "name": "builtin",
            "handle": participant_id,
            "participant_id": participant_id,
        },
        "repository": {
            "full_name": "wanjiedata/ContribArena",
            "url": "https://github.com/wanjiedata/ContribArena",
        },
        "season": {
            "id": "season_0",
            "name": "Season 0 Owned Calibration",
            "phase": "owned_repo_calibration",
            "status": "active",
        },
        "opportunity_source": "discovery",
        "opportunity_source_ref": "wanjiedata/ContribArena",
        "started_at": _ts(seq),
        "completed_at": _ts(seq + 1),
        "duration_seconds": 60,
        "run_status": "completed",
        "terminal_reason": "run_completed",
        "terminal_layer": "run",
        "contribution_class": "low_risk_code",
        "pipeline": [],
        "quality_gate": {"status": "pass", "warnings": []},
        "pull_request": {
            "url": f"https://github.com/wanjiedata/ContribArena/pull/{seq + 10}",
            "number": seq + 10,
            "state": "open",
        },
        "maintainer_outcome": {
            "status": "pending",
            "observed_at": "",
            "source": "none",
        },
        "judgement": {
            "status": "judged",
            "judge_score": 60 + seq,
            "arena_score": 60 + seq,
            "rubric_summary": [],
            "source_artifacts": ["judgement.json"],
        },
        "artifacts": [
            {
                "name": "patch.diff",
                "kind": "diff",
                "visibility": "public",
                "url": "",
                "size_bytes": 80,
                "redacted": False,
            },
            {
                "name": "phase_review_maintainer_review.jsonl",
                "kind": "jsonl",
                "visibility": "internal",
                "url": "",
                "size_bytes": 100,
                "redacted": False,
            },
        ],
    }
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "patch.diff").write_text(
        "diff --git a/README.md b/README.md\n",
        encoding="utf-8",
    )
    (run_dir / "config.json").write_text(
        json.dumps(
            {"workspace": {"persistent_metadata_path": str(workspace_dir / "container_id")}},
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "operator_events.jsonl").write_text(
        json.dumps(
            {
                "ts": _ts(seq),
                "phase": "run",
                "status": "started",
                "payload": {"wake_source": "auto"},
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "discovery_log.jsonl").write_text(
        json.dumps(
            {
                "ts": _ts(seq),
                "season_id": "season_0",
                "participant_id": participant_id,
                "query": "agent framework",
                "filters_resolved": {"language": "Python", "stars_min": 50},
                "github_query_string": "agent framework language:Python stars:>=50",
                "total_hits": 3,
                "returned_count": 1,
                "candidates": ["wanjiedata/ContribArena"],
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "phase_review_maintainer_review.jsonl").write_text(
        json.dumps(
            {
                "reviewer_role": "self_pre_submission_review",
                "review_mode": "self",
                "status": "completed",
                "severity": "low",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "phase_transition.jsonl").write_text(
        json.dumps(
            {
                "event_type": "goal_created",
                "scope": "contribution",
                "phase": "work",
                "sub_phase": None,
                "created_at": _ts(seq),
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "pr_review_log.jsonl").write_text(
        json.dumps(
            {
                "season_id": "season_0",
                "participant_id": participant_id,
                "repository": "wanjiedata/ContribArena",
                "number": seq + 10,
                "url": f"https://github.com/wanjiedata/ContribArena/pull/{seq + 10}",
                "state": "open",
                "lifecycle_status": "tracking",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _ts(offset: int) -> str:
    return datetime(2026, 5, 19, 8, 0, offset, tzinfo=UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
