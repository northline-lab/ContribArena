from __future__ import annotations

from dataclasses import dataclass

from contribarena.models import CiCheck, CiStatus
from contribarena.tools.github_client import GitHubClient, repo_api_path


@dataclass(frozen=True)
class PullRequestCreateResult:
    ok: bool
    number: int | None = None
    url: str = ""
    head_sha: str = ""
    error: str = ""
    source: str = ""


class GitHubPullRequestClient:
    def __init__(self, client: GitHubClient | None = None, token_env: str = "GITHUB_TOKEN") -> None:
        self.client = client or GitHubClient()
        self.token_env = token_env

    def authenticated_actor(self) -> str:
        response = self.client.rest_json("GET", "/user", token_env=self.token_env)
        if not response.ok or not isinstance(response.data, dict):
            return ""
        return str(response.data.get("login") or "")

    def open_pr(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestCreateResult:
        response = self.client.rest_json(
            "POST",
            repo_api_path(owner, repo, "pulls"),
            json_body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "maintainer_can_modify": True,
            },
            token_env=self.token_env,
        )
        if not response.ok:
            return PullRequestCreateResult(ok=False, error=response.error, source=response.source)
        if not isinstance(response.data, dict):
            return PullRequestCreateResult(
                ok=False,
                error="GitHub PR response was not a JSON object",
                source=response.source,
            )
        number = response.data.get("number")
        url = response.data.get("html_url") or response.data.get("url") or ""
        head = response.data.get("head")
        head_sha = head.get("sha") if isinstance(head, dict) else ""
        return PullRequestCreateResult(
            ok=True,
            number=int(number) if isinstance(number, int) else None,
            url=str(url),
            head_sha=str(head_sha or ""),
            source=response.source,
        )

    def get_check_runs(self, *, owner: str, repo: str, ref: str) -> CiStatus:
        response = self.client.rest_json(
            "GET",
            repo_api_path(owner, repo, f"commits/{ref}/check-runs"),
            token_env=self.token_env,
        )
        if not response.ok:
            return CiStatus(
                status="failure",
                source="github",
                checks=[
                    CiCheck(
                        name="github_check_runs",
                        status="failure",
                        details=response.error,
                    )
                ],
            )
        if not isinstance(response.data, dict):
            return CiStatus(
                status="failure",
                source="github",
                checks=[
                    CiCheck(
                        name="github_check_runs",
                        status="failure",
                        details="GitHub check-runs response was not a JSON object.",
                    )
                ],
            )
        raw_checks = response.data.get("check_runs")
        if not isinstance(raw_checks, list) or not raw_checks:
            return CiStatus(
                status="not_run",
                source="github",
                checks=[
                    CiCheck(
                        name="github_check_runs",
                        status="skipped",
                        details="No GitHub check runs were returned.",
                    )
                ],
            )
        checks = [_normalize_check_run(item) for item in raw_checks if isinstance(item, dict)]
        if not checks:
            return CiStatus(status="not_run", source="github")
        overall = "failure" if any(check.status == "failure" for check in checks) else "success"
        if all(check.status == "skipped" for check in checks):
            overall = "not_run"
        return CiStatus(status=overall, source="github", checks=checks)


def _normalize_check_run(item: dict[str, object]) -> CiCheck:
    name = str(item.get("name") or "github_check")
    status = str(item.get("status") or "")
    conclusion = str(item.get("conclusion") or "")
    if conclusion == "success":
        normalized = "success"
    elif conclusion in {"failure", "cancelled", "timed_out", "action_required", "neutral"}:
        normalized = "failure"
    elif status in {"queued", "in_progress", "requested", "waiting", "pending"}:
        normalized = "skipped"
    else:
        normalized = "skipped"
    details = item.get("details_url") or item.get("html_url") or conclusion or status
    return CiCheck(name=name, status=normalized, details=str(details or ""))
