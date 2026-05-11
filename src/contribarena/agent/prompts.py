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
        "If an edit or patch fails, view the exact target lines, make one smaller edit, "
        "or use aci_undo if the workspace got worse."
    ),
    "submit_review_failed": (
        "If submit-time review rejects the patch, fix the named issue, rerun focused "
        "verification when needed, then submit again; otherwise return blocked."
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
            "6. Use ACI tools against paths under repo/ to inspect, search, and make the smallest useful change.\n"
            "7. If the verification command is not obvious, call aci_suggest_verification(path='repo'); then use aci_verify.\n"
            "8. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
            "9. Return the final structured completion result.\n"
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
            "7. Use ACI tools against paths under repo/ to inspect, search, and make the smallest useful change.\n"
            "8. If the verification command is not obvious, call aci_suggest_verification(path='repo'); then use aci_verify.\n"
            "9. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
            "10. Return the final structured completion result.\n"
        )
    return (
        "Complete this M0.2.1 end-to-end shadow run with the shortest valid tool sequence.\n\n"
        f"{target}\n"
        "The selected task may be a low-risk follow-up identified from metadata, issues, or repository layout. "
        "Do not open a PR, write GitHub comments, or perform live GitHub writes. "
        "Prefer ACI tools over raw shell editing: aci_find_files for file discovery, aci_view for bounded reading, "
        "aci_search for bounded text search, aci_replace or aci_insert for edits, aci_create for new files, "
        "aci_undo when an edit needs to be reverted, aci_suggest_verification when test commands are unclear, "
        "aci_verify for focused checks, and aci_submit_patch to finish without staging or committing. "
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
        "8. Make the smallest code or test/docs change that directly addresses the problem statement. Do not make unrelated cleanup.\n"
        "9. Use aci_suggest_verification(path='repo') if the focused verification command is not obvious, then run aci_verify.\n"
        "10. If verification is blocked by missing dependencies, timeout, or unavailable tests, record that blocker explicitly instead of broad setup churn.\n"
        "11. Call aci_submit_patch(path='repo') to capture the shadow patch; it includes new files, so do not run git add or git commit for submission. Only set no_command_verification_rationale when command verification is genuinely unavailable.\n"
        "12. Return the final structured ContribArena result with problem_statement_summary, reproduction_notes, verification_summary, and blockers.\n\n"
        "Completion rule: status may be completed only if the submitted patch directly addresses "
        "the problem statement and at least one local verification command succeeded. Otherwise "
        "return blocked or failed with explicit reasons. Shadow mode means no GitHub writes."
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
    return "\n".join(
        f"- {name}: {text}" for name, text in RECOVERY_TEMPLATES.items()
    )
