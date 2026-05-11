from __future__ import annotations

import difflib
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from contribarena.models import AciResult, CommandResult, PatchResult

if TYPE_CHECKING:
    from contribarena.engine.workspace import DockerWorkspaceManager


DEFAULT_SEARCH_GLOBS = (
    "!.git",
    "!node_modules",
    "!dist",
    "!build",
    "!.venv",
    "!__pycache__",
)


@dataclass
class AciExecution:
    result: AciResult
    commands: list[CommandResult] = field(default_factory=list)
    patches: list[PatchResult] = field(default_factory=list)
    undo_diff: str | None = None


def aci_view(
    workspace: "DockerWorkspaceManager",
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
) -> AciExecution:
    safe_path = _safe_path(path)
    start = max(1, start_line)
    count = min(max(1, max_lines), 500)
    end = start + count - 1
    command = (
        f"test -f {shlex.quote(safe_path)} && "
        f"nl -ba {shlex.quote(safe_path)} | sed -n '{start},{end}p'"
    )
    cmd = workspace.run(command)
    output = _cap_output(cmd.stdout or cmd.stderr)
    return AciExecution(
        result=AciResult(
            tool="aci_view",
            success=cmd.exit_code == 0,
            output=output,
            error=None if cmd.exit_code == 0 else output or f"file not found: {safe_path}",
        ),
        commands=[cmd],
    )


def aci_search(
    workspace: "DockerWorkspaceManager",
    pattern: str,
    path: str = ".",
    max_results: int = 80,
) -> AciExecution:
    safe_path = _safe_path(path)
    limit = min(max(1, max_results), 200)
    glob_args = " ".join(f"--glob {shlex.quote(glob)}" for glob in DEFAULT_SEARCH_GLOBS)
    command = (
        "set +e; "
        f"rg --line-number --color never {glob_args} -- "
        f"{shlex.quote(pattern)} {shlex.quote(safe_path)} | head -n {limit}"
    )
    cmd = workspace.run(command)
    output = _cap_output(cmd.stdout)
    if not output.strip():
        output = f"No matches for {pattern!r} under {safe_path}."
    elif len(output.splitlines()) >= limit:
        output += "\n[result limit reached; narrow the search pattern or path]"
    return AciExecution(
        result=AciResult(tool="aci_search", success=True, output=output),
        commands=[cmd],
    )


def aci_find_files(
    workspace: "DockerWorkspaceManager",
    pattern: str,
    path: str = ".",
    max_results: int = 80,
) -> AciExecution:
    safe_path = _safe_path(path)
    find_pattern = _find_name_pattern(pattern)
    limit = min(max(1, max_results), 200)
    prune_entries = [f"*/{entry.removeprefix('!')}" for entry in DEFAULT_SEARCH_GLOBS]
    prune = " -o ".join(
        f"-path {shlex.quote(entry)}" for entry in prune_entries
    )
    command = (
        f"find {shlex.quote(safe_path)} \\( {prune} \\) -prune -o "
        f"-type f -iname {shlex.quote(find_pattern)} -print | head -n {limit}"
    )
    cmd = workspace.run(command)
    output = _cap_output(cmd.stdout or cmd.stderr)
    if cmd.exit_code == 0 and not output.strip():
        output = f"No files matching {pattern!r} under {safe_path}."
    elif cmd.exit_code == 0 and len(output.splitlines()) >= limit:
        output += "\n[result limit reached; narrow the filename pattern or path]"
    return AciExecution(
        result=AciResult(
            tool="aci_find_files",
            success=cmd.exit_code == 0,
            output=output,
            error=None if cmd.exit_code == 0 else output,
        ),
        commands=[cmd],
    )


def aci_replace(
    workspace: "DockerWorkspaceManager",
    path: str,
    old_str: str,
    new_str: str,
) -> AciExecution:
    safe_path = _safe_path(path)
    read = workspace.run(f"cat -- {shlex.quote(safe_path)}")
    if read.exit_code != 0:
        output = _cap_output(read.stderr or read.stdout)
        return AciExecution(
            result=AciResult(
                tool="aci_replace",
                success=False,
                output=output,
                error=output or f"could not read {safe_path}",
            ),
            commands=[read],
        )
    count = read.stdout.count(old_str)
    if count != 1:
        message = f"old_str must match exactly once in {safe_path}; matched {count} times"
        return AciExecution(
            result=AciResult(tool="aci_replace", success=False, output=message, error=message),
            commands=[read],
        )
    updated = read.stdout.replace(old_str, new_str, 1)
    diff = _unified_diff(safe_path, read.stdout, updated)
    undo_diff = _unified_diff(safe_path, updated, read.stdout)
    patch = workspace.apply_patch(diff)
    output = (
        f"Replaced text in {safe_path}.\n\n{_snippet_after_text(updated, new_str)}"
        if patch.success
        else patch.error or f"patch failed for {safe_path}"
    )
    return AciExecution(
        result=AciResult(
            tool="aci_replace",
            success=patch.success,
            output=_cap_output(output),
            files_modified=patch.files_modified,
            error=None if patch.success else output,
        ),
        commands=[read],
        patches=[patch],
        undo_diff=undo_diff if patch.success else None,
    )


