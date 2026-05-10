from __future__ import annotations

from contribarena.config.schema import RepoCandidate, RunConfig


def repo_search(
    config: RunConfig, query: str = "", filters: object | None = None
) -> list[RepoCandidate]:
    _ = query, filters
    return config.discovery.candidates
