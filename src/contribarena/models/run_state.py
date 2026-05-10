from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    RUN_STARTED = "run_started"
    CONFIG_LOADED = "config_loaded"
    WORKSPACE_READY = "workspace_ready"
    REPO_DISCOVERED = "repo_discovered"
    REPO_ELIGIBLE = "repo_eligible"
    REPO_PROFILED = "repo_profiled"
    OPPORTUNITIES_RANKED = "opportunities_ranked"
    TASK_SELECTED = "task_selected"
    WORKSPACE_CHECKED = "workspace_checked"
    ARTIFACTS_WRITTEN = "artifacts_written"
    RUN_COMPLETED = "run_completed"
    WORKSPACE_FAILED = "workspace_failed"
    GOVERNANCE_BLOCKED = "governance_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    AGENT_ERROR = "agent_error"
