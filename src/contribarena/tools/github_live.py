from __future__ import annotations

import json
import os
import shlex
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from contribarena.config.schema import OwnedRepositoryPolicy, RepoCandidate, RunConfig
from contribarena.engine.middleware.artifact import ArtifactCapture
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_attempt,
    record_governance_pr,
    save_governance_state,
    upsert_lifecycle_record,
)
from contribarena.engine.operator_events import truncate_for_operator
from contribarena.engine.workspace import DockerWorkspaceManager
from contribarena.models import AciResult, PrLifecycleRecord, QualityGateCheck, QualityGateResult
from contribarena.tools.github_pr import (
    ForkEnsureResult,
    GitHubPullRequestClient,
    PullRequestCreateResult,
    PullRequestLookupResult,
    PullRequestStatusResult,
)
from contribarena.tools.repo_eligibility import repo_check_eligibility


LIVE_PR_RETRY_ATTEMPTS = 3
LIVE_PR_RETRY_SLEEP_SECONDS = 2.0


@dataclass(frozen=True)
class LiveGithubContext:
    run_id: str
    run_mode: str = ""
    season_id: str = ""
    participant_id: str = ""
    run_dir: str = ""


def github_prepare_fork(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    client: object | None,
    owner: str,
    repo: str,
) -> AciResult:
    policy = _owned_repo_policy(config, owner, repo)
    strategy = "fork" if config.run.mode == "external_live" else (
        policy.pr_submission.strategy if policy is not None else "fork"
    )
    actor = config.governance.bot_identity.actor or "contribarena-bot"
    pr_client = client or GitHubPullRequestClient(
        token_env=config.governance.bot_identity.token_env
    )
    target_repository = f"{owner}/{repo}"
    if config.run.mode == "owned_live" and policy is None:
        result = _result(
            "github_prepare_fork",
            False,
            "owned_live target is not configured as an owned repository",
            "governance_block",
        )
        _record_live_action(
            capture,
            context,
            action="github.ensure_fork",
            status="blocked",
            target_repository=target_repository,
            external_write=False,
            error_kind="governance_block",
            error=result.error or "",
        )
        return result
    if strategy == "upstream_branch":
        payload = {
            "strategy": strategy,
            "target_repository": target_repository,
            "push_repository": target_repository,
            "push_owner": owner,
            "created": False,
        }
        _record_live_action(
            capture,
            context,
            action="github.prepare_upstream_branch",
            status="ready",
            target_repository=target_repository,
            external_write=False,
            extra=payload,
        )
        return _result("github_prepare_fork", True, output=payload)

    fork_owner = (policy.pr_submission.fork_owner if policy is not None else None) or actor
    ensure_fork = getattr(pr_client, "ensure_fork", None)
    if ensure_fork is None:
        result = _result(
            "github_prepare_fork",
            False,
            "PR client does not support fork submission",
            "infrastructure",
        )
        _record_live_action(
            capture,
            context,
            action="github.ensure_fork",
            status="failed",
            target_repository=target_repository,
            external_write=False,
            error_kind="infrastructure",
            error=result.error or "",
            extra={"requested_fork_owner": fork_owner},
        )
        return result
    fork: ForkEnsureResult = ensure_fork(owner=owner, repo=repo, fork_owner=fork_owner)
    payload = {
        "strategy": strategy,
        "target_repository": target_repository,
        "push_repository": fork.full_name or f"{fork_owner}/{repo}",
        "push_owner": fork.owner or fork_owner,
        "created": fork.created,
        "fork_url": fork.url,
    }
    _record_live_action(
        capture,
        context,
        action="github.create_fork" if fork.created else "github.ensure_fork",
        status="ready" if fork.ok else "failed",
        target_repository=target_repository,
        external_write=fork.created,
        error_kind="" if fork.ok else "fork_invalid",
        error=fork.error,
        extra={**payload, "requested_fork_owner": fork_owner},
    )
    if not fork.ok:
        return _result("github_prepare_fork", False, fork.error, "fork_invalid", payload)
    return _result("github_prepare_fork", True, output=payload)


