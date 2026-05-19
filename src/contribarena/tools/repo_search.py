from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from contribarena.config.schema import RepoCandidate, RepoSearchFilters, RunConfig
from contribarena.errors import ConfigError
from contribarena.tools.github_client import GitHubClient


@dataclass(frozen=True)
class RepoSearchResult:
    candidates: list[RepoCandidate]
    log_row: dict[str, object]


def repo_search(
    config: RunConfig, query: str = "", filters: object | None = None
) -> list[RepoCandidate]:
    return repo_search_with_log(config, query=query, filters=filters).candidates


def repo_search_with_log(
    config: RunConfig, query: str = "", filters: object | None = None
) -> RepoSearchResult:
    search_query = query.strip() or config.discovery.query.strip()
    search_filters = _normalize_filters(filters, config.discovery.filters)
    search_query, search_filters = _apply_season_discovery_defaults(
        config,
        search_query,
        search_filters,
    )

    if not search_query and search_filters.is_empty():
        candidates = _filter_by_season_profile(config, config.discovery.candidates)
        return RepoSearchResult(
            candidates=candidates,
            log_row=_log_row(
                config=config,
                query=search_query,
                filters=search_filters,
                github_query_string="fixed_candidates",
                total_hits=len(candidates),
                candidates=candidates,
            ),
        )

    client = GitHubClient()
    github_query = _build_query(search_query, search_filters)
    gh_response = client.gh_json(
        [
            "search",
            "repos",
            "--json",
            "fullName,description,stargazersCount,language,pushedAt",
            "--limit",
            "100",
            github_query,
        ]
    )
    if gh_response.ok:
        candidates = _filter_by_season_profile(
            config,
            [_candidate_from_gh(item) for item in gh_response.data or []],
        )
        return RepoSearchResult(
            candidates=candidates,
            log_row=_log_row(
                config=config,
                query=search_query,
                filters=search_filters,
                github_query_string=github_query,
                total_hits=len(gh_response.data or []),
                candidates=candidates,
            ),
        )

    rest_response = client.rest_json(
        "GET",
        "/search/repositories",
        params={"q": github_query, "per_page": 100},
    )
    if rest_response.ok:
        items = rest_response.data.get("items", [])
        candidates = _filter_by_season_profile(
            config,
            [_candidate_from_rest(item) for item in items],
        )
        return RepoSearchResult(
            candidates=candidates,
            log_row=_log_row(
                config=config,
                query=search_query,
                filters=search_filters,
                github_query_string=github_query,
                total_hits=int(rest_response.data.get("total_count") or len(items)),
                candidates=candidates,
            ),
        )
    return RepoSearchResult(
        candidates=[],
        log_row=_log_row(
            config=config,
            query=search_query,
            filters=search_filters,
            github_query_string=github_query,
            total_hits=0,
            candidates=[],
            error=rest_response.error or gh_response.error,
        ),
    )


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


def _apply_season_discovery_defaults(
    config: RunConfig,
    query: str,
    filters: RepoSearchFilters,
) -> tuple[str, RepoSearchFilters]:
    profile = config.season.discovery_profile if config.season is not None else None
    if profile is None:
        return query, filters
    resolved_query = query or (profile.seed_queries[0] if profile.seed_queries else "")
    updates: dict[str, object] = filters.model_dump(exclude_none=True)
    if profile.language_filter and not filters.language:
        updates["language"] = profile.language_filter[0]
    if profile.min_stars is not None and filters.stars_min is None:
        updates["stars_min"] = profile.min_stars
    if profile.topic_filter and not filters.topic:
        updates["topic"] = profile.topic_filter[0]
    return resolved_query, RepoSearchFilters.model_validate(updates)


def _filter_by_season_profile(
    config: RunConfig,
    candidates: list[RepoCandidate],
) -> list[RepoCandidate]:
    if config.season is None:
        return candidates
    profile = config.season.discovery_profile
    allowlist = set(profile.allowlist)
    denylist = set(profile.denylist)
    filtered = [candidate for candidate in candidates if candidate.full_name not in denylist]
    if profile.scope == "owned":
        if not allowlist:
            raise ConfigError("owned discovery profile requires allowlist")
        filtered = [candidate for candidate in filtered if candidate.full_name in allowlist]
    elif allowlist:
        filtered = [candidate for candidate in filtered if candidate.full_name in allowlist]
    return filtered


def _log_row(
    *,
    config: RunConfig,
    query: str,
    filters: RepoSearchFilters,
    github_query_string: str,
    total_hits: int,
    candidates: list[RepoCandidate],
    error: str = "",
) -> dict[str, object]:
    return {
        "ts": datetime.now(UTC).isoformat(),
        "season_id": config.run.season_id or (config.season.id if config.season else ""),
        "participant_id": config.run.participant_id or "",
        "query": query,
        "filters_resolved": filters.model_dump(mode="json", exclude_none=True),
        "github_query_string": github_query_string,
        "total_hits": total_hits,
        "returned_count": len(candidates),
        "candidates": [candidate.full_name for candidate in candidates],
        "error": error,
    }


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
