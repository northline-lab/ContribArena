from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class GitHubResponse:
    ok: bool
    data: Any = None
    error: str = ""
    source: str = ""


class GitHubClient:
    def gh_json(self, args: list[str]) -> GitHubResponse:
        try:
            result = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except FileNotFoundError:
            return GitHubResponse(ok=False, error="gh CLI not found", source="gh")
        except subprocess.TimeoutExpired:
            return GitHubResponse(ok=False, error="gh CLI timed out", source="gh")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return GitHubResponse(
                ok=False,
                error=classify_github_error(result.returncode, detail),
                source="gh",
            )
        try:
            return GitHubResponse(ok=True, data=json.loads(result.stdout or "null"), source="gh")
        except json.JSONDecodeError as exc:
            return GitHubResponse(ok=False, error=f"invalid gh JSON: {exc}", source="gh")

    def rest_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        token_env: str | None = None,
    ) -> GitHubResponse:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get(token_env) if token_env else github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = path if path.startswith("http") else f"https://api.github.com{path}"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            return GitHubResponse(ok=False, error=f"httpx request failed: {exc}", source="httpx")

        if response.status_code >= 400:
            return GitHubResponse(
                ok=False,
                error=classify_http_error(response.status_code, response.text),
                source="httpx",
            )
        try:
            return GitHubResponse(ok=True, data=response.json(), source="httpx")
        except json.JSONDecodeError as exc:
            return GitHubResponse(ok=False, error=f"invalid REST JSON: {exc}", source="httpx")

    def rest_text(self, path: str) -> GitHubResponse:
        headers: dict[str, str] = {"Accept": "application/vnd.github.raw"}
        token = github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.get(f"https://api.github.com{path}", headers=headers)
        except httpx.HTTPError as exc:
            return GitHubResponse(ok=False, error=f"httpx request failed: {exc}", source="httpx")
        if response.status_code >= 400:
            return GitHubResponse(
                ok=False,
                error=classify_http_error(response.status_code, response.text),
                source="httpx",
            )
        return GitHubResponse(ok=True, data=response.text, source="httpx")


def classify_github_error(returncode: int, detail: str) -> str:
    lowered = detail.lower()
    if "auth" in lowered or "login" in lowered or "credentials" in lowered:
        category = "authentication missing"
    elif "rate limit" in lowered or "abuse" in lowered or "secondary rate" in lowered:
        category = "rate limit or abuse detection"
    elif "not found" in lowered or "could not resolve" in lowered:
        category = "command failed or repo not found"
    else:
        category = "gh command failed"
    return f"{category}: exit_code={returncode}: {detail}"


def github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def classify_http_error(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code in {401, 403} and ("rate limit" in lowered or "abuse" in lowered):
        category = "rate limit or abuse detection"
    elif status_code in {401, 403}:
        category = "authentication missing or forbidden"
    elif status_code == 404:
        category = "repo not found"
    else:
        category = "http request failed"
    return f"{category}: status_code={status_code}: {body[:500]}"


def repo_api_path(owner: str, repo: str, suffix: str = "") -> str:
    base = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    return f"{base}/{suffix.lstrip('/')}" if suffix else base
