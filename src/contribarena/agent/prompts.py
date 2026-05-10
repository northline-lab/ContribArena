from __future__ import annotations

from contribarena.config.schema import RunConfig


def build_goal_prompt(config: RunConfig) -> str:
    candidate = config.discovery.candidates[0]
    clone_url = str(candidate.url)
    if clone_url.startswith("https://github.com/") and not clone_url.endswith(".git"):
        clone_url = f"{clone_url}.git"
    return (
        "Complete this M0.0 run with the shortest valid tool sequence.\n\n"
        f"Target repository: {candidate.owner}/{candidate.repo}\n"
        f"Clone URL: {clone_url}\n\n"
        "Required sequence:\n"
        f"1. Call repo_check_eligibility(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        f"2. Call repo_get_metadata(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        f"3. Call repo_get_issues(owner='{candidate.owner}', repo='{candidate.repo}').\n"
        "4. Call workspace_run once to clone and inspect the repo, using this command exactly:\n"
        f"   git clone {clone_url} repo && cd repo && git status --short && ls -la\n"
        "5. Return the final structured completion result. Do not keep exploring after the clone command succeeds.\n\n"
        "The selected task may be a low-risk follow-up identified from metadata, issues, or repository layout. "
        "Do not open a PR, write GitHub comments, or perform live GitHub writes."
    )
