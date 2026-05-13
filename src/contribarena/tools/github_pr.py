from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from contribarena.models import CiCheck, CiStatus
from contribarena.tools.github_client import GitHubClient, repo_api_path


LABEL_METADATA: dict[str, tuple[str, str]] = {
    "contribarena-live": ("0e8a16", "Opened by the ContribArena owned-live harness"),
    "contribarena-external-live": (
        "5319e7",
        "Opened by the ContribArena external-live harness",
    ),
    "issue-solving": ("1d76db", "ContribArena issue-solving run"),
    "risk-low": ("c2e0c6", "Low-risk contribution"),
    "risk-medium": ("fbca04", "Medium-risk contribution"),
    "risk-high": ("d93f0b", "High-risk contribution"),
}


@dataclass(frozen=True)
class PullRequestCreateResult:
    ok: bool
    number: int | None = None
    url: str = ""
    head_sha: str = ""
    error: str = ""
    source: str = ""


@dataclass(frozen=True)
class ForkEnsureResult:
    ok: bool
    owner: str = ""
    repo: str = ""
    full_name: str = ""
    url: str = ""
    created: bool = False
    error: str = ""
    source: str = ""


@dataclass(frozen=True)
class LabelOperationResult:
    ok: bool
    labels: list[str]
    error: str = ""
    source: str = ""
    status_code: int | None = None


@dataclass(frozen=True)
class PullRequestStatusResult:
    ok: bool
    number: int
    state: str = ""
    merged: bool = False
    url: str = ""
    title: str = ""
    head_sha: str = ""
    head_ref: str = ""
    base_ref: str = ""
    draft: bool = False
    comments: int = 0
    review_comments: int = 0
    error: str = ""
    source: str = ""
    status_code: int | None = None


@dataclass(frozen=True)
class PullRequestReviewSignal:
    author: str = ""
    state: str = ""
    body: str = ""
    submitted_at: str = ""
    url: str = ""


@dataclass(frozen=True)
class IssueCommentResult:
    ok: bool
    id: int | None = None
    url: str = ""
    error: str = ""
    source: str = ""
    status_code: int | None = None


