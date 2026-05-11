from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from contribarena.models import CommandResult, PatchResult
from contribarena.tools.aci import aci_create, aci_replace, aci_search, aci_submit_patch, aci_view


class FakeWorkspace:
    def __init__(self) -> None:
        self.files = {"repo/app.py": "print('old')\n"}
        self.commands: list[str] = []
        self.patches: list[str] = []

    def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
        self.commands.append(cmd)
        if cmd.startswith("cat -- 'repo/app.py'") or cmd.startswith("cat -- repo/app.py"):
            return _cmd(cmd, stdout=self.files["repo/app.py"])
        if cmd.startswith("test ! -e 'repo/new.py'") or cmd.startswith("test ! -e repo/new.py"):
            return _cmd(cmd)
        if "nl -ba" in cmd:
            return _cmd(cmd, stdout="     1\tprint('old')\n")
        if "rg --line-number" in cmd:
            return _cmd(cmd, stdout="repo/app.py:1:print('old')\n")
        if "git diff --binary" in cmd:
            return _cmd(cmd, stdout="diff --git a/repo/app.py b/repo/app.py\n")
        return _cmd(cmd, stderr="unexpected command", exit_code=1)

    def apply_patch(self, diff: str) -> PatchResult:
        self.patches.append(diff)
        if "repo/app.py" in diff:
            self.files["repo/app.py"] = "print('new')\n"
            return PatchResult(success=True, files_modified=["repo/app.py"])
        if "repo/new.py" in diff:
            self.files["repo/new.py"] = "value = 1\n"
            return PatchResult(success=True, files_modified=["repo/new.py"])
        return PatchResult(success=False, error="bad patch")


class LocalWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult:
        completed = subprocess.run(
            ["bash", "-c", cmd],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return CommandResult(
            command=cmd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=0.01,
        )

    def apply_patch(self, diff: str) -> PatchResult:
        completed = subprocess.run(
            ["git", "apply", "-"],
            cwd=self.root,
            input=diff,
            capture_output=True,
            text=True,
            check=False,
        )
        return PatchResult(
            success=completed.returncode == 0,
            files_modified=["repo/app.py"] if "repo/app.py" in diff else ["repo/new.py"],
            error=None if completed.returncode == 0 else completed.stderr,
        )


class AciToolsTest(unittest.TestCase):
    def test_view_search_replace_create_and_submit(self) -> None:
        workspace = FakeWorkspace()

        view = aci_view(workspace, "repo/app.py").result  # type: ignore[arg-type]
        search = aci_search(workspace, "old", "repo").result  # type: ignore[arg-type]
        replace = aci_replace(workspace, "repo/app.py", "old", "new").result  # type: ignore[arg-type]
        create = aci_create(workspace, "repo/new.py", "value = 1\n").result  # type: ignore[arg-type]
        submit = aci_submit_patch(workspace).result  # type: ignore[arg-type]

        self.assertTrue(view.success)
        self.assertIn("print('old')", search.output)
        self.assertTrue(replace.success)
        self.assertEqual(["repo/app.py"], replace.files_modified)
        self.assertTrue(create.success)
        self.assertEqual(["repo/new.py"], create.files_modified)
        self.assertTrue(submit.success)
        self.assertIn("diff --git", submit.output)

    def test_replace_rejects_non_unique_match(self) -> None:
        workspace = FakeWorkspace()
        workspace.files["repo/app.py"] = "x\nx\n"

        result = aci_replace(workspace, "repo/app.py", "x", "y").result  # type: ignore[arg-type]

        self.assertFalse(result.success)
        self.assertIn("matched 2 times", result.error or "")

    def test_generated_patches_apply_with_git_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "app.py").write_text("print('old')\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
            workspace = LocalWorkspace(root)

            replace = aci_replace(workspace, "repo/app.py", "old", "new").result  # type: ignore[arg-type]
            create = aci_create(workspace, "repo/new.py", "value = 1\n").result  # type: ignore[arg-type]

            self.assertTrue(replace.success, replace.error)
            self.assertTrue(create.success, create.error)
            self.assertEqual("print('new')\n", (root / "repo" / "app.py").read_text())
            self.assertEqual("value = 1\n", (root / "repo" / "new.py").read_text())


def _cmd(command: str, stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_seconds=0.01,
    )


if __name__ == "__main__":
    unittest.main()
