from __future__ import annotations

import difflib
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from contribarena.models import AciResult, CommandResult, PatchOperation, PatchResult


EDIT_ERROR_KINDS = {
    "invalid_schema",
    "unsafe_path",
    "file_not_found",
    "file_exists",
    "binary_file",
    "patch_parse_error",
    "context_mismatch",
    "ambiguous_match",
    "generated_file_rejected",
    "too_large_edit",
    "verification_required",
    "internal_editor_error",
}


class EditorWorkspace(Protocol):
    def run(self, cmd: str, timeout_seconds: int | None = None) -> CommandResult: ...

    def apply_patch(self, diff: str) -> PatchResult: ...


@dataclass
class EditorExecution:
    result: AciResult
    commands: list[CommandResult] = field(default_factory=list)
    patches: list[PatchResult] = field(default_factory=list)
    undo_diff: str | None = None


@dataclass(frozen=True)
class _EditorFailure(Exception):
    kind: str
    message: str
    output: str = ""
    commands: tuple[CommandResult, ...] = ()


def aci_apply_patch(
    workspace: EditorWorkspace,
    operations: list[PatchOperation | dict[str, object]],
    rationale: str = "",
    expected_files: list[str] | None = None,
) -> EditorExecution:
    commands: list[CommandResult] = []
    try:
        normalized = _normalize_operations(operations)
        _validate_expected_files(normalized, expected_files or [])
        diff, undo_diff, command_list, output = _build_patch(workspace, normalized)
        commands.extend(command_list)
        if not diff.strip():
            raise _EditorFailure("invalid_schema", "no edit operations were provided")
        if _edit_size_too_large(diff):
            raise _EditorFailure("too_large_edit", "edit is too large for one aci_apply_patch call")
        patch = workspace.apply_patch(diff)
        if not patch.success:
            raise _EditorFailure(
                "internal_editor_error",
                patch.error or "workspace patch application failed",
                output=patch.error or "",
                commands=tuple(commands),
            )
    except _EditorFailure as failure:
        return EditorExecution(
            result=AciResult(
                tool="aci_apply_patch",
                success=False,
                output=failure.output or failure.message,
                error=failure.message,
                error_kind=failure.kind,
                recovery_kind=_recovery_kind_for_error(failure.kind),
                review_notes=_review_notes(rationale, expected_files),
            ),
            commands=[*commands, *failure.commands],
        )
    return EditorExecution(
        result=AciResult(
            tool="aci_apply_patch",
            success=True,
            output=_cap_output(output),
            files_modified=patch.files_modified,
            review_notes=_review_notes(rationale, expected_files, passed=True),
        ),
        commands=commands,
        patches=[patch],
        undo_diff=undo_diff,
    )


def _normalize_operations(
    operations: list[PatchOperation | dict[str, object]],
) -> list[PatchOperation]:
    if not isinstance(operations, list):
        raise _EditorFailure("invalid_schema", "operations must be a list")
    normalized: list[PatchOperation] = []
    for item in operations:
        prepared = _prepare_operation_item(item)
        try:
            operation = (
                prepared
                if isinstance(prepared, PatchOperation)
                else PatchOperation.model_validate(prepared)
            )
        except Exception as exc:
            raise _EditorFailure("invalid_schema", f"invalid patch operation: {exc}") from exc
        if operation.type not in {"create_file", "update_file", "delete_file", "move_file"}:
            raise _EditorFailure(
                "invalid_schema", f"unsupported patch operation type: {operation.type}"
            )
        normalized.append(operation)
    return normalized


def _prepare_operation_item(
    item: PatchOperation | dict[str, object],
) -> PatchOperation | dict[str, object]:
    if isinstance(item, PatchOperation):
        return item
    if not isinstance(item, dict):
        return item
    prepared = dict(item)
    if "type" not in prepared and "op" in prepared:
        prepared["type"] = prepared["op"]
    if "content" not in prepared:
        for alias in ("new_content", "new_file_content", "replacement"):
            if alias in prepared:
                prepared["content"] = prepared[alias]
                break
    if "diff" not in prepared and "hunks" in prepared:
        prepared["diff"] = _structured_diff_from_hunks(
            str(prepared.get("path") or ""),
            prepared["hunks"],
        )
    return prepared


def _structured_diff_from_hunks(path: str, hunks: object) -> str:
    safe_path = _safe_path(path)
    hunk_texts: list[str] = []
    if isinstance(hunks, str):
        hunk_texts.append(hunks)
    elif isinstance(hunks, list):
        hunk_texts.extend(_structured_hunk_text(hunk) for hunk in hunks)
    else:
        raise _EditorFailure("invalid_schema", "hunks must be a string or list")
    body: list[str] = []
    for hunk in hunk_texts:
        body.append("@@")
        body.extend(hunk.splitlines())
    return "\n".join(
        [
            "*** Begin Patch",
            f"*** Update File: {safe_path}",
            *body,
            "*** End Patch",
        ]
    )