def aci_insert(
    workspace: "DockerWorkspaceManager",
    path: str,
    insert_after_line: int,
    text: str,
) -> AciExecution:
    safe_path = _safe_path(path)
    read = workspace.run(f"cat -- {shlex.quote(safe_path)}")
    if read.exit_code != 0:
        output = _cap_output(read.stderr or read.stdout)
        return AciExecution(
            result=AciResult(
                tool="aci_insert",
                success=False,
                output=output,
                error=output or f"could not read {safe_path}",
            ),
            commands=[read],
        )
    lines = read.stdout.splitlines(keepends=True)
    if insert_after_line < 0 or insert_after_line > len(lines):
        message = (
            f"insert_after_line must be between 0 and {len(lines)} for {safe_path}; "
            f"got {insert_after_line}"
        )
        return AciExecution(
            result=AciResult(tool="aci_insert", success=False, output=message, error=message),
            commands=[read],
        )
    inserted = text if text.endswith("\n") else f"{text}\n"
    updated = "".join([*lines[:insert_after_line], inserted, *lines[insert_after_line:]])
    diff = _unified_diff(safe_path, read.stdout, updated)
    undo_diff = _unified_diff(safe_path, updated, read.stdout)
    patch = workspace.apply_patch(diff)
    output = (
        f"Inserted text into {safe_path} after line {insert_after_line}.\n\n"
        f"{_snippet_around_line(updated, max(1, insert_after_line + 1))}"
        if patch.success
        else patch.error or f"patch failed for {safe_path}"
    )
    return AciExecution(
        result=AciResult(
            tool="aci_insert",
            success=patch.success,
            output=_cap_output(output),
            files_modified=patch.files_modified,
            error=None if patch.success else output,
        ),
        commands=[read],
        patches=[patch],
        undo_diff=undo_diff if patch.success else None,
    )


def aci_create(workspace: "DockerWorkspaceManager", path: str, content: str) -> AciExecution:
    safe_path = _safe_path(path)
    exists = workspace.run(f"test ! -e {shlex.quote(safe_path)}")
    if exists.exit_code != 0:
        message = f"refusing to overwrite existing file: {safe_path}"
        return AciExecution(
            result=AciResult(tool="aci_create", success=False, output=message, error=message),
            commands=[exists],
        )
    diff = _create_file_diff(safe_path, content)
    undo_diff = _delete_file_diff(safe_path, content)
    patch = workspace.apply_patch(diff)
    output = (
        f"Created {safe_path}.\n\n{_snippet_around_line(content, 1)}"
        if patch.success
        else patch.error or f"patch failed for {safe_path}"
    )
    return AciExecution(
        result=AciResult(
            tool="aci_create",
            success=patch.success,
            output=_cap_output(output),
            files_modified=patch.files_modified,
            error=None if patch.success else output,
        ),
        commands=[exists],
        patches=[patch],
        undo_diff=undo_diff if patch.success else None,
    )


def aci_undo(workspace: "DockerWorkspaceManager", diff: str) -> AciExecution:
    patch = workspace.apply_patch(diff)
    output = "Reverted the last ACI edit." if patch.success else patch.error or "undo failed"
    return AciExecution(
        result=AciResult(
            tool="aci_undo",
            success=patch.success,
            output=_cap_output(output),
            files_modified=patch.files_modified,
            error=None if patch.success else output,
        ),
        patches=[patch],
    )


def aci_verify(
    workspace: "DockerWorkspaceManager",
    command: str,
    path: str = "repo",
    timeout_seconds: int | None = None,
) -> AciExecution:
    safe_path = _safe_path(path)
    cmd = workspace.run(
        f"cd {shlex.quote(safe_path)} && {command}",
        timeout_seconds=timeout_seconds,
    )
    output = _cap_output(
        "\n".join(
            part
            for part in [
                f"$ {command}",
                f"exit_code={cmd.exit_code}",
                "stdout:",
                cmd.stdout,
                "stderr:",
                cmd.stderr,
            ]
            if part is not None
        )
    )
    if cmd.exit_code != 0:
        output += f"\n\n[{_verification_recovery_hint(cmd)}]"
    return AciExecution(
        result=AciResult(
            tool="aci_verify",
            success=cmd.exit_code == 0,
            output=output,
            error=None if cmd.exit_code == 0 else output,
        ),
        commands=[cmd],
    )


