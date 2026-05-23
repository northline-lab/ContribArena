from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from contribarena.config.schema import WorkspaceConfig
from contribarena.engine.workspace import DockerWorkspaceManager


class WorkspaceTest(unittest.TestCase):
    def test_workspace_run_uses_docker_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            log_path = Path(tmp) / "docker.log"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                f'echo "$@" >> {log_path}\n'
                'if [ "$1" = "run" ]; then echo container-id; exit 0; fi\n'
                'if [ "$1" = "exec" ]; then echo /workspace; exit 0; fi\n'
                'if [ "$1" = "rm" ]; then exit 0; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                workspace = DockerWorkspaceManager("run-1", "owner/repo", WorkspaceConfig())
                workspace.start()
                result = workspace.run("pwd")
                patch_result = workspace.apply_patch("diff --git a/a.txt b/a.txt\n")
                workspace.stop()
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(0, result.exit_code)
            self.assertTrue(patch_result.success)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("run -d", log)
            self.assertIn("exec", log)
            self.assertIn("exec -i", log)
            self.assertIn("/proc/1/fd/1", log)
            self.assertIn("set -o pipefail", log)
            self.assertIn("rm -f", log)

    def test_workspace_stop_times_out_instead_of_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                'if [ "$1" = "rm" ]; then sleep 2; exit 0; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                workspace = DockerWorkspaceManager(
                    "run-1",
                    "owner/repo",
                    WorkspaceConfig(command_timeout_seconds=1),
                )
                result = workspace.stop()
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(124, result.exit_code)
            self.assertTrue(result.timed_out)
            self.assertIn("timed out", result.stderr)

    def test_persistent_workspace_start_reuses_existing_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            log_path = root / "docker.log"
            metadata_path = root / "workspace" / "container_id"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                f'echo "$@" >> {log_path}\n'
                'if [ "$1" = "inspect" ]; then exit 0; fi\n'
                'if [ "$1" = "start" ]; then exit 0; fi\n'
                'if [ "$1" = "run" ]; then exit 9; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                workspace = DockerWorkspaceManager(
                    "run-1",
                    "owner/repo",
                    WorkspaceConfig(
                        persistent_key="season_0-agent-owner-repo",
                        persistent_metadata_path=metadata_path,
                    ),
                )
                workspace.start()
            finally:
                os.environ["PATH"] = old_path

            log = log_path.read_text(encoding="utf-8")
            self.assertIn("inspect contribarena-season_0-agent-owner-repo", log)
            self.assertIn("start contribarena-season_0-agent-owner-repo", log)
            self.assertNotIn("run -d", log)
            self.assertEqual("contribarena-season_0-agent-owner-repo", metadata_path.read_text().strip())
            self.assertTrue((metadata_path.parent / "last_used_at").exists())

    def test_persistent_workspace_syncs_repository_with_fetch_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            log_path = root / "docker.log"
            metadata_path = root / "workspace" / "container_id"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                f'echo "$@" >> {log_path}\n'
                'if [ "$1" = "inspect" ]; then exit 0; fi\n'
                'if [ "$1" = "start" ]; then exit 0; fi\n'
                'if [ "$1" = "exec" ]; then exit 0; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                workspace = DockerWorkspaceManager(
                    "run-1",
                    "owner/repo",
                    WorkspaceConfig(
                        persistent_key="season_0-agent-owner-repo",
                        persistent_metadata_path=metadata_path,
                    ),
                )
                workspace.start()
                result = workspace.sync_repository("https://github.com/owner/repo", "main")
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(0, result.exit_code)
            self.assertEqual("setup", result.command_type)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("git -C repo fetch --depth 1 origin main", log)
            self.assertIn("git -C repo reset --hard FETCH_HEAD", log)

    def test_persistent_workspace_stop_retains_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            log_path = root / "docker.log"
            metadata_path = root / "workspace" / "container_id"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env sh\n"
                f'echo "$@" >> {log_path}\n'
                'if [ "$1" = "rm" ]; then exit 9; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                workspace = DockerWorkspaceManager(
                    "run-1",
                    "owner/repo",
                    WorkspaceConfig(
                        persistent_key="season_0-agent-owner-repo",
                        persistent_metadata_path=metadata_path,
                    ),
                )
                result = workspace.stop()
            finally:
                os.environ["PATH"] = old_path

            self.assertEqual(0, result.exit_code)
            self.assertIn("retain persistent workspace", result.command)
            self.assertFalse(log_path.exists())
            self.assertEqual("contribarena-season_0-agent-owner-repo", metadata_path.read_text().strip())


if __name__ == "__main__":
    unittest.main()