def _structured_hunk_text(hunk: object) -> str:
    if isinstance(hunk, str):
        return hunk
    if isinstance(hunk, list):
        return "\n".join(str(line) for line in hunk)
    if not isinstance(hunk, dict):
        raise _EditorFailure("invalid_schema", "each hunk must be a string, list, or object")
    lines = hunk.get("lines")
    if isinstance(lines, list):
        return "\n".join(str(line) for line in lines)
    old = hunk.get("old")
    new = hunk.get("new")
    if old is not None and new is not None:
        return _replacement_hunk(str(old), str(new))
    old_lines = hunk.get("old_lines")
    new_lines = hunk.get("new_lines")
    if isinstance(old_lines, list) and isinstance(new_lines, list):
        return _replacement_hunk(
            "\n".join(str(line) for line in old_lines),
            "\n".join(str(line) for line in new_lines),
        )
    raise _EditorFailure(
        "invalid_schema",
        "hunk object requires lines, old/new, or old_lines/new_lines",
    )


def _replacement_hunk(old: str, new: str) -> str:
    lines = [f"-{line}" for line in old.splitlines()]
    lines.extend(f"+{line}" for line in new.splitlines())
    return "\n".join(lines)


def _validate_expected_files(operations: list[PatchOperation], expected_files: list[str]) -> None:
    if not expected_files:
        return
    normalized_expected = {_safe_path(path) for path in expected_files}
    touched: set[str] = set()
    for operation in operations:
        touched.add(_safe_path(operation.path))
        if operation.destination:
            touched.add(_safe_path(operation.destination))
    unexpected = sorted(touched - normalized_expected)
    if unexpected:
        raise _EditorFailure(
            "invalid_schema",
            "operation touched files outside expected_files: " + ", ".join(unexpected),
        )


def _build_patch(
    workspace: EditorWorkspace,
    operations: list[PatchOperation],
) -> tuple[str, str, list[CommandResult], str]:
    commands: list[CommandResult] = []
    diff_parts: list[str] = []
    undo_parts: list[str] = []
    snippets: list[str] = []
    for operation in operations:
        diff, undo_diff, operation_commands, snippet = _operation_patch(workspace, operation)
        diff_parts.append(diff)
        undo_parts.insert(0, undo_diff)
        commands.extend(operation_commands)
        snippets.append(snippet)
    return "\n".join(diff_parts), "\n".join(undo_parts), commands, "\n\n".join(snippets)


def _operation_patch(
    workspace: EditorWorkspace,
    operation: PatchOperation,
) -> tuple[str, str, list[CommandResult], str]:
    path = _safe_edit_path(operation.path)
    if operation.type == "create_file":
        return _create_file_patch(workspace, path, operation.content)
    if operation.type == "update_file":
        return _update_file_patch(workspace, path, operation.content, operation.diff)
    if operation.type == "delete_file":
        return _delete_file_patch(workspace, path)
    if operation.type == "move_file":
        destination = _safe_edit_path(operation.destination or "")
        return _move_file_patch(workspace, path, destination)
    raise _EditorFailure("invalid_schema", f"unsupported patch operation type: {operation.type}")


def _create_file_patch(
    workspace: EditorWorkspace,
    path: str,
    content: str | None,
) -> tuple[str, str, list[CommandResult], str]:
    if content is None:
        raise _EditorFailure("invalid_schema", "create_file requires content")
    exists = workspace.run(f"test ! -e {shlex.quote(path)}")
    if exists.exit_code != 0:
        raise _EditorFailure(
            "file_exists", f"refusing to overwrite existing file: {path}", commands=(exists,)
        )
    diff = _create_file_diff(path, content)
    undo = _delete_file_diff(path, content)
    return diff, undo, [exists], f"Created {path}.\n\n{_snippet_around_line(content, 1)}"


def _update_file_patch(
    workspace: EditorWorkspace,
    path: str,
    content: str | None,
    patch_text: str | None,
) -> tuple[str, str, list[CommandResult], str]:
    original, read = _read_text_file(workspace, path)
    if content is not None:
        updated = content if content.endswith("\n") else f"{content}\n"
    elif patch_text:
        updated = _apply_structured_patch(original, patch_text, path)
    else:
        raise _EditorFailure(
            "invalid_schema", "update_file requires diff or content", commands=(read,)
        )
    diff = _unified_diff(path, original, updated)
    undo = _unified_diff(path, updated, original)
    return diff, undo, [read], f"Updated {path}.\n\n{_first_changed_snippet(original, updated)}"


