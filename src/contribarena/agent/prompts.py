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
            "1. Call repo_search() to load the configured candidates.\n"
            f"2. Call repo_check_eligibility(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"3. Call repo_get_metadata(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            f"4. Call repo_get_issues(owner='{candidate.owner}', repo='{candidate.repo}').\n"
            "5. Call workspace_run once to clone and inspect the repo, using this command exactly:\n"
            f"   git clone {clone_url} repo && cd repo && git status --short && ls -la\n"
            "6. Return the final structured completion result. Do not keep exploring after the clone command succeeds.\n"
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
            "6. Call workspace_run once to clone and inspect the repo. Use a GitHub HTTPS clone URL.\n"
            "7. Return the final structured completion result. Do not keep exploring after the clone command succeeds.\n"
        )
    return (
        "Complete this M0.1 shadow run with the shortest valid tool sequence.\n\n"
        f"{target}\n"
        "The selected task may be a low-risk follow-up identified from metadata, issues, or repository layout. "
        "Do not open a PR, write GitHub comments, or perform live GitHub writes."
    )


def _clone_url(url: str) -> str:
    if url.startswith("https://github.com/") and not url.endswith(".git"):
        return f"{url}.git"
    return url