def github_prepare_branch(
    *,
    config: RunConfig,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    owner: str,
    repo: str,
    base: str,
    branch: str,
    path: str = "repo",
) -> AciResult:
    target_repository = f"{owner}/{repo}"
    expected_prefix = f"contribarena/{context.run_id}-"
    if not branch.startswith(expected_prefix):
        error = f"Live PR branch must start with {expected_prefix}"
        payload = {
            "branch": branch,
            "base": base,
            "required_prefix": expected_prefix,
        }
        _record_live_action(
            capture,
            context,
            action="github.prepare_branch",
            status="failed",
            target_repository=target_repository,
            external_write=False,
            error_kind="branch_identity_invalid",
            error=error,
            retryable=False,
            extra=payload,
        )
        return _result("github_prepare_branch", False, error, "branch_identity_invalid", payload)
    url = f"https://github.com/{target_repository}.git"
    quoted_path = shlex.quote(path)
    safe_run_id = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_"
        for ch in context.run_id
    )[:64] or "run"
    patch_path = f"/tmp/contribarena-live-submission-{safe_run_id}.patch"
    command = " && ".join(
        [
            f"test -d {quoted_path}/.git",
            f"rm -f {shlex.quote(patch_path)}",
            f"(cd {quoted_path} && "
            "(git diff --binary HEAD -- .; "
            "git ls-files --others --exclude-standard -z -- . | "
            "while IFS= read -r -d '' file; do "
            "git diff --no-index --binary -- /dev/null \"$file\" || true; "
            "done)) > "
            f"{shlex.quote(patch_path)}",
            f"if [ -s {shlex.quote(patch_path)} ]; then "
            f"patch_saved=1; else patch_saved=0; fi",
            f"git -C {quoted_path} clean -fd -- . >/dev/null",
            f"git -C {quoted_path} remote set-url origin {shlex.quote(url)}",
            f"git -c http.version=HTTP/1.1 -C {quoted_path} fetch --no-tags --depth 1 origin {shlex.quote(base)}",
            f"git -C {quoted_path} checkout -B {shlex.quote(branch)} FETCH_HEAD",
            f"if [ \"$patch_saved\" = 1 ]; then git -C {quoted_path} apply --index {shlex.quote(patch_path)}; fi",
            f"git -C {quoted_path} merge-base --is-ancestor FETCH_HEAD HEAD",
            f"printf 'base_sha=' && git -C {quoted_path} rev-parse FETCH_HEAD",
            f"printf 'head_sha=' && git -C {quoted_path} rev-parse HEAD",
            f"printf 'patch_restored=' && printf '%s\\n' \"$patch_saved\"",
        ]
    )
    cmd = workspace.run(command, timeout_seconds=config.workspace.command_timeout_seconds)
    capture.record_command(cmd)
    success = cmd.exit_code == 0
    transient = _transient_message("\n".join((cmd.stderr, cmd.stdout)))
    payload = {
        "branch": branch,
        "base": base,
        "history_safe": success,
        "base_sha": _prefixed_line(cmd.stdout, "base_sha="),
        "head_sha": _prefixed_line(cmd.stdout, "head_sha="),
        "patch_restored": _prefixed_line(cmd.stdout, "patch_restored=") == "1",
    }
    _record_live_action(
        capture,
        context,
        action="github.prepare_branch",
        status="prepared" if success else "failed",
        target_repository=target_repository,
        external_write=False,
        error_kind="" if success else "git_prepare_branch_transient" if transient else "branch_history_invalid",
        error="" if success else truncate_for_operator(cmd.stderr or cmd.stdout),
        retryable=not success and transient,
        extra=payload,
    )
    if not success:
        return _result(
            "github_prepare_branch",
            False,
            truncate_for_operator(cmd.stderr or cmd.stdout),
            "git_prepare_branch_transient" if transient else "branch_history_invalid",
            payload,
        )
    return _result("github_prepare_branch", True, output=payload)