def _delete_file_patch(
    workspace: EditorWorkspace,
    path: str,
) -> tuple[str, str, list[CommandResult], str]:
    original, read = _read_text_file(workspace, path)
    diff = _delete_file_diff(path, original)
    undo = _create_file_diff(path, original)
    return diff, undo, [read], f"Deleted {path}."


def _move_file_patch(
    workspace: EditorWorkspace,
    source: str,
    destination: str,
) -> tuple[str, str, list[CommandResult], str]:
    original, read = _read_text_file(workspace, source)
    exists = workspace.run(f"test ! -e {shlex.quote(destination)}")
    if exists.exit_code != 0:
        raise _EditorFailure(
            "file_exists",
            f"refusing to overwrite existing file: {destination}",
            commands=(read, exists),
        )
    diff = "\n".join(
        [_delete_file_diff(source, original), _create_file_diff(destination, original)]
    )
    undo = "\n".join(
        [_delete_file_diff(destination, original), _create_file_diff(source, original)]
    )
    return diff, undo, [read, exists], f"Moved {source} to {destination}."


def _read_text_file(workspace: EditorWorkspace, path: str) -> tuple[str, CommandResult]:
    read = workspace.run(f"cat -- {shlex.quote(path)}")
    if read.exit_code != 0:
        output = read.stderr or read.stdout or f"file not found: {path}"
        raise _EditorFailure("file_not_found", output, output=output, commands=(read,))
    if "\x00" in read.stdout:
        raise _EditorFailure(
            "binary_file", f"refusing to edit binary file: {path}", commands=(read,)
        )
    return read.stdout, read


def _apply_structured_patch(original: str, patch_text: str, expected_path: str) -> str:
    hunks = _parse_structured_hunks(patch_text, expected_path)
    updated_lines = original.splitlines()
    trailing_newline = original.endswith("\n")
    for hunk in hunks:
        old_lines, new_lines = hunk
        matches = _find_exact_matches(updated_lines, old_lines)
        if not matches:
            raise _EditorFailure(
                "context_mismatch",
                f"patch context did not match {expected_path}",
                output=_closest_context_output(updated_lines, old_lines),
            )
        if len(matches) > 1:
            raise _EditorFailure(
                "ambiguous_match",
                f"patch context matched multiple locations in {expected_path}",
                output=_ambiguous_context_output(updated_lines, matches),
            )
        start = matches[0]
        updated_lines = [
            *updated_lines[:start],
            *new_lines,
            *updated_lines[start + len(old_lines) :],
        ]
    text = "\n".join(updated_lines)
    if trailing_newline or text:
        text += "\n"
    return text


def _parse_structured_hunks(
    patch_text: str, expected_path: str
) -> list[tuple[list[str], list[str]]]:
    lines = patch_text.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise _EditorFailure(
            "patch_parse_error",
            "structured patch must start with *** Begin Patch and end with *** End Patch",
        )
    current_path: str | None = None
    hunk_lines: list[str] = []
    hunks: list[tuple[list[str], list[str]]] = []
    in_hunk = False
    for line in lines[1:-1]:
        if line.startswith("*** Update File: "):
            current_path = _safe_path(line.removeprefix("*** Update File: ").strip())
            if current_path != expected_path:
                raise _EditorFailure(
                    "invalid_schema",
                    f"operation path {expected_path} does not match patch path {current_path}",
                )
            continue
        if line.startswith("@@"):
            if in_hunk and hunk_lines:
                hunks.append(_hunk_to_lines(hunk_lines))
            in_hunk = True
            hunk_lines = []
            continue
        if not in_hunk:
            if line.strip():
                raise _EditorFailure("patch_parse_error", "patch content must be inside @@ hunks")
            continue
        hunk_lines.append(line)
    if in_hunk and hunk_lines:
        hunks.append(_hunk_to_lines(hunk_lines))
    if current_path is None:
        raise _EditorFailure("patch_parse_error", "structured patch must include *** Update File")
    if not hunks:
        raise _EditorFailure(
            "patch_parse_error", "structured patch must include at least one @@ hunk"
        )
    return hunks


def _hunk_to_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    old_lines: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith("+"):
            new_lines.append(line[1:])
        elif line == "":
            old_lines.append("")
            new_lines.append("")
        else:
            raise _EditorFailure("patch_parse_error", "hunk lines must start with space, -, or +")
    if old_lines == new_lines:
        raise _EditorFailure("patch_parse_error", "hunk does not change content")
    return old_lines, new_lines


