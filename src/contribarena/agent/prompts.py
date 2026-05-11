from __future__ import annotations

import json

from contribarena.config.schema import RunConfig


def build_goal_prompt(config: RunConfig) -> str:
    if config.discovery.candidates:
        candidate = config.discovery.candidates[0]
        clone_url = _clone_url(str(candidate.url))
        target = (
            f"Target repository: {candidate.owner}/{candidate.repo}\n"
            f"Clone URL: {clone_url}\n\n"
            "Required sequence:\n"
            "1. Call repo_search() with no arguments to load the configured candidates.\n"
            f"2. Call repo_check_eligibility(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"3. Call repo_get_metadata(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"4. Call repo_get_issues(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            "5. Call workspace_run to clone the repo, using this command exactly:\n"
            f"   git -c http.version=HTTP/1.1 clone --depth 1 {clone_url} repo && cd repo && git status --short && ls -la\n"
            "6. Use ACI tools against paths under repo/ to inspect, search, and make the smallest useful change.\n"
            "7. Prefer aci_verify for the most relevant lightweight verification command.\n"
            "8. Call aci_submit_patch(path='repo') to capture the shadow patch.\n"
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
            "8. Prefer aci_verify for the most relevant lightweight verification command.\n"
            "9. Call aci_submit_patch(path='repo') to capture the shadow patch.\n"
            "10. Return the final structured completion result.\n"
        )
    return (
        "Complete this M0.2.1 end-to-end shadow run with the shortest valid tool sequence.\n\n"
        f"{target}\n"
        "The selected task may be a low-risk follow-up identified from metadata, issues, or repository layout. "
        "Do not open a PR, write GitHub comments, or perform live GitHub writes. "
        "Prefer ACI tools over raw shell editing: aci_find_files for file discovery, aci_view for bounded reading, "
        "aci_search for bounded text search, aci_replace or aci_insert for edits, aci_create for new files, "
        "aci_undo when an edit needs to be reverted, aci_verify for focused checks, and aci_submit_patch to finish. "
        "If a tool output is truncated or too broad, narrow the query. If a command is missing or the environment is blocked, "
        "record the blocker instead of making broad setup changes."
    )


def _clone_url(url: str) -> str:
    if url.startswith("https://github.com/") and not url.endswith(".git"):
        return f"{url}.git"
    return url