def github_commit(
    *,
    config: RunConfig,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    title: str,
    body: str = "",
    path: str = "repo",
) -> AciResult:
    actor = config.governance.bot_identity.actor or "contribarena-bot"
    email = f"{actor}@users.noreply.github.com"
    message_file = ".git/CONTRIBARENA_COMMIT_MSG"
    message = title.strip() or "ContribArena live contribution"
    if body.strip():
        message += "\n\n" + body.strip()
    quoted_path = shlex.quote(path)
    quoted_message = shlex.quote(message)
    command = " && ".join(
        [
            f"git -C {quoted_path} config user.name {shlex.quote(actor)}",
            f"git -C {quoted_path} config user.email {shlex.quote(email)}",
            f"printf %s {quoted_message} > {shlex.quote(path + '/' + message_file)}",
            f"git -C {quoted_path} add -A",
            f"git -C {quoted_path} diff --cached --quiet --exit-code && exit 3 || true",
            f"git -C {quoted_path} commit -F {shlex.quote(message_file)}",
            f"printf 'commit_sha=' && git -C {quoted_path} rev-parse HEAD",
            f"git -C {quoted_path} diff-tree --no-commit-id --name-only -r HEAD | sed 's/^/file=/'",
            f"rm -f {shlex.quote(path + '/' + message_file)}",
        ]
    )
    cmd = workspace.run(command, timeout_seconds=config.workspace.command_timeout_seconds)
    capture.record_command(cmd)
    success = cmd.exit_code == 0
    stdout_lines = [line.strip() for line in cmd.stdout.splitlines() if line.strip()]
    commit_sha = _prefixed_line(cmd.stdout, "commit_sha=")
    files = [
        line.removeprefix("file=")
        for line in stdout_lines
        if line.startswith("file=")
    ]
    error_kind = "git_no_changes" if cmd.exit_code == 3 else "infrastructure"
    payload = {"commit_sha": commit_sha, "files_changed": files}
    _record_live_action(
        capture,
        context,
        action="github.commit",
        status="committed" if success else "failed",
        target_repository=_configured_repo_full_name(config),
        external_write=False,
        error_kind="" if success else error_kind,
        error="" if success else truncate_for_operator(cmd.stderr or cmd.stdout),
        extra=payload,
    )
    if not success:
        return _result(
            "github_commit",
            False,
            truncate_for_operator(cmd.stderr or cmd.stdout),
            error_kind,
            payload,
        )
    return _result("github_commit", True, output=payload)


def github_push_branch(
    *,
    config: RunConfig,
    workspace: DockerWorkspaceManager,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    owner: str,
    repo: str,
    branch: str,
    path: str = "repo",
) -> AciResult:
    token_env = config.governance.bot_identity.token_env
    token = os.environ.get(token_env, "")
    remote_name = "contribarena-submit"
    remote_url = f'"https://x-access-token:${{{token_env}}}@github.com/{owner}/{repo}.git"'
    remote_branch_ref = f"refs/heads/{branch}"
    tracking_ref = f"refs/remotes/{remote_name}/{branch}"
    command = " && ".join(
        [
            f"(git -C {shlex.quote(path)} remote remove {remote_name} >/dev/null 2>&1 || true)",
            f"git -C {shlex.quote(path)} remote add {remote_name} {remote_url}",
            f"(git -c http.version=HTTP/1.1 -C {shlex.quote(path)} fetch --no-tags {remote_name} "
            f"{shlex.quote('+' + remote_branch_ref + ':' + tracking_ref)} >/dev/null 2>&1 || true)",
            f"if git -C {shlex.quote(path)} show-ref --verify --quiet {shlex.quote(tracking_ref)}; then "
            f"lease_arg={shlex.quote('--force-with-lease=' + remote_branch_ref + ':')}"
            f"$(git -C {shlex.quote(path)} rev-parse {shlex.quote(tracking_ref)}); "
            f"else lease_arg={shlex.quote('--force-with-lease=' + remote_branch_ref + ':')}; fi; "
            f"git -c http.version=HTTP/1.1 -C {shlex.quote(path)} push {remote_name} "
            f"{shlex.quote('HEAD:' + remote_branch_ref)} "
            '"$lease_arg"',
            f"git -C {shlex.quote(path)} rev-parse HEAD",
        ]
    )
    result = None
    attempts = 0
    for attempt in range(1, LIVE_PR_RETRY_ATTEMPTS + 1):
        attempts = attempt
        result = workspace.run_with_env(command, {token_env: token})
        result = _redact_command_result(result, token)
        capture.record_command(result)
        if result.exit_code == 0 or not _transient_message(
            "\n".join((result.stderr, result.stdout))
        ):
            break
        if attempt < LIVE_PR_RETRY_ATTEMPTS:
            time.sleep(LIVE_PR_RETRY_SLEEP_SECONDS)
    assert result is not None
    success = result.exit_code == 0
    head_sha = _last_sha(result.stdout)
    payload = {
        "remote": f"{owner}/{repo}",
        "branch": branch,
        "head": f"{owner}:{branch}",
        "head_sha": head_sha,
        "attempts": attempts,
    }
    error_kind = (
        "" if success else "git_push_transient" if _transient_message(result.stderr + result.stdout) else "git_push_nontransient"
    )
    policy = _owned_repo_policy(config, owner, repo)
    push_action = "github.push_fork_branch"
    if config.run.mode == "owned_live" and policy is not None:
        if policy.pr_submission.strategy == "upstream_branch":
            push_action = "github.push_upstream_branch"
    _record_live_action(
        capture,
        context,
        action=push_action,
        status="pushed" if success else "failed",
        target_repository=_configured_repo_full_name(config),
        external_write=True,
        error_kind=error_kind,
        error="" if success else truncate_for_operator(result.stderr or result.stdout),
        retryable=error_kind == "git_push_transient",
        extra=payload,
    )
    if not success:
        return _result(
            "github_push_branch",
            False,
            truncate_for_operator(result.stderr or result.stdout),
            error_kind,
            payload,
        )
    return _result("github_push_branch", True, output=payload)


