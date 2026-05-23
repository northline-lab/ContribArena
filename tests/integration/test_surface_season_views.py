from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contribarena.config import load_run_config
from contribarena.engine.api import create_app
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.surface_indexer import index_surface_data

from tests.integration.test_season_end_to_end import _write_run_artifacts


class SurfaceSeasonViewTests(unittest.TestCase):
    def test_static_surface_and_api_expose_truthful_season_zero_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_dir = root / "runs"
            workspace_dir = (
                root
                / "seasons"
                / "season_0"
                / "participants"
                / "season_0:qwen36plus"
                / "workspaces"
                / "wanjiedata-ContribArena"
            )
            workspace_dir.mkdir(parents=True)
            (workspace_dir / "container_id").write_text(
                "container-season_0:qwen36plus\n",
                encoding="utf-8",
            )
            (workspace_dir / "last_used_at").write_text(
                "2026-05-19T08:00:00Z\n",
                encoding="utf-8",
            )
            (workspace_dir / "clone_state.json").write_text(
                '{"repo_slug":"wanjiedata/ContribArena","run_id":"surface-run"}\n',
                encoding="utf-8",
            )
            run_dir = runs_dir / "surface-run"
            run_dir.mkdir(parents=True)
            _write_run_artifacts(
                run_dir,
                run_id="surface-run",
                participant_id="season_0:qwen36plus",
                seq=1,
                workspace_dir=workspace_dir,
            )

            static = index_surface_data(
                input_dir=runs_dir,
                output_dir=root / "public" / "data",
            )

            self.assertEqual(1, static.runs_indexed)
            surface = _read_json(root / "public" / "data" / "surface.json")
            self.assertEqual("season_0", surface["seasons"][0]["id"])
            self.assertEqual("season_0:qwen36plus", surface["participants"][0]["participant_id"])
            self.assertEqual("agent framework", surface["discovery"]["surface-run"][0]["query"])
            self.assertEqual(
                "self_pre_submission_review",
                surface["runs"][0]["self_review"][0]["reviewer_role"],
            )
            self.assertEqual("auto", surface["scheduler"][0]["wake_source"])
            self.assertEqual("container-season_0:qwen36plus", surface["workspaces"][0]["container_id"])
            self.assertEqual("tracking", surface["pr_lifecycle"][0]["lifecycle_status"])
            self.assertEqual(
                "data/runs/surface-run/artifacts/patch.diff",
                surface["runs"][0]["artifacts"][0]["url"],
            )

            loaded_config = load_run_config(Path("examples/season-0-owned.yaml"))
            config = loaded_config.model_copy(
                update={
                    "artifacts": loaded_config.artifacts.model_copy(update={"output_root": runs_dir}),
                    "backend": loaded_config.backend.model_copy(
                        update={"read_model_path": root / "read.sqlite", "watch_enabled": False}
                    ),
                },
                deep=True,
            )
            app = create_app(config, input_dir=runs_dir, watch=False)
            routes = {getattr(route, "path", "") for route in app.routes}
            self.assertIn("/api/seasons/{season_id}", routes)
            self.assertIn("/api/seasons/{season_id}/participants", routes)
            self.assertIn("/api/seasons/{season_id}/leaderboard", routes)
            self.assertIn("/api/participants/{participant_id}", routes)
            self.assertIn("/api/seasons/{season_id}/pr-lifecycle", routes)
            self.assertIn("/api/seasons/{season_id}/scheduler", routes)
            self.assertIn("/api/seasons/{season_id}/workspaces", routes)
            self.assertIn("/api/runs/{run_id}/discovery", routes)
            self.assertIn("/api/runs/{run_id}/self-review", routes)

            read_model = SurfaceReadModel(root / "read.sqlite")
            read_model.refresh_from_artifacts(runs_dir)
            season_payload = read_model.seasons()[0]
            self.assertEqual(1, season_payload["participants_count"])
            self.assertEqual(
                "season_0:qwen36plus",
                read_model.participants("season_0")[0]["participant_id"],
            )
            self.assertEqual("auto", read_model.scheduler_events("season_0")[0]["wake_source"])
            self.assertEqual(
                "container-season_0:qwen36plus",
                read_model.season_workspaces("season_0")[0]["container_id"],
            )
            participant_payload = read_model.participant("season_0:qwen36plus")
            self.assertIsNotNone(participant_payload)
            assert participant_payload is not None
            self.assertEqual("surface-run", participant_payload["latest_run_id"])
            self.assertEqual(1, len(participant_payload["pr_lifecycle"]))
            self.assertEqual("agent framework", read_model.discovery_calls("surface-run")[0]["query"])
            self.assertEqual(
                "self_pre_submission_review",
                read_model.self_review("surface-run")[0]["reviewer_role"],
            )


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
