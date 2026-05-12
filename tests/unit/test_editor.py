from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from contribarena.models import CommandResult, PatchResult
from contribarena.tools.editor import aci_apply_patch


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
            files_modified=_files_from_patch(diff),
            error=None if completed.returncode == 0 else completed.stderr,
        )


class EditorBoundaryTest(unittest.TestCase):
    def test_content_operations_and_undo_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "a.txt").write_text("old\n", encoding="utf-8")
            workspace = LocalWorkspace(root)

            update = aci_apply_patch(
                workspace,
                [{"type": "update_file", "path": "repo/a.txt", "content": "new\n"}],
            )
            create = aci_apply_patch(
                workspace,
                [{"type": "create_file", "path": "repo/b.txt", "content": "b\n"}],
            )
            move = aci_apply_patch(
                workspace,
                [{"type": "move_file", "path": "repo/b.txt", "destination": "repo/c.txt"}],
            )
            delete = aci_apply_patch(
                workspace,
                [{"type": "delete_file", "path": "repo/c.txt"}],
            )
            undo_delete = workspace.apply_patch(delete.undo_diff or "")

            self.assertTrue(update.result.success, update.result.error)
            self.assertTrue(create.result.success, create.result.error)
            self.assertTrue(move.result.success, move.result.error)
            self.assertTrue(delete.result.success, delete.result.error)
            self.assertTrue(undo_delete.success, undo_delete.error)
            self.assertEqual("new\n", (root / "repo" / "a.txt").read_text(encoding="utf-8"))
            self.assertEqual("b\n", (root / "repo" / "c.txt").read_text(encoding="utf-8"))

    def test_structured_patch_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            workspace = LocalWorkspace(root)

            result = aci_apply_patch(
                workspace,
                [
                    {
                        "type": "update_file",
                        "path": "repo/app.py",
                        "diff": (
                            "*** Begin Patch\n"
                            "*** Update File: repo/app.py\n"
                            "@@\n"
                            " x = 1\n"
                            "-y = 2\n"
                            "+y = 3\n"
                            "*** End Patch"
                        ),
                    }
                ],
            ).result

            self.assertTrue(result.success, result.error)
            self.assertEqual("x = 1\ny = 3\n", (root / "repo" / "app.py").read_text())

    def test_accepts_common_operation_aliases_and_hunk_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "app.py").write_text("alpha\nbeta\n", encoding="utf-8")
            workspace = LocalWorkspace(root)

            result = aci_apply_patch(
                workspace,
                [
                    {
                        "op": "update_file",
                        "path": "repo/app.py",
                        "hunks": [{"old": "beta", "new": "gamma"}],
                    }
                ],
            ).result

            self.assertTrue(result.success, result.error)
            self.assertEqual("alpha\ngamma\n", (root / "repo" / "app.py").read_text())

    def test_rejects_invalid_schema_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            workspace = LocalWorkspace(root)

            invalid = aci_apply_patch(workspace, [{"type": "replace", "path": "repo/a.txt"}]).result
            unsafe = aci_apply_patch(
                workspace,
                [{"type": "create_file", "path": "../a.txt", "content": "x"}],
            ).result

            self.assertFalse(invalid.success)
            self.assertEqual("invalid_schema", invalid.error_kind)
            self.assertFalse(unsafe.success)
            self.assertEqual("unsafe_path", unsafe.error_kind)

    def test_rejects_file_state_and_binary_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "exists.txt").write_text("x\n", encoding="utf-8")
            (root / "repo" / "binary.dat").write_bytes(b"a\x00b")
            workspace = LocalWorkspace(root)

            missing = aci_apply_patch(
                workspace,
                [{"type": "update_file", "path": "repo/missing.txt", "content": "x\n"}],
            ).result
            exists = aci_apply_patch(
                workspace,
                [{"type": "create_file", "path": "repo/exists.txt", "content": "x\n"}],
            ).result
            binary = aci_apply_patch(
                workspace,
                [{"type": "update_file", "path": "repo/binary.dat", "content": "x\n"}],
            ).result

            self.assertEqual("file_not_found", missing.error_kind)
            self.assertEqual("file_exists", exists.error_kind)
            self.assertEqual("binary_file", binary.error_kind)

    def test_rejects_patch_parse_context_ambiguous_generated_and_large_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "repo" / "app.py").write_text("same\nsame\n", encoding="utf-8")
            workspace = LocalWorkspace(root)

            parse = aci_apply_patch(
                workspace,
                [{"type": "update_file", "path": "repo/app.py", "diff": "not a patch"}],
            ).result
            mismatch = aci_apply_patch(
                workspace,
                [
                    {
                        "type": "update_file",
                        "path": "repo/app.py",
                        "diff": "*** Begin Patch\n*** Update File: repo/app.py\n@@\n-missing\n+new\n*** End Patch",
                    }
                ],
            ).result
            ambiguous = aci_apply_patch(
                workspace,
                [
                    {
                        "type": "update_file",
                        "path": "repo/app.py",
                        "diff": "*** Begin Patch\n*** Update File: repo/app.py\n@@\n-same\n+new\n*** End Patch",
                    }
                ],
            ).result
            generated = aci_apply_patch(
                workspace,
                [{"type": "create_file", "path": "repo/.pytest_cache/x", "content": "x\n"}],
            ).result
            large = aci_apply_patch(
                workspace,
                [
                    {
                        "type": "update_file",
                        "path": "repo/app.py",
                        "content": "x" * 210_000,
                    }
                ],
            ).result

            self.assertEqual("patch_parse_error", parse.error_kind)
            self.assertEqual("context_mismatch", mismatch.error_kind)
            self.assertIn("Snippet", mismatch.output)
            self.assertEqual("ambiguous_match", ambiguous.error_kind)
            self.assertIn("Ambiguous", ambiguous.output)
            self.assertEqual("generated_file_rejected", generated.error_kind)
            self.assertEqual("too_large_edit", large.error_kind)


def _files_from_patch(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[3].removeprefix("b/"))
        elif line.startswith("+++ b/"):
            files.append(line.removeprefix("+++ b/"))
    return sorted(set(files))


if __name__ == "__main__":
    unittest.main()