def aci_suggest_verification(
    workspace: "DockerWorkspaceManager",
    path: str = "repo",
) -> AciExecution:
    safe_path = _safe_path(path)
    command = (
        f"cd {shlex.quote(safe_path)} && "
        "find . -maxdepth 3 -type f "
        "\\( -name pyproject.toml -o -name setup.py -o -name setup.cfg -o "
        "-name pytest.ini -o -name tox.ini -o -name package.json -o "
        "-name pnpm-lock.yaml -o -name yarn.lock -o -name package-lock.json -o "
        "-name Cargo.toml -o -name go.mod -o -name Makefile -o -name justfile -o "
        "-name '*.py' \\) "
        "-print | sort"
    )
    cmd = workspace.run(command)
    if cmd.exit_code != 0:
        output = _cap_output(cmd.stderr or cmd.stdout)
        return AciExecution(
            result=AciResult(
                tool="aci_suggest_verification",
                success=False,
                output=output,
                error=output or f"could not inspect verification files under {safe_path}",
            ),
            commands=[cmd],
        )
    suggestions = _verification_suggestions(cmd.stdout.splitlines())
    output = "\n".join(suggestions) if suggestions else (
        "No obvious verification files found. Use a narrow command such as "
        "`python3 -m compileall .` for Python files or inspect project docs."
    )
    return AciExecution(
        result=AciResult(tool="aci_suggest_verification", success=True, output=output),
        commands=[cmd],
    )


def aci_submit_patch(workspace: "DockerWorkspaceManager", path: str = "repo") -> AciExecution:
    safe_path = _safe_path(path)
    cmd = workspace.run(
        f"if [ -d {shlex.quote(safe_path)}/.git ]; then "
        f"cd {shlex.quote(safe_path)} && git diff --binary -- .; "
        "else git diff --binary -- .; fi"
    )
    output = _cap_output(cmd.stdout or "No workspace diff.")
    return AciExecution(
        result=AciResult(
            tool="aci_submit_patch",
            success=cmd.exit_code == 0,
            output=output,
            error=None if cmd.exit_code == 0 else _cap_output(cmd.stderr or cmd.stdout),
        ),
        commands=[cmd],
    )


def _safe_path(path: str) -> str:
    normalized = str(PurePosixPath(path.strip() or "."))
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"path must be relative to workspace: {path}")
    if "/../" in normalized:
        raise ValueError(f"path must not escape workspace: {path}")
    return normalized


def _find_name_pattern(pattern: str) -> str:
    cleaned = pattern.strip() or "*"
    if cleaned in {"**", "**/*", "./**/*"}:
        return "*"
    if "/" in cleaned:
        return PurePosixPath(cleaned).name or "*"
    return cleaned


def _unified_diff(path: str, old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    body = "".join(
        difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}")
    )
    return f"diff --git a/{path} b/{path}\n{body}"


def _create_file_diff(path: str, content: str) -> str:
    lines = content.splitlines(keepends=True)
    if content and not content.endswith("\n"):
        lines.append("\n")
    body = "".join(
        difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{path}")
    )
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\n{body}"


def _delete_file_diff(path: str, content: str) -> str:
    lines = content.splitlines(keepends=True)
    if content and not content.endswith("\n"):
        lines.append("\n")
    body = "".join(
        difflib.unified_diff(lines, [], fromfile=f"a/{path}", tofile="/dev/null")
    )
    return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n{body}"


def _snippet_after_text(text: str, needle: str, radius: int = 3) -> str:
    line_number = 1
    if needle:
        before = text.find(needle)
        if before >= 0:
            line_number = text[:before].count("\n") + 1
    return _snippet_around_line(text, line_number, radius)


def _snippet_around_line(text: str, line_number: int, radius: int = 3) -> str:
    lines = text.splitlines()
    if not lines:
        return "Snippet: <empty file>"
    center = min(max(1, line_number), len(lines))
    start = max(1, center - radius)
    end = min(len(lines), center + radius)
    body = "\n".join(f"{index:>6}\t{lines[index - 1]}" for index in range(start, end + 1))
    return f"Snippet {start}-{end}:\n{body}"


def _verification_suggestions(files: list[str]) -> list[str]:
    names = {PurePosixPath(path.removeprefix("./")).name for path in files}
    suggestions: list[str] = []
    if {"pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"} & names:
        suggestions.append("python3 -m pytest")
        suggestions.append("python3 -m compileall .")
    elif any(name.endswith(".py") for name in names):
        suggestions.append("python3 -m compileall .")
    if "package.json" in names:
        if "pnpm-lock.yaml" in names:
            suggestions.append("pnpm test")
        elif "yarn.lock" in names:
            suggestions.append("yarn test")
        else:
            suggestions.append("npm test")
    if "Cargo.toml" in names:
        suggestions.append("cargo test")
    if "go.mod" in names:
        suggestions.append("go test ./...")
    if "Makefile" in names:
        suggestions.append("make test")
    if "justfile" in names:
        suggestions.append("just test")
    return [f"- {command}" for command in dict.fromkeys(suggestions)]


def _verification_recovery_hint(cmd: CommandResult) -> str:
    combined = f"{cmd.stdout}\n{cmd.stderr}".lower()
    if cmd.timed_out or cmd.exit_code == 124:
        return (
            "verification timed out; rerun a narrower focused command, inspect the "
            "smallest relevant test target, or record the timeout as a blocker"
        )
    if "command not found" in combined or "not found" in combined or "no such file or directory" in combined:
        return (
            "verification command is missing; call aci_suggest_verification, try the "
            "project-specific equivalent, or record the missing tool as a blocker"
        )
    return (
        "verification failed; inspect the smallest relevant error, fix once, "
        "rerun this command, or use aci_undo if the edit made things worse"
    )


def _cap_output(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[output truncated; narrow the request]"
