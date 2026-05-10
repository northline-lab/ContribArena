from __future__ import annotations

import json
import subprocess
from typing import Any

from contribarena.config.schema import RepoCandidate


def repo_get_metadata(candidate: RepoCandidate) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                candidate.full_name,
                "--json",
                "name,description,defaultBranchRef,languages,stargazerCount",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        result = None
    if result is None or result.returncode != 0:
        return {
            "owner": candidate.owner,
            "repo": candidate.repo,
            "description": "",
            "default_branch": candidate.branch or "main",
            "languages": [],
            "stars": 0,
            "open_issues": 0,
            "fallback": True,
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "owner": candidate.owner,
            "repo": candidate.repo,
            "description": "",
            "default_branch": candidate.branch or "main",
            "languages": [],
            "stars": 0,
            "open_issues": 0,
            "fallback": True,
        }
