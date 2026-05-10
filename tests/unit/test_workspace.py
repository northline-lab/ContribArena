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
            self.assertIn("rm -f", log)


if __name__ == "__main__":
    unittest.main()
