from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from contribarena.config.schema import RepoCandidate
from contribarena.models import EligibilityResult
from contribarena.tools.github_client import GitHubClient, repo_api_path
from contribarena.tools.repo_metadata import repo_get_metadata


def repo_check_eligibility(candidate: RepoCandidate) -> EligibilityResult:
    checks: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    metadata = repo_get_metadata(candidate)
    checks.append("activity")
    if metadata.last_push:
        pushed_at = _parse_github_datetime(metadata.last_push)
        if pushed_at and pushed_at < datetime.now(UTC) - timedelta(days=90):
            reasons.append("last push is older than 90 days")
    else:
        warnings.append("could not verify last push date")

    client = GitHubClient()
    checks.append("contribution_acceptance")
    merged_prs = _merged_external_pr_count(client, candidate)
    if merged_prs == 0:
        reasons.append("no merged external contributor PR found in the past 6 months")

    checks.append("code_size")
    estimated_loc = _estimated_loc(client, candidate)
    if estimated_loc is None:
        warnings.append("could not estimate repository size")
    elif estimated_loc > 50_000:
        reasons.append(f"estimated repository size exceeds 50k LOC: {estimated_loc}")

    checks.append("contribution_norms")
    docs = _read_policy_docs(client, candidate)
    if not docs:
        warnings.append("no README/CONTRIBUTING policy text could be read")
    elif not _looks_english(docs):
        reasons.append("repository policy text does not appear to be English")

    checks.append("bot_policy")
    if _prohibits_ai_or_bots(docs):
        reasons.append("repository policy appears to prohibit bot or AI contributions")

    return EligibilityResult(
        eligible=not reasons,
        reasons=reasons or ["eligible for M0.1 shadow-mode inspection"],
        warnings=warnings,
        checks_performed=checks,
    )


def _merged_external_pr_count(client: GitHubClient, candidate: RepoCandidate) -> int:
    since = datetime.now(UTC) - timedelta(days=180)
    gh_response = client.gh_json(
        [
            "pr",
            "list",
            "--repo",
            candidate.full_name,
            "--state",
            "merged",
            "--limit",
            "100",
            "--json",
            "author,mergedAt",
        ]
    )
    if gh_response.ok:
        return sum(
            1 for item in gh_response.data or [] if _is_external_recent_pr(item, candidate, since)
        )

    rest_response = client.rest_json(
        "GET",
        repo_api_path(candidate.owner, candidate.repo, "pulls"),
        params={"state": "closed", "per_page": 100, "sort": "updated", "direction": "desc"},
    )
    if not rest_response.ok:
        return 0
    return sum(
        1 for item in rest_response.data or [] if _is_external_recent_pr(item, candidate, since)
    )


def _is_external_recent_pr(item: dict[str, Any], candidate: RepoCandidate, since: datetime) -> bool:
    merged_at = _parse_github_datetime(str(item.get("mergedAt") or item.get("merged_at") or ""))
    if not merged_at or merged_at < since:
        return False
    author = item.get("author") or item.get("user") or {}
    login = str(author.get("login") or "") if isinstance(author, dict) else ""
    if not login:
        return False
    return login.lower() != candidate.owner.lower() and not login.endswith("[bot]")


def _estimated_loc(client: GitHubClient, candidate: RepoCandidate) -> int | None:
    response = client.rest_json("GET", repo_api_path(candidate.owner, candidate.repo, "languages"))
    if not response.ok or not isinstance(response.data, dict):
        return None
    total_bytes = sum(value for value in response.data.values() if isinstance(value, int))
    return total_bytes // 50


def _read_policy_docs(client: GitHubClient, candidate: RepoCandidate) -> str:
    bodies = []
    for path in ["README.md", "CONTRIBUTING.md", ".github/CONTRIBUTING.md", "CODE_OF_CONDUCT.md"]:
        response = client.rest_text(
            repo_api_path(candidate.owner, candidate.repo, f"contents/{path}")
        )
        if response.ok and response.data:
            bodies.append(str(response.data))
    return "\n\n".join(bodies)


def _looks_english(text: str) -> bool:
    if not text.strip():
        return False
    ascii_chars = sum(1 for char in text[:2000] if ord(char) < 128)
    return ascii_chars / min(len(text), 2000) > 0.85


def _prohibits_ai_or_bots(text: str) -> bool:
    lowered = text.lower()
    phrases = [
        "no ai generated",
        "ai-generated contributions are not accepted",
        "do not submit ai",
        "no bot contributions",
        "bot contributions are not accepted",
        "automated pull requests are not accepted",
    ]
    return any(phrase in lowered for phrase in phrases)


def _parse_github_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
