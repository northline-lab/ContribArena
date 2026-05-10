from __future__ import annotations

from typing import Any

from contribarena.config.schema import RepoCandidate, RepoSearchFilters, RunConfig
from contribarena.tools.github_client import GitHubClient


def repo_search(
    config: RunConfig, query: str = "", filters: object | None = None
) -> list[RepoCandidate]:
    search_query = query.strip() or config.discovery.query.strip()
    search_filters = _normalize_filters(filters, config.discovery.filters)

    if not search_query and search_filters.is_empty():
        return config.discovery.candidates

    client = GitHubClient()
    gh_response = client.gh_json(
        [
            "search",
            "repos",
            "--json",
            "fullName,description,stargazersCount,language,pushedAt",
            "--limit",
            "100",
            _build_query(search_query, search_filters),
        ]
    )
    if gh_response.ok:
        return [_candidate_from_gh(item) for item in gh_response.data or []]

    rest_response = client.rest_json(
        "GET",
        "/search/repositories",
        params={"q": _build_query(search_query, search_filters), "per_page": 100},
    )
    if rest_response.ok:
        return [_candidate_from_rest(item) for item in rest_response.data.get("items", [])]
    return []


def _normalize_filters(filters: object | None, default: RepoSearchFilters) -> RepoSearchFilters:
    if filters is None:
        return default
    if isinstance(filters, RepoSearchFilters):
        return filters
    if isinstance(filters, dict):
        merged: dict[str, Any] = default.model_dump(exclude_none=True)
        merged.update(filters)
        return RepoSearchFilters.model_validate(merged)
    return default


def _build_query(query: str, filters: RepoSearchFilters) -> str:
    parts = [query.strip()] if query.strip() else []
    if filters.language:
        parts.append(f"language:{filters.language}")
    if filters.stars_min is not None:
        parts.append(f"stars:>={filters.stars_min}")
    if filters.pushed_after:
        parts.append(f"pushed:>{filters.pushed_after}")
    if filters.topic:
        parts.append(f"topic:{filters.topic}")
    return " ".join(parts).strip() or "stars:>=1"


def _candidate_from_gh(item: dict[str, Any]) -> RepoCandidate:
    full_name = str(item.get("fullName") or "")
    owner, repo = _split_full_name(full_name)
    notes = _notes(
        description=item.get("description"),
        language=item.get("language"),
        stars=item.get("stargazersCount"),
        pushed_at=item.get("pushedAt"),
    )
    return RepoCandidate(
        owner=owner,
        repo=repo,
        url=f"https://github.com/{owner}/{repo}",
        notes=notes,
    )


def _candidate_from_rest(item: dict[str, Any]) -> RepoCandidate:
    full_name = str(item.get("full_name") or "")
    owner, repo = _split_full_name(full_name)
    notes = _notes(
        description=item.get("description"),
        language=item.get("language"),
        stars=item.get("stargazers_count"),
        pushed_at=item.get("pushed_at"),
    )
    return RepoCandidate(
        owner=owner,
        repo=repo,
        url=item.get("html_url") or f"https://github.com/{owner}/{repo}",
        notes=notes,
    )


def _split_full_name(full_name: str) -> tuple[str, str]:
    if "/" not in full_name:
        return "", full_name
    owner, repo = full_name.split("/", 1)
    return owner, repo


def _notes(
    description: object = None,
    language: object = None,
    stars: object = None,
    pushed_at: object = None,
) -> str | None:
    parts = []
    if description:
        parts.append(str(description))
    meta = []
    if language:
        meta.append(f"language={language}")
    if stars is not None:
        meta.append(f"stars={stars}")
    if pushed_at:
        meta.append(f"pushed_at={pushed_at}")
    if meta:
        parts.append("; ".join(meta))
    return " | ".join(parts) if parts else None
