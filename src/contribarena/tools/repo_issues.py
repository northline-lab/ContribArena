from __future__ import annotations

import json
import subprocess
from typing import Any

from contribarena.config.schema import RepoCandidate


def repo_get_issues(candidate: RepoCandidate) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                candidate.full_name,
                "--json",
                "number,title,url,labels",
                "--limit",
                "20",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
