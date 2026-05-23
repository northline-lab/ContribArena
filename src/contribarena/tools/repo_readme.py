from __future__ import annotations

from contribarena.config.schema import RepoCandidate
from contribarena.engine.operator_events import truncate_for_operator
from contribarena.models import RepoReadmeResult
from contribarena.tools.github_client import GitHubClient, repo_api_path


def repo_get_readme(candidate: RepoCandidate, max_chars: int = 6000) -> RepoReadmeResult:
    limit = max(500, min(max_chars, 20_000))
    client = GitHubClient()
    response = client.rest_text(repo_api_path(candidate.owner, candidate.repo, "readme"))
    if not response.ok:
        return RepoReadmeResult(
            full_name=candidate.full_name,
            success=False,
            error=response.error,
        )
    return RepoReadmeResult(
        full_name=candidate.full_name,
        success=True,
        content=truncate_for_operator(str(response.data or ""), limit),
    )
