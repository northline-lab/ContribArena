from __future__ import annotations

from typing import Any

from contribarena.config.schema import RepoCandidate
from contribarena.models import IssueLinkage, PullRequestCandidate
from contribarena.tools.github_client import GitHubClient, repo_api_path


def repo_get_open_prs(candidate: RepoCandidate, limit: int = 30) -> list[PullRequestCandidate]:
    return _list_prs(candidate, state="open", limit=limit)


def repo_get_recent_merged_prs(
    candidate: RepoCandidate, limit: int = 30
) -> list[PullRequestCandidate]:
    prs = _list_prs(candidate, state="closed", limit=limit)
    return [pr for pr in prs if pr.merged_at]


def repo_search_prs_by_title(
    candidate: RepoCandidate, query: str, limit: int = 20
) -> list[PullRequestCandidate]:
    needle = query.strip().lower()
    if not needle:
        return []
    combined = repo_get_open_prs(candidate, limit=limit) + repo_get_recent_merged_prs(
        candidate, limit=limit
    )
    return [pr for pr in combined if needle in pr.title.lower()][:limit]


def repo_get_issue_linkage(candidate: RepoCandidate, issue_number: int) -> IssueLinkage:
    client = GitHubClient()
    issue = _issue_payload(client, candidate, issue_number)
    comments = _issue_comments(client, candidate, issue_number)
    linked = [
        pr
        for pr in repo_get_open_prs(candidate, limit=50)
        + repo_get_recent_merged_prs(candidate, limit=50)
        if issue_number in pr.linked_issues or f"#{issue_number}" in pr.body
    ]
    return IssueLinkage(
        issue_number=issue_number,
        assignees=_assignees(issue),
        linked_prs=linked,
        recent_comments=comments[:5],
    )


def repo_get_pr_review_history(
    candidate: RepoCandidate, limit: int = 20
) -> list[dict[str, object]]:
    client = GitHubClient()
    reviews: list[dict[str, object]] = []
    for pr in repo_get_recent_merged_prs(candidate, limit=min(limit, 20)):
        response = client.rest_json(
            "GET",
            repo_api_path(candidate.owner, candidate.repo, f"pulls/{pr.number}/reviews"),
            params={"per_page": 20},
        )
        if response.ok:
            for item in response.data or []:
                if isinstance(item, dict):
                    reviews.append(
                        {
                            "pull_number": pr.number,
                            "state": item.get("state") or "",
                            "body": str(item.get("body") or "")[:1000],
                            "submitted_at": item.get("submitted_at") or "",
                        }
                    )
                    if len(reviews) >= limit:
                        return reviews
    return reviews


def _list_prs(candidate: RepoCandidate, *, state: str, limit: int) -> list[PullRequestCandidate]:
    limit = max(1, min(limit, 100))
    client = GitHubClient()
    gh_response = client.gh_json(
        [
            "pr",
            "list",
            "--repo",
            candidate.full_name,
            "--state",
            "open" if state == "open" else "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,state,author,body,labels,createdAt,updatedAt,mergedAt,isDraft,closingIssuesReferences",
        ]
    )
    if gh_response.ok:
        return [_from_gh(item) for item in gh_response.data or []]

    response = client.rest_json(
        "GET",
        repo_api_path(candidate.owner, candidate.repo, "pulls"),
        params={
            "state": state,
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        },
    )
    if not response.ok:
        return []
    return [_from_rest(item) for item in response.data or [] if isinstance(item, dict)]


def _from_gh(item: dict[str, Any]) -> PullRequestCandidate:
    author = item.get("author")
    return PullRequestCandidate(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        state=str(item.get("state") or ""),
        author=str(author.get("login") if isinstance(author, dict) else author or ""),
        body=str(item.get("body") or ""),
        labels=_labels(item.get("labels")),
        created_at=_optional_str(item.get("createdAt")),
        updated_at=_optional_str(item.get("updatedAt")),
        merged_at=_optional_str(item.get("mergedAt")),
        draft=bool(item.get("isDraft") or False),
        linked_issues=_linked_issues(item.get("closingIssuesReferences")),
    )


def _from_rest(item: dict[str, Any]) -> PullRequestCandidate:
    user = item.get("user")
    return PullRequestCandidate(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        url=str(item.get("html_url") or ""),
        state=str(item.get("state") or ""),
        author=str(user.get("login") if isinstance(user, dict) else ""),
        body=str(item.get("body") or ""),
        labels=[],
        created_at=_optional_str(item.get("created_at")),
        updated_at=_optional_str(item.get("updated_at")),
        merged_at=_optional_str(item.get("merged_at")),
        draft=bool(item.get("draft") or False),
    )


def _issue_payload(
    client: GitHubClient, candidate: RepoCandidate, issue_number: int
) -> dict[str, Any]:
    response = client.rest_json(
        "GET", repo_api_path(candidate.owner, candidate.repo, f"issues/{issue_number}")
    )
    return response.data if response.ok and isinstance(response.data, dict) else {}


def _issue_comments(
    client: GitHubClient, candidate: RepoCandidate, issue_number: int
) -> list[str]:
    response = client.rest_json(
        "GET",
        repo_api_path(candidate.owner, candidate.repo, f"issues/{issue_number}/comments"),
        params={"per_page": 20},
    )
    if not response.ok:
        return []
    return [
        str(item.get("body") or "")[:1000]
        for item in response.data or []
        if isinstance(item, dict)
    ]


def _labels(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            labels.append(str(item.get("name") or ""))
        elif item:
            labels.append(str(item))
    return [label for label in labels if label]


def _linked_issues(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    numbers: list[int] = []
    for item in raw:
        if isinstance(item, dict) and item.get("number"):
            numbers.append(int(item["number"]))
    return numbers


def _assignees(item: dict[str, Any]) -> list[str]:
    raw = item.get("assignees")
    if not isinstance(raw, list):
        return []
    return [str(user.get("login") or "") for user in raw if isinstance(user, dict)]


def _optional_str(value: object) -> str | None:
    return str(value) if value else None
