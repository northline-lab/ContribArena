from __future__ import annotations

from contribarena.config.schema import RepoCandidate
from contribarena.models import RepoMetadata
from contribarena.tools.github_client import GitHubClient, repo_api_path


def repo_get_metadata(candidate: RepoCandidate) -> RepoMetadata:
    client = GitHubClient()
    gh_response = client.gh_json(
        [
            "repo",
            "view",
            candidate.full_name,
            "--json",
            "name,description,stargazerCount,forkCount,primaryLanguage,pushedAt,createdAt,defaultBranchRef,url,openIssuesCount",
        ]
    )
    if gh_response.ok:
        return _from_gh(candidate, gh_response.data)

    rest_response = client.rest_json("GET", repo_api_path(candidate.owner, candidate.repo))
    if rest_response.ok:
        return _from_rest(candidate, rest_response.data)

    return RepoMetadata(
        owner=candidate.owner,
        repo=candidate.repo,
        full_name=candidate.full_name,
        url=str(candidate.url),
        description="",
        default_branch=candidate.branch or "main",
        fallback=True,
    )


def _from_gh(candidate: RepoCandidate, data: dict[str, object]) -> RepoMetadata:
    primary_language = data.get("primaryLanguage")
    if isinstance(primary_language, dict):
        language = str(primary_language.get("name") or "")
    else:
        language = str(primary_language or "")
    default_branch = data.get("defaultBranchRef")
    if isinstance(default_branch, dict):
        branch = str(default_branch.get("name") or candidate.branch or "main")
    else:
        branch = candidate.branch or "main"
    return RepoMetadata(
        owner=candidate.owner,
        repo=candidate.repo,
        full_name=candidate.full_name,
        url=str(data.get("url") or candidate.url),
        description=str(data.get("description") or ""),
        stars=int(data.get("stargazerCount") or 0),
        forks=int(data.get("forkCount") or 0),
        language=language,
        last_push=_optional_str(data.get("pushedAt")),
        created_at=_optional_str(data.get("createdAt")),
        open_issues=int(data.get("openIssuesCount") or 0),
        default_branch=branch,
    )


def _from_rest(candidate: RepoCandidate, data: dict[str, object]) -> RepoMetadata:
    return RepoMetadata(
        owner=candidate.owner,
        repo=candidate.repo,
        full_name=candidate.full_name,
        url=str(data.get("html_url") or candidate.url),
        description=str(data.get("description") or ""),
        stars=int(data.get("stargazers_count") or 0),
        forks=int(data.get("forks_count") or 0),
        language=str(data.get("language") or ""),
        last_push=_optional_str(data.get("pushed_at")),
        created_at=_optional_str(data.get("created_at")),
        open_issues=int(data.get("open_issues_count") or 0),
        default_branch=str(data.get("default_branch") or candidate.branch or "main"),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value else None
