from __future__ import annotations

import json

from contribarena.config.schema import RunConfig


RECOVERY_TEMPLATES: dict[str, str] = {
    "format_error": (
        "If a tool call is malformed, missing required arguments, has unexpected "
        "arguments, or tries to call more than one tool, make one corrected tool call "
        "only. Do not continue as if the failed action happened."
    ),
    "blocked_command": (
        "If a command is blocked or unavailable, inspect the smallest useful local "
        "context, try one narrower alternative when safe, or return blocked with the "
        "specific command and reason."
    ),
    "command_timeout": (
        "If a command times out, rerun only a narrower focused command or classify the "
        "timeout as a blocker. Do not broaden setup or repeat the same long command."
    ),
    "too_large_output": (
        "If output is truncated or too broad, narrow by path, filename, symbol, or line "
        "window before asking for more context."
    ),
    "patch_failure": (
        "If aci_apply_patch returns patch_parse_error, context_mismatch, or "
        "ambiguous_match, view the exact target lines and retry with a smaller "
        "structured operation; use aci_undo if the workspace got worse."
    ),
    "submit_review_failed": (
        "If submit-time review rejects generated or cache files, call aci_clean_generated, "
        "rerun focused verification when needed, then submit again. For other review "
        "failures, fix the named issue or return blocked."
    ),
}