class GitHubPullRequestClient:
    def __init__(self, client: GitHubClient | None = None, token_env: str = "GITHUB_TOKEN") -> None:
        self.client = client or GitHubClient()
        self.token_env = token_env

    def authenticated_actor(self) -> str:
        response = self.client.rest_json("GET", "/user", token_env=self.token_env)
        if not response.ok or not isinstance(response.data, dict):
            return ""
        return str(response.data.get("login") or "")

    def ensure_fork(
        self,
        *,
        owner: str,
        repo: str,
        fork_owner: str,
    ) -> ForkEnsureResult:
        existing = self.client.rest_json(
            "GET",
            repo_api_path(fork_owner, repo),
            token_env=self.token_env,
        )
        if existing.ok:
            return _fork_result(existing.data, created=False, source=existing.source)
        if "repo not found" not in existing.error:
            return ForkEnsureResult(ok=False, error=existing.error, source=existing.source)

        created = self.client.rest_json(
            "POST",
            repo_api_path(owner, repo, "forks"),
            token_env=self.token_env,
        )
        if not created.ok:
            return ForkEnsureResult(ok=False, error=created.error, source=created.source)
        result = _fork_result(created.data, created=True, source=created.source)
        if fork_owner and result.owner and result.owner != fork_owner:
            return ForkEnsureResult(
                ok=False,
                error=f"created fork owner {result.owner} does not match expected {fork_owner}",
                source=result.source,
            )
        return result

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

    def ensure_labels(self, *, owner: str, repo: str, labels: list[str]) -> LabelOperationResult:
        normalized = _normalize_labels(labels)
        for label in normalized:
            existing = self.client.rest_json(
                "GET",
                repo_api_path(owner, repo, f"labels/{quote(label, safe='')}"),
                token_env=self.token_env,
            )
            if existing.ok:
                continue
            if "repo not found" not in existing.error:
                return LabelOperationResult(
                    ok=False,
                    labels=normalized,
                    error=existing.error,
                    source=existing.source,
                    status_code=existing.status_code,
                )
            color, description = LABEL_METADATA.get(
                label, ("cfd3d7", "ContribArena run label")
            )
            created = self.client.rest_json(
                "POST",
                repo_api_path(owner, repo, "labels"),
                json_body={"name": label, "color": color, "description": description},
                token_env=self.token_env,
            )
            if not created.ok:
                return LabelOperationResult(
                    ok=False,
                    labels=normalized,
                    error=created.error,
                    source=created.source,
                    status_code=created.status_code,
                )
        return LabelOperationResult(ok=True, labels=normalized)

    def set_pr_labels(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> LabelOperationResult:
        normalized = _normalize_labels(labels)
        response = self.client.rest_json(
            "PUT",
            repo_api_path(owner, repo, f"issues/{issue_number}/labels"),
            json_body={"labels": normalized},
            token_env=self.token_env,
        )
        if not response.ok:
            return LabelOperationResult(
                ok=False,
                labels=normalized,
                error=response.error,
                source=response.source,
                status_code=response.status_code,
            )
        return LabelOperationResult(
            ok=True,
            labels=normalized,
            source=response.source,
            status_code=response.status_code,
        )

    def get_pr(self, *, owner: str, repo: str, number: int) -> PullRequestStatusResult:
        response = self.client.rest_json(
            "GET",
            repo_api_path(owner, repo, f"pulls/{number}"),
            token_env=self.token_env,
        )
        if not response.ok:
            return PullRequestStatusResult(
                ok=False,
                number=number,
                error=response.error,
                source=response.source,
                status_code=response.status_code,
            )
        if not isinstance(response.data, dict):
            return PullRequestStatusResult(
                ok=False,
                number=number,
                error="GitHub PR response was not a JSON object",
                source=response.source,
                status_code=response.status_code,
            )
        head = response.data.get("head")
        base = response.data.get("base")
        return PullRequestStatusResult(
            ok=True,
            number=number,
            state=str(response.data.get("state") or ""),
            merged=bool(response.data.get("merged") or False),
            url=str(response.data.get("html_url") or response.data.get("url") or ""),
            title=str(response.data.get("title") or ""),
            head_sha=str(head.get("sha") if isinstance(head, dict) else ""),
            head_ref=str(head.get("ref") if isinstance(head, dict) else ""),
            base_ref=str(base.get("ref") if isinstance(base, dict) else ""),
            draft=bool(response.data.get("draft") or False),
            comments=int(response.data.get("comments") or 0),
            review_comments=int(response.data.get("review_comments") or 0),
            source=response.source,
            status_code=response.status_code,
        )

    def list_reviews(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
    ) -> list[PullRequestReviewSignal]:
        response = self.client.rest_json(
            "GET",
            repo_api_path(owner, repo, f"pulls/{number}/reviews"),
            params={"per_page": 100},
            token_env=self.token_env,
        )
        if not response.ok or not isinstance(response.data, list):
            return []
        reviews: list[PullRequestReviewSignal] = []
        for item in response.data:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            author = str(user.get("login") if isinstance(user, dict) else "")
            reviews.append(
                PullRequestReviewSignal(
                    author=author,
                    state=str(item.get("state") or ""),
                    body=str(item.get("body") or ""),
                    submitted_at=str(item.get("submitted_at") or ""),
                    url=str(item.get("html_url") or ""),
                )
            )
        return reviews

    def create_issue_comment(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> IssueCommentResult:
        response = self.client.rest_json(
            "POST",
            repo_api_path(owner, repo, f"issues/{issue_number}/comments"),
            json_body={"body": body},
            token_env=self.token_env,
        )
        if not response.ok:
            return IssueCommentResult(
                ok=False,
                error=response.error,
                source=response.source,
                status_code=response.status_code,
            )
        if not isinstance(response.data, dict):
            return IssueCommentResult(
                ok=False,
                error="GitHub comment response was not a JSON object",
                source=response.source,
                status_code=response.status_code,
            )
        return IssueCommentResult(
            ok=True,
            id=response.data.get("id") if isinstance(response.data.get("id"), int) else None,
            url=str(response.data.get("html_url") or response.data.get("url") or ""),
            source=response.source,
            status_code=response.status_code,
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
            return self._empty_check_runs_status(owner=owner, repo=repo)
        checks = [_normalize_check_run(item) for item in raw_checks if isinstance(item, dict)]
        if not checks:
            return CiStatus(status="not_run", source="github")
        overall = "failure" if any(check.status == "failure" for check in checks) else "success"
        if all(check.status == "skipped" for check in checks):
            overall = "not_run"
        return CiStatus(status=overall, source="github", checks=checks)

    def _empty_check_runs_status(self, *, owner: str, repo: str) -> CiStatus:
        workflows = self.client.rest_json(
            "GET",
            repo_api_path(owner, repo, "actions/workflows"),
            token_env=self.token_env,
        )
        details = "No GitHub check runs were returned yet."
        if workflows.ok and isinstance(workflows.data, dict):
            raw_workflows = workflows.data.get("workflows")
            if isinstance(raw_workflows, list) and raw_workflows:
                details = f"No GitHub check runs were returned yet; workflows_configured={len(raw_workflows)}."
            elif isinstance(raw_workflows, list):
                details = "No GitHub Actions workflows are configured for this repository."
        elif not workflows.ok:
            details = f"No GitHub check runs were returned; workflow lookup failed: {workflows.error}"
        return CiStatus(
            status="not_run",
            source="github",
            checks=[
                CiCheck(
                    name="github_check_runs",
                    status="skipped",
                    details=details,
                )
            ],
        )


def _normalize_labels(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(label.strip() for label in labels if label.strip()))


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


def _fork_result(data: object, *, created: bool, source: str) -> ForkEnsureResult:
    if not isinstance(data, dict):
        return ForkEnsureResult(
            ok=False,
            error="GitHub fork response was not a JSON object",
            source=source,
        )
    owner = data.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else ""
    name = data.get("name") or ""
    full_name = data.get("full_name") or ""
    url = data.get("html_url") or data.get("url") or ""
    return ForkEnsureResult(
        ok=True,
        owner=str(owner_login or ""),
        repo=str(name or ""),
        full_name=str(full_name or ""),
        url=str(url or ""),
        created=created,
        source=source,
    )
