from __future__ import annotations

from typing import Any

from contribarena.config.schema import RepoCandidate
from contribarena.models import IssueCandidate
from contribarena.tools.github_client import GitHubClient, repo_api_path


def repo_get_issues(
    candidate: RepoCandidate,
    filters: dict[str, Any] | None = None,
    *,
    include_prs: bool = False,
) -> list[IssueCandidate]:
    normalized = filters or {}
    limit = int(normalized.get("limit") or 50)
    client = GitHubClient()
    args = [
        "issue",
        "list",
        "--repo",
        candidate.full_name,
        "--json",
        "number,title,body,url,labels,assignees,createdAt,updatedAt",
        "--limit",
        str(limit),
        "--state",
        "open",
    ]
    labels = normalized.get("labels")
    if labels:
        label_value = ",".join(labels) if isinstance(labels, list) else str(labels)
        args.extend(["--label", label_value])
    gh_response = client.gh_json(args)
    if gh_response.ok:
        issues = [_from_gh(item) for item in gh_response.data or []]
        return _apply_local_filters(issues, gh_response.data, normalized)

    params: dict[str, object] = {
        "state": "open",
        "per_page": min(limit, 100),
        "sort": "created",
        "direction": "desc",
    }
    if labels:
        params["labels"] = ",".join(labels) if isinstance(labels, list) else str(labels)
    rest_response = client.rest_json(
        "GET", repo_api_path(candidate.owner, candidate.repo, "issues"), params=params
    )
    if not rest_response.ok:
        return []
    issues = [
        _from_rest(item)
        for item in rest_response.data or []
        if isinstance(item, dict) and (include_prs or "pull_request" not in item)
    ]
    return _apply_local_filters(issues, rest_response.data, normalized)


def _from_gh(item: dict[str, Any]) -> IssueCandidate:
    return IssueCandidate(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        body=str(item.get("body") or ""),
        labels=_labels(item.get("labels")),
        created_at=_optional_str(item.get("createdAt")),
        updated_at=_optional_str(item.get("updatedAt")),
    )


def _from_rest(item: dict[str, Any]) -> IssueCandidate:
    return IssueCandidate(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        url=str(item.get("html_url") or ""),
        body=str(item.get("body") or ""),
        labels=_labels(item.get("labels")),
        created_at=_optional_str(item.get("created_at")),
        updated_at=_optional_str(item.get("updated_at")),
    )


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


def _has_assignee(items: object, number: int) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if isinstance(item, dict) and item.get("number") == number:
            return bool(item.get("assignee") or item.get("assignees"))
    return False


def _apply_local_filters(
    issues: list[IssueCandidate], raw_items: object, filters: dict[str, Any]
) -> list[IssueCandidate]:
    created_after = str(filters.get("created_after") or "")
    if created_after:
        issues = [
            issue
            for issue in issues
            if issue.created_at is not None and issue.created_at >= created_after
        ]
    if filters.get("no_assignee"):
        issues = [issue for issue in issues if not _has_assignee(raw_items, issue.number)]
    return issues


def _optional_str(value: object) -> str | None:
    return str(value) if value else None