def build_goal_prompt(config: RunConfig) -> str:
    if config.issue is not None:
        return _build_issue_solving_prompt(config)
    if config.discovery.candidates:
        candidate = config.discovery.candidates[0]
        clone_url = _clone_url(str(candidate.url))
        clone_command = _clone_command(clone_url)
        target = (
            f"Target repository: {candidate.owner}/{candidate.repo}\n"
            f"Clone URL: {clone_url}\n\n"
            "Required sequence:\n"
            "1. Call repo_search() with no arguments to load the configured candidates.\n"
            f"2. Call repo_check_eligibility(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"3. Call repo_get_metadata(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"4. Call repo_get_issues(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            "5. Call workspace_run to clone the repo, using this command exactly:\n"
            f"   {clone_command}\n"
            "6. Use ACI tools against paths under repo/ to inspect and search; use aci_apply_patch as the primary edit tool for the smallest useful change.\n"
            "7. If the verification command is not obvious, call aci_suggest_verification(path='repo'); then use aci_verify.\n"
            "8. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
            "9. Briefly state what happened; the harness derives the final run result from captured evidence.\n"
        )
    else:
        query = config.discovery.query or ""
        filters_json = json.dumps(config.discovery.filters.model_dump(exclude_none=True))
        target = (
            "No fixed repository candidate is configured.\n\n"
            "Required sequence:\n"
            f"1. Call repo_search(query='{query}', filters_json='{filters_json}') and choose one low-risk repository.\n"
            "2. Call repo_check_eligibility(owner=<chosen_owner>, repo=<chosen_repo>).\n"
            "3. If the repository is not eligible, choose another repository from the search results.\n"
            "4. Call repo_get_metadata(owner=<chosen_owner>, repo=<chosen_repo>).\n"
            "5. Call repo_get_issues(owner=<chosen_owner>, repo=<chosen_repo>).\n"
            "6. Call workspace_run to clone the repo into repo/. Use a GitHub HTTPS clone URL and prefer "
            "`git -c http.version=HTTP/1.1 clone --depth 1 <url> repo`.\n"
            "7. Use ACI tools against paths under repo/ to inspect and search; use aci_apply_patch as the primary edit tool for the smallest useful change.\n"
            "8. If the verification command is not obvious, call aci_suggest_verification(path='repo'); then use aci_verify.\n"
            "9. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
            "10. Briefly state what happened; the harness derives the final run result from captured evidence.\n"
        )
    if config.run.mode == "owned_live":
        run_label = "M0.4 owned-live autonomous contributor run"
        task_source = (
            "The selected task must be autonomously discovered inside the configured owned "
            "repository from issues, docs, code, tests, TODOs, or project plans. "
        )
        live_boundary = (
            "Do not directly open a PR, push branches, or write GitHub comments; the harness "
            "will perform governed live writes after your submitted patch passes review. "
        )
    elif config.run.mode == "external_live":
        run_label = "M0.5 external-live autonomous contributor run"
        task_source = (
            "The selected task must require a code or test change. Do not choose a docs-only, "
            "README-only, typo-only, or prose-only follow-up for this run. "
        )
        live_boundary = (
            "Do not directly open a PR, push branches, or write GitHub comments; the harness "
            "will perform fork-only governed live writes after your submitted patch passes review. "
        )
    else:
        run_label = "M0.2.1 end-to-end shadow run"
        task_source = (
            "The selected task may be a low-risk follow-up identified from metadata, issues, or repository layout. "
        )
        live_boundary = "Do not open a PR, write GitHub comments, or perform live GitHub writes. "
    return (
        f"Complete this {run_label} with the shortest valid tool sequence.\n\n"
        f"{target}\n"
        f"{task_source}"
        f"{live_boundary}"
        "Prefer ACI tools over raw shell editing: aci_find_files for file discovery, aci_view for bounded reading, "
        "aci_search for bounded text search, aci_apply_patch for create_file/update_file/delete_file/move_file edits "
        "with operations_json as a JSON list, "
        "aci_undo when an edit needs to be reverted, aci_suggest_verification when test commands are unclear, "
        "aci_verify for focused checks, aci_clean_generated for generated/cache cleanup, "
        "and aci_submit_patch to finish without staging or committing. "
        'Minimal aci_apply_patch example: operations_json=[{"type":"update_file","path":"repo/app.py","diff":"*** Begin Patch\\n*** Update File: repo/app.py\\n@@\\n old context\\n-old line\\n+new line\\n*** End Patch"}]. '
        "Do not edit files through workspace_run, shell redirection, sed, python scripts, or git commands; "
        "those edits lack unified-editor provenance and submit-time review will reject them. "
        "Call aci_runtime_get_context(scope='run') early; it returns guidance availability, goal context, memory hints, and tracked PR summaries. "
        "Treat the long-term goal as direction, not as permission to ignore this run's concrete task. Use aci_goal_update only for the single short-term goal; mark it complete only after current evidence proves the objective is done. If aci_goal_update returns terminal_status=goal_abandon_limit, end this run with a final structured blocked result. "
        "If guidance is available, read the returned path relative to the workspace root, not repo/. "
        "If tracked PRs or external-write memory hints are present, inspect them before opening duplicate or conflicting work. "
        "Before editing, check repository-local guidance such as AGENTS.md, CONTRIBUTING.md, or .github templates when present. "
        "If a tool output is truncated or too broad, narrow the query. If a command is missing or the environment is blocked, "
        "record the blocker instead of making broad setup changes."
        "\n\nRecovery templates:\n"
        f"{_recovery_template_text()}"
    )


def _build_issue_solving_prompt(config: RunConfig) -> str:
    issue = config.issue
    if issue is None:
        raise ValueError("issue-solving prompt requires config.issue")
    if not config.discovery.candidates:
        raise ValueError("issue-solving mode requires a fixed discovery candidate")
    candidate = config.discovery.candidates[0]
    clone_url = str(issue.clone_url) if issue.clone_url else _clone_url(str(candidate.url))
    clone_command = _clone_command(clone_url)
    source = f"\nSource URL: {issue.source_url}" if issue.source_url else ""
    reproduction_hint = (
        f"\nReproduction hint: {issue.reproduction_hint}" if issue.reproduction_hint else ""
    )
    verification_hint = (
        f"\nVerification hint: {issue.verification_hint}" if issue.verification_hint else ""
    )
    title = issue.title or "Configured problem statement"
    return (
        "Complete this M0.2.2 issue-solving shadow run. Your job is to address the "
        "explicit problem statement, not to self-select a typo, docs cleanup, or unrelated "
        "low-risk task.\n\n"
        f"Target repository: {candidate.owner}/{candidate.repo}\n"
        f"Clone URL: {clone_url}\n"
        f"Issue title: {title}{source}{reproduction_hint}{verification_hint}\n\n"
        "Problem statement:\n"
        f"{issue.problem_statement.strip()}\n\n"
        "Required sequence:\n"
        "1. Call repo_search() with no arguments to load the configured candidate.\n"
        f"2. Call repo_check_eligibility(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        f"3. Call repo_get_metadata(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        f"4. Call repo_get_issues(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        "5. Call workspace_run to clone the repo, using this command exactly:\n"
        f"   {clone_command}\n"
        "6. Restate the problem briefly in your own words, then inspect the smallest relevant files using ACI tools.\n"
        "7. Reproduce the failure when practical, or write explicit reproduction notes when the issue is directly inspectable.\n"
        "8. Make the smallest code or test/docs change that directly addresses the problem statement using aci_apply_patch as the primary edit tool. Do not make unrelated cleanup.\n"
        "9. Use aci_suggest_verification(path='repo') if the focused verification command is not obvious, then run aci_verify.\n"
        "10. If verification is blocked by missing dependencies, timeout, or unavailable tests, record that blocker explicitly instead of broad setup churn.\n"
        "11. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
        "12. Return the final structured ContribArena result with problem_statement_summary, reproduction_notes, verification_summary, and blockers.\n\n"
        "Completion rule: status may be completed only if the submitted patch directly addresses "
        "the problem statement and at least one local verification command succeeded. Otherwise "
        "return blocked or failed with explicit reasons. Call aci_runtime_get_context(scope='run') early; it returns guidance availability, goal context, memory hints, and tracked PR summaries. "
        "Treat the long-term goal as direction, not as permission to ignore this issue. Use aci_goal_update only for the single short-term goal; mark it complete only after current evidence proves the objective is done. If aci_goal_update returns terminal_status=goal_abandon_limit, end this run with a final structured blocked result. "
        "If guidance is available, read the returned path relative to the workspace root, not repo/. If tracked PRs or external-write memory hints are present, inspect them before opening duplicate or conflicting work. Then check and follow repository-local guidance such as AGENTS.md, CONTRIBUTING.md, or .github templates when present. Shadow mode means no GitHub writes."
        "\n\nRecovery templates:\n"
        f"{_recovery_template_text()}"
    )


def _clone_url(url: str) -> str:
    if url.startswith("https://github.com/") and not url.endswith(".git"):
        return f"{url}.git"
    return url


def _clone_command(clone_url: str) -> str:
    return (
        "rm -rf repo && "
        "for attempt in 1 2 3; do "
        f"git -c http.version=HTTP/1.1 clone --depth 1 {clone_url} repo && break; "
        "rm -rf repo; sleep 2; "
        "done && test -d repo/.git && cd repo && git status --short && ls -la"
    )


def _recovery_template_text() -> str:
    return "\n".join(f"- {name}: {text}" for name, text in RECOVERY_TEMPLATES.items())