def github_open_pr(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    client: object | None,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> AciResult:
    target_repository = f"{owner}/{repo}"
    quality = _pre_submit_quality(capture)
    if quality.status != "pass":
        payload = quality.model_dump(mode="json")
        _record_live_action(
            capture,
            context,
            action="github.open_pr",
            status="blocked",
            target_repository=target_repository,
            external_write=False,
            error_kind="quality_gate",
            error="; ".join(quality.blockers),
            extra={"quality_gate": payload},
        )
        return _result("github_open_pr", False, "; ".join(quality.blockers), "quality_gate", payload)

    pr_client = client or GitHubPullRequestClient(
        token_env=config.governance.bot_identity.token_env
    )
    actor = _authenticated_actor(pr_client) or config.governance.bot_identity.actor
    state = load_governance_state(config)
    external_review_passed = True
    external_review_reasons: list[str] | None = None
    contribution_class = "low_risk_code"
    if config.run.mode == "external_live":
        external_review = _external_live_review(
            config=config,
            capture=capture,
            owner=owner,
            repo=repo,
        )
        external_review_passed = external_review.passed
        external_review_reasons = external_review.reasons
        contribution_class = external_review.contribution_class
    decision = GovernanceMiddleware().evaluate_pr_open(
        config=config,
        quality_gate=quality,
        target_owner=owner,
        target_repo=repo,
        base_branch=base,
        contribution_class=contribution_class,
        state=state,
        agent_id=config.run.participant_id or "builtin",
        actor=actor,
        external_review_passed=external_review_passed,
        external_review_reasons=external_review_reasons,
    )
    if not decision.passed:
        record_governance_attempt(
            state,
            repository=target_repository,
            status="blocked",
            decision_id=decision.id,
            action=decision.action,
        )
        save_governance_state(config, state)
        _record_live_action(
            capture,
            context,
            action="github.open_pr",
            status="blocked",
            target_repository=target_repository,
            external_write=False,
            error_kind="governance_block",
            error="; ".join(decision.reasons),
            governance_decision_id=decision.id,
            extra={
                "governance_status": decision.status,
                "governance_reasons": decision.reasons,
                "governance_action": decision.action,
                "github_actor": decision.actor,
                "contribution_class": decision.contribution_class,
                "target_repository": decision.target_repository,
            },
        )
        return _result(
            "github_open_pr",
            False,
            "; ".join(decision.reasons),
            "governance_block",
            decision.model_dump(mode="json"),
        )

    expected_head_sha = _pushed_head_sha(capture, head)
    lookup = _find_existing_pr(pr_client, owner=owner, repo=repo, head=head, base=base)
    if lookup.ok:
        payload = _pr_payload(
            lookup,
            head=head,
            base=base,
            idempotent=True,
            title=title,
            body=body,
        )
        identity_error = _verify_pr_identity(
            pr_client,
            capture,
            context,
            owner=owner,
            repo=repo,
            number=lookup.number,
            expected_head=head,
            expected_head_sha=expected_head_sha,
        )
        if identity_error:
            _record_live_action(
                capture,
                context,
                action="github.open_pr",
                status="failed",
                target_repository=target_repository,
                external_write=False,
                error_kind="pr_identity_mismatch",
                error=identity_error,
                retryable=False,
                governance_decision_id=decision.id,
                extra=payload,
            )
            return _result("github_open_pr", False, identity_error, "pr_identity_mismatch", payload)
        _record_open_pr_success(
            config,
            capture,
            context,
            state,
            target_repository,
            decision.id,
            payload,
            existing=True,
        )
        return _result("github_open_pr", True, output=payload)

    open_pr = getattr(pr_client, "open_pr")
    pr_result: PullRequestCreateResult | None = None
    attempts = 0
    for attempt in range(1, LIVE_PR_RETRY_ATTEMPTS + 1):
        attempts = attempt
        pr_result = open_pr(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base,
        )
        if pr_result.ok or not _transient_message(pr_result.error):
            break
        if attempt < LIVE_PR_RETRY_ATTEMPTS:
            time.sleep(LIVE_PR_RETRY_SLEEP_SECONDS)
    assert pr_result is not None
    if not pr_result.ok:
        error_kind = (
            "github_api_transient"
            if _transient_message(pr_result.error)
            else "github_api_nontransient"
        )
        record_governance_attempt(
            state,
            repository=target_repository,
            status="failed",
            decision_id=decision.id,
            action=decision.action,
        )
        save_governance_state(config, state)
        _record_live_action(
            capture,
            context,
            action="github.open_pr",
            status="failed",
            target_repository=target_repository,
            external_write=True,
            error_kind=error_kind,
            error=pr_result.error,
            retryable=error_kind == "github_api_transient",
            governance_decision_id=decision.id,
            extra={"head": head, "base": base, "attempts": attempts},
        )
        return _result("github_open_pr", False, pr_result.error, error_kind)

    payload = {
        "idempotent": False,
        "number": pr_result.number,
        "url": pr_result.url,
        "head": head,
        "base": base,
        "head_sha": pr_result.head_sha,
        "title": title,
        "body": body,
        "governance_decision_id": decision.id,
        "governance_status": decision.status,
        "governance_reasons": decision.reasons,
        "governance_action": decision.action,
        "github_actor": decision.actor,
        "target_repository": decision.target_repository,
        "contribution_class": decision.contribution_class,
        "attempts": attempts,
    }
    identity_error = _verify_pr_identity(
        pr_client,
        capture,
        context,
        owner=owner,
        repo=repo,
        number=pr_result.number,
        expected_head=head,
        expected_head_sha=expected_head_sha or pr_result.head_sha,
    )
    if identity_error:
        _record_live_action(
            capture,
            context,
            action="github.open_pr",
            status="failed",
            target_repository=target_repository,
            external_write=False,
            error_kind="pr_identity_mismatch",
            error=identity_error,
            retryable=False,
            governance_decision_id=decision.id,
            extra=payload,
        )
        return _result("github_open_pr", False, identity_error, "pr_identity_mismatch", payload)
    _record_open_pr_success(
        config,
        capture,
        context,
        state,
        target_repository,
        decision.id,
        payload,
        existing=False,
    )
    return _result("github_open_pr", True, output=payload)


def github_observe_pr(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    client: object | None,
    owner: str,
    repo: str,
    number: int,
) -> AciResult:
    pr_client = client or GitHubPullRequestClient(
        token_env=config.governance.bot_identity.token_env
    )
    get_pr = getattr(pr_client, "get_pr", None)
    if get_pr is None:
        return _result(
            "github_observe_pr",
            False,
            "PR client does not support PR observation",
            "infrastructure",
        )
    result = get_pr(owner=owner, repo=repo, number=number)
    payload = {
        "number": number,
        "url": result.url,
        "state": "merged" if result.merged else result.state,
        "head_sha": result.head_sha,
        "head_ref": result.head_ref,
        "base_ref": result.base_ref,
        "draft": result.draft,
        "comments": result.comments,
        "review_comments": result.review_comments,
    }
    _record_live_action(
        capture,
        context,
        action="github.observe_pr",
        status="observed" if result.ok else "failed",
        target_repository=f"{owner}/{repo}",
        external_write=False,
        error_kind="" if result.ok else "github_api_nontransient",
        error=result.error,
        extra=payload,
    )
    if not result.ok:
        return _result("github_observe_pr", False, result.error, "github_api_nontransient", payload)
    return _result("github_observe_pr", True, output=payload)


def _record_open_pr_success(
    config: RunConfig,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    state: Any,
    repository: str,
    decision_id: str,
    payload: dict[str, object],
    *,
    existing: bool,
) -> None:
    number = payload.get("number")
    if isinstance(number, int):
        record_governance_attempt(
            state,
            repository=repository,
            status="opened",
            decision_id=decision_id,
            action="github.open_pr",
        )
        record_governance_pr(
            state,
            repository=repository,
            number=number,
            url=str(payload.get("url") or ""),
            branch=str(payload.get("head") or ""),
            season_id=context.season_id,
            participant_id=context.participant_id,
        )
        upsert_lifecycle_record(
            state,
            PrLifecycleRecord(
                season_id=context.season_id,
                participant_id=context.participant_id,
                repository=repository,
                number=number,
                url=str(payload.get("url") or ""),
                originating_run_dir=context.run_dir,
                branch=str(payload.get("head") or ""),
                head=str(payload.get("head") or ""),
                base=str(payload.get("base") or "main"),
                head_sha=str(payload.get("head_sha") or ""),
                last_observed_at=datetime.now(UTC).isoformat(),
                summary="opened by agent-owned github_open_pr tool",
            ),
        )
        save_governance_state(config, state)
    _record_live_action(
        capture,
        context,
        action="github.open_pr",
        status="existing" if existing else "opened",
        target_repository=repository,
        external_write=not existing,
        governance_decision_id=decision_id,
        extra=payload,
    )


def _pre_submit_quality(capture: ArtifactCapture) -> QualityGateResult:
    blockers: list[str] = []
    checks: list[QualityGateCheck] = []
    latest = _latest_successful_submit(capture)
    patch = latest.output if latest is not None else ""
    _add_quality_check(checks, blockers, "patch_submitted", _has_patch_diff(patch), "patch diff present")
    finalized = any(
        item.tool == "aci_submit_patch_finalize" and item.success
        for item in capture.aci_results
    )
    _add_quality_check(checks, blockers, "patch_finalized", finalized, "review patch finalized")
    verified = _has_successful_verification_after_last_edit(capture)
    _add_quality_check(checks, blockers, "verified_after_edit", verified, "verification evidence present")
    suspicious = _suspicious_patch_paths(_patch_paths(patch))
    _add_quality_check(
        checks,
        blockers,
        "maintainer_appropriate_paths",
        not suspicious,
        "no suspicious paths" if not suspicious else "suspicious paths: " + ", ".join(suspicious),
    )
    return QualityGateResult(
        status="block" if blockers else "pass",
        blockers=blockers,
        warnings=[],
        checks=checks,
    )


def _add_quality_check(
    checks: list[QualityGateCheck],
    blockers: list[str],
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(QualityGateCheck(name=name, status="pass" if passed else "block", detail=detail))
    if not passed:
        blockers.append(detail)


def _record_live_action(
    capture: ArtifactCapture,
    context: LiveGithubContext,
    *,
    action: str,
    status: str,
    target_repository: str,
    external_write: bool,
    error_kind: str = "",
    error: str = "",
    retryable: bool = False,
    nonfatal: bool = False,
    governance_decision_id: str = "",
    extra: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "2",
        "ts": datetime.now(UTC).isoformat(),
        "run_id": context.run_id,
        "mode": context.run_mode,
        "season_id": context.season_id,
        "participant_id": context.participant_id,
        "action": action,
        "status": status,
        "target_repository": target_repository,
        "requested_by_agent": True,
        "executed_by_harness": True,
        "external_write": external_write,
        "governance_decision_id": governance_decision_id,
        "error_kind": error_kind,
        "error": truncate_for_operator(error),
        "retryable": retryable,
        "nonfatal": nonfatal,
    }
    payload.update(extra or {})
    capture.record_live_action(payload)


@dataclass(frozen=True)
class _ExternalLiveReview:
    passed: bool
    reasons: list[str]
    contribution_class: str


def _external_live_review(
    *,
    config: RunConfig,
    capture: ArtifactCapture,
    owner: str,
    repo: str,
) -> _ExternalLiveReview:
    reasons: list[str] = []
    target = RepoCandidate(
        owner=owner,
        repo=repo,
        url=f"https://github.com/{owner}/{repo}",
    )
    patch = _latest_successful_submit(capture).output if _latest_successful_submit(capture) else ""
    contribution_class = _contribution_class(patch)
    if not patch.strip():
        reasons.append("external_live requires a submitted patch before PR submission")
    try:
        eligibility = repo_check_eligibility(target)
    except Exception as exc:
        reasons.append(f"eligibility check failed: {exc}")
    else:
        if not eligibility.eligible:
            reasons.extend(f"eligibility: {reason}" for reason in eligibility.reasons)
    if contribution_class not in config.governance.contribution_classes.allowed:
        reasons.append(f"contribution class is not allowed: {contribution_class}")
    diff_paths = _patch_paths(patch)
    if "docs" not in config.governance.contribution_classes.allowed and diff_paths:
        if not _has_code_or_test_path(diff_paths):
            reasons.append("external_live code-only run requires at least one code or test path")
    return _ExternalLiveReview(
        passed=not reasons,
        reasons=reasons,
        contribution_class=contribution_class,
    )


def _result(
    tool: str,
    success: bool,
    error: str = "",
    error_kind: str = "",
    output: dict[str, object] | None = None,
) -> AciResult:
    payload = output or {}
    return AciResult(
        tool=tool,
        success=success,
        output=json.dumps(payload, ensure_ascii=True) if payload else ("" if success else error),
        error=None if success else error,
        error_kind=None if success else error_kind,
        recovery_kind=None if success else error_kind or "tool_failure",
    )


def _owned_repo_policy(config: RunConfig, owner: str, repo: str) -> OwnedRepositoryPolicy | None:
    for policy in config.governance.owned_repositories:
        if policy.owner == owner and policy.repo == repo:
            return policy
    return None


def _configured_repo_full_name(config: RunConfig) -> str:
    if config.discovery.candidates:
        return config.discovery.candidates[0].full_name
    return ""


def _latest_successful_submit(capture: ArtifactCapture) -> AciResult | None:
    for item in reversed(capture.aci_results):
        if item.tool == "aci_submit_patch" and item.success:
            return item
    return None


def _has_patch_diff(patch: str) -> bool:
    return patch.strip().startswith("diff --git ") or "\ndiff --git " in patch


def _has_successful_verification_after_last_edit(capture: ArtifactCapture) -> bool:
    last_edit_index = -1
    for index, item in enumerate(capture.aci_results):
        if item.tool in {"aci_apply_patch", "aci_replace", "aci_insert", "aci_create", "aci_undo"} and item.success:
            last_edit_index = index
    return any(
        item.tool == "aci_verify" and item.success
        for item in capture.aci_results[last_edit_index + 1 :]
    )


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            paths.append(parts[3].removeprefix("b/"))
    return sorted(set(paths))


def _suspicious_patch_paths(paths: list[str]) -> list[str]:
    markers = ("/__pycache__/", ".pyc", ".pyo", ".DS_Store", ".egg-info/", ".pytest_cache/")
    suspicious: list[str] = []
    for path in paths:
        normalized = f"/{path}"
        if any(marker in normalized for marker in markers):
            suspicious.append(path)
    return suspicious


def _contribution_class(patch: str) -> str:
    paths = _patch_paths(patch)
    if not paths:
        return "low_risk_code"
    if all(_is_docs_only_path(path) for path in paths):
        return "docs"
    if all(_is_test_only_path(path) for path in paths):
        return "tests"
    return "low_risk_code"


def _normalized_diff_path(path: str) -> str:
    lowered = path.lower()
    return lowered.removeprefix("repo/") if lowered.startswith("repo/") else lowered


def _is_docs_only_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if _is_code_or_test_path(lowered):
        return False
    if lowered.startswith(("docs/", "doc/")):
        return True
    if lowered in {"readme.md", "readme.rst", "changelog.md", "changelog.rst"}:
        return True
    return lowered.endswith((".md", ".rst", ".txt"))


def _is_test_only_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if lowered.startswith(("tests/", "test/")):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _is_code_or_test_path(path: str) -> bool:
    lowered = _normalized_diff_path(path)
    if _is_test_only_path(lowered):
        return True
    if lowered.startswith(("src/", "lib/", "pkg/", "packages/", "app/")):
        return True
    return lowered.endswith(
        (
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".c",
            ".cc",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".rb",
            ".php",
            ".swift",
            ".scala",
            ".sh",
        )
    )


def _has_code_or_test_path(paths: list[str]) -> bool:
    return any(_is_code_or_test_path(path) for path in paths)


def _redact_command_result(result: Any, token: str) -> Any:
    if not token:
        return result
    return result.model_copy(
        update={
            "command": result.command.replace(token, "***"),
            "stdout": result.stdout.replace(token, "***"),
            "stderr": result.stderr.replace(token, "***"),
        }
    )


def _transient_message(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "apiconnectionerror",
            "connection error",
            "connection reset",
            "socket reset",
            "gnutls recv error",
            "failed to connect",
            "couldn't connect to server",
            "timeout",
            "timed out",
            "500",
            "502",
            "503",
            "504",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
        )
    )


def _prefixed_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _last_sha(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if len(stripped) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in stripped):
            return stripped
    return ""


def _authenticated_actor(client: object) -> str:
    method = getattr(client, "authenticated_actor", None)
    if method is None:
        return ""
    try:
        return str(method() or "")
    except Exception:
        return ""


def _find_existing_pr(
    client: object,
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
) -> PullRequestLookupResult:
    method = getattr(client, "find_open_pr_by_head", None)
    if method is None:
        return PullRequestLookupResult(ok=False, error="not_supported")
    return method(owner=owner, repo=repo, head=head, base=base)


def _pr_payload(
    lookup: PullRequestLookupResult,
    *,
    head: str,
    base: str,
    idempotent: bool,
    title: str = "",
    body: str = "",
) -> dict[str, object]:
    return {
        "idempotent": idempotent,
        "number": lookup.number,
        "url": lookup.url,
        "head": head,
        "base": base,
        "head_sha": lookup.head_sha,
        "state": lookup.state,
        "title": title,
        "body": body,
    }


def _verify_pr_identity(
    client: object,
    capture: ArtifactCapture,
    context: LiveGithubContext,
    *,
    owner: str,
    repo: str,
    number: int | None,
    expected_head: str,
    expected_head_sha: str,
) -> str:
    if number is None:
        return "GitHub PR response did not include a PR number"
    get_pr = getattr(client, "get_pr", None)
    if get_pr is None:
        return ""
    observed: PullRequestStatusResult = get_pr(owner=owner, repo=repo, number=number)
    expected_ref = _head_ref_from_pr_head(expected_head)
    error = _pr_identity_error(
        observed=observed,
        expected_ref=expected_ref,
        expected_head_sha=expected_head_sha,
    )
    _record_live_action(
        capture,
        context,
        action="github.verify_pr_identity",
        status="verified" if not error else "failed",
        target_repository=f"{owner}/{repo}",
        external_write=False,
        error_kind="" if not error else "pr_identity_mismatch",
        error=error,
        retryable=False,
        extra={
            "number": number,
            "expected_head": expected_head,
            "expected_head_ref": expected_ref,
            "expected_head_sha": expected_head_sha,
            "observed_head_ref": observed.head_ref,
            "observed_head_sha": observed.head_sha,
            "observed_state": observed.state,
        },
    )
    return error


def _pushed_head_sha(capture: ArtifactCapture, head: str) -> str:
    for row in reversed(capture.live_action_rows):
        if row.get("action") not in {"github.push_fork_branch", "github.push_upstream_branch"}:
            continue
        if row.get("status") != "pushed":
            continue
        if row.get("head") != head:
            continue
        return str(row.get("head_sha") or "")
    return ""


def _head_ref_from_pr_head(head: str) -> str:
    return head.rsplit(":", 1)[-1]


def _pr_identity_error(
    *,
    observed: PullRequestStatusResult,
    expected_ref: str,
    expected_head_sha: str,
) -> str:
    if not observed.ok:
        return observed.error or "GitHub PR identity observation failed"
    if expected_ref and observed.head_ref and observed.head_ref != expected_ref:
        return (
            f"GitHub PR head ref mismatch: expected {expected_ref}, "
            f"observed {observed.head_ref}"
        )
    if expected_head_sha and observed.head_sha and observed.head_sha != expected_head_sha:
        return (
            f"GitHub PR head sha mismatch: expected {expected_head_sha}, "
            f"observed {observed.head_sha}"
        )
    return ""