def _find_exact_matches(lines: list[str], needle: list[str]) -> list[int]:
    if not needle:
        return [0] if not lines else list(range(len(lines) + 1))
    end = len(lines) - len(needle) + 1
    return [index for index in range(max(0, end)) if lines[index : index + len(needle)] == needle]


def _safe_edit_path(path: str) -> str:
    safe_path = _safe_path(path)
    rejection = generated_path_rejection(safe_path)
    if rejection:
        raise _EditorFailure("generated_file_rejected", rejection)
    return safe_path


def _safe_path(path: str) -> str:
    normalized = str(PurePosixPath((path or "").strip()))
    if normalized in {"", "."}:
        raise _EditorFailure("unsafe_path", "path must name a file")
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise _EditorFailure("unsafe_path", f"path must be relative to workspace: {path}")
    if "/../" in normalized:
        raise _EditorFailure("unsafe_path", f"path must not escape workspace: {path}")
    return normalized


def generated_path_rejection(path: str) -> str | None:
    lowered = path.lower()
    markers = (
        "__pycache__/",
        ".pytest_cache/",
        "node_modules/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".tox/",
        ".nox/",
        "dist/",
        "build/",
        ".egg-info/",
    )
    suffixes = (".pyc", ".pyo", ".tmp", ".temp", ".log")
    if lowered.startswith(("tmp/", "temp/")):
        return f"refusing generated or temporary path: {path}"
    if any(marker in lowered for marker in markers):
        return f"refusing generated, dependency, or cache path: {path}"
    if lowered.endswith(suffixes):
        return f"refusing generated or temporary file: {path}"
    return None


def _edit_size_too_large(diff: str) -> bool:
    return len(diff.encode("utf-8")) > 200_000 or len(diff.splitlines()) > 5_000


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
    body = "".join(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{path}"))
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\n{body}"


def _delete_file_diff(path: str, content: str) -> str:
    lines = content.splitlines(keepends=True)
    if content and not content.endswith("\n"):
        lines.append("\n")
    body = "".join(difflib.unified_diff(lines, [], fromfile=f"a/{path}", tofile="/dev/null"))
    return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n{body}"


def _first_changed_snippet(old: str, new: str) -> str:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    line_number = 1
    for index, line in enumerate(new_lines, start=1):
        if index > len(old_lines) or old_lines[index - 1] != line:
            line_number = index
            break
    return _snippet_around_line(new, line_number)


def _snippet_around_line(text: str, line_number: int, radius: int = 3) -> str:
    lines = text.splitlines()
    if not lines:
        return "Snippet: <empty file>"
    center = min(max(1, line_number), len(lines))
    start = max(1, center - radius)
    end = min(len(lines), center + radius)
    body = "\n".join(f"{index:>6}\t{lines[index - 1]}" for index in range(start, end + 1))
    return f"Snippet {start}-{end}:\n{body}"


def _closest_context_output(lines: list[str], old_lines: list[str]) -> str:
    if not lines:
        return "Current file is empty."
    needle = old_lines[0] if old_lines else ""
    for index, line in enumerate(lines, start=1):
        if needle and needle in line:
            return _snippet_around_line("\n".join(lines) + "\n", index)
    return _snippet_around_line("\n".join(lines) + "\n", 1)


def _ambiguous_context_output(lines: list[str], matches: list[int]) -> str:
    shown = matches[:3]
    snippets = [
        _snippet_around_line("\n".join(lines) + "\n", index + 1, radius=1) for index in shown
    ]
    return "Ambiguous matches near:\n\n" + "\n\n".join(snippets)


def _cap_output(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[output truncated]"


def _review_notes(
    rationale: str,
    expected_files: list[str] | None,
    *,
    passed: bool = False,
) -> str:
    parts = [
        "unified editor applied structured operations"
        if passed
        else "unified editor rejected structured operations"
    ]
    if rationale.strip():
        parts.append("rationale=" + rationale.strip()[:300])
    if expected_files:
        parts.append("expected_files=" + ", ".join(expected_files[:20]))
    return "; ".join(parts)


def _recovery_kind_for_error(kind: str) -> str:
    if kind in {
        "patch_parse_error",
        "context_mismatch",
        "ambiguous_match",
        "internal_editor_error",
    }:
        return "patch_failure"
    if kind in {"unsafe_path", "invalid_schema"}:
        return "invalid_tool_arguments"
    return "submit_review_failed" if kind == "generated_file_rejected" else "tool_failure"
