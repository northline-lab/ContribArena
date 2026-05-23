from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from contribarena.config import load_run_config
from contribarena.config.schema import SeasonParticipantConfig
from contribarena.engine.controller import LocalController
from contribarena.engine.read_model import SurfaceReadModel
from contribarena.engine.runner import RunResult, Runner
from contribarena.engine.seasons import load_participant_state
from contribarena.models import AgentFinalResult, OpportunitySummary, RepoSummary, SelectedTask
from contribarena.models.agent_result import WorkspaceSummary
from contribarena.tools.github_pr import ForkEnsureResult, PullRequestCreateResult


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
                    "judgement": config.judgement.model_copy(update={"enabled": False}),
                    "governance": config.governance.model_copy(update={"live_enabled": True}),
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

            result = None
            controller = LocalController(launcher=launcher)
            for _ in range(5):
                result = controller.run_once(config)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual("run_completed", result.status)
            self.assertEqual(5, len(launcher.launched))
            self.assertEqual(
                {
                    "season_0:qwen36plus",
                    "season_0:deepseekv4pro",
                    "season_0:gpt55",
                    "season_0:gemini31pro",
                    "season_0:claudeopus47",
                },
                {item.run.participant_id for item in launcher.launched},
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

    def test_season_zero_acceptance_runs_real_runner_path_for_one_participant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_run_config(Path("examples/season-0-owned.yaml"))
            assert config.season is not None
            config = config.model_copy(
                update={
                    "artifacts": config.artifacts.model_copy(update={"output_root": root / "runs"}),
                    "backend": config.backend.model_copy(update={"read_model_path": root / "read.sqlite"}),
                    "memory": config.memory.model_copy(update={"root": root / "memory"}),
                    "judgement": config.judgement.model_copy(update={"enabled": False}),
                    "governance": config.governance.model_copy(update={"live_enabled": True}),
                    "season": config.season.model_copy(
                        update={
                            "status": "active",
                            "state_root": root / "seasons",
                            "defaults": config.season.defaults.model_copy(
                                update={"wake_interval": "1s"}
                            ),
                            "participants": [SeasonParticipantConfig(model="local-stub")],
                        }
                    ),
                },
                deep=True,
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_docker(bin_dir / "docker")
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                result = LocalController(
                    launcher=Runner(agent=_TinySeasonAgent(), pr_client=_FakePrClient())
                ).run_once(config)
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual("run_completed", result.status)
            self.assertIsNotNone(result.run_result)
            assert result.run_result is not None
            run_dir = result.run_result.run_dir
            self.assertTrue((run_dir / "run_summary.json").exists())
            workspace_commands = json.loads((run_dir / "workspace_command.json").read_text())
            self.assertTrue(
                any(
                    command.get("command_type") == "setup"
                    and "git -C repo fetch --depth 1 origin" in command.get("command", "")
                    for command in workspace_commands["commands"]
                )
            )
            state = load_participant_state(
                store=__import__(
                    "contribarena.engine.seasons",
                    fromlist=["SeasonStore"],
                ).SeasonStore(root / "seasons"),
                season_id="season_0",
                participant_id="season_0:local-stub",
            )
            self.assertEqual(1, state["runs_count"])
            self.assertEqual(0.0, state["cumulative_cost"])
            model = SurfaceReadModel(root / "read.sqlite")
            refresh = model.refresh_from_artifacts(root / "runs")
            self.assertEqual(1, refresh.runs_indexed)
            self.assertEqual(1, len(model.season_workspaces("season_0")))


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


class _TinySeasonAgent:
    def run(self, config, tools, prompt: str, model_provider: object = None, **kwargs: object):  # noqa: ANN001
        tools.aci_goal_update(
            "Submit a verified season acceptance patch.",
            "active",
            scope="contribution",
        )
        tools.aci_view("repo/README.md")
        tools.aci_replace("repo/app.py", "old", "new")
        tools.aci_verify("python3 -m compileall .", "repo")
        tools.aci_submit_patch()
        tools.aci_submit_patch_finalize()
        return AgentFinalResult(
            status="completed",
            repo=RepoSummary(
                owner="wanjiedata",
                name="ContribArena",
                url="https://github.com/wanjiedata/ContribArena",
            ),
            repo_profile="# Repo Profile\n\nSeason fixture.",
            opportunities=[
                OpportunitySummary(
                    title="Replace marker",
                    rationale="Small deterministic test change.",
                    risk="low",
                    source="test",
                )
            ],
            selected_task=SelectedTask(
                title="Replace marker",
                rationale="Exercise season acceptance path.",
                expected_change="old -> new",
                risk="low",
            ),
            workspace_summary=WorkspaceSummary(
                commands_run=[],
                patch_applied=True,
                notes="season acceptance patch submitted",
            ),
        )


def _write_fake_docker(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env sh\n"
        'args="$*"\n'
        'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
        'if [ "$1" = "inspect" ]; then exit 0; fi\n'
        'if [ "$1" = "start" ]; then exit 0; fi\n'
        'if [ "$1" = "rm" ]; then exit 0; fi\n'
        'if [ "$1" = "exec" ]; then\n'
        '  case "$args" in\n'
        '    *"git -C repo fetch --depth 1 origin"*) exit 0 ;;\n'
        '    *"git -C repo reset --hard FETCH_HEAD"*) exit 0 ;;\n'
        '    *"cat -- repo/README.md"*) printf "# Fixture\\n"; exit 0 ;;\n'
        '    *"cat -- repo/app.py"*) printf "def marker():\\n    return \'old\'\\n"; exit 0 ;;\n'
        '    *"nl -ba repo/app.py"*) printf "     1\\tdef marker():\\n     2\\t    return \'old\'\\n"; exit 0 ;;\n'
        '    *"python3 -m compileall ."*) printf "compile ok\\n"; exit 0 ;;\n'
        '    *"git diff --binary -- ."*) printf "diff --git a/repo/app.py b/repo/app.py\\n"; exit 0 ;;\n'
        '    *"git apply -"*) exit 0 ;;\n'
        '    *) printf "/workspace\\n"; exit 0 ;;\n'
        "  esac\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class _FakePrClient:
    def authenticated_actor(self) -> str:
        return "contribarena-bot"

    def ensure_fork(self, *, owner: str, repo: str, fork_owner: str) -> ForkEnsureResult:
        return ForkEnsureResult(
            ok=True,
            owner=fork_owner,
            repo=repo,
            full_name=f"{fork_owner}/{repo}",
            url=f"https://github.com/{fork_owner}/{repo}",
            created=False,
            source="fake",
        )

    def open_pr(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestCreateResult:
        return PullRequestCreateResult(
            ok=True,
            number=42,
            url=f"https://github.com/{owner}/{repo}/pull/42",
            head_sha="abc123",
            source="fake",
        )


if __name__ == "__main__":
    unittest.main()
