from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from contribarena.config.schema import RunConfig
from contribarena.engine.middleware.governance import (
    GovernanceMiddleware,
    load_governance_state,
    record_governance_attempt,
    save_governance_state,
)
from contribarena.engine.runtime_config import apply_output_dir
from contribarena.engine.runner import RunResult, Runner
from contribarena.models import GovernanceDecision


class RunLauncher(Protocol):
    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> RunResult:
        ...


@dataclass
class ControllerTickResult:
    status: str
    decision: GovernanceDecision | None = None
    run_result: RunResult | None = None


@dataclass
class ControllerResult:
    ticks: list[ControllerTickResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.ticks:
            return "skipped"
        if any(tick.status == "run_failed" for tick in self.ticks):
            return "failed"
        if any(tick.status == "run_completed" for tick in self.ticks):
            return "completed"
        return self.ticks[-1].status


class LocalController:
    def __init__(
        self,
        launcher: RunLauncher | None = None,
        governance: GovernanceMiddleware | None = None,
    ) -> None:
        self.launcher = launcher or Runner()
        self.governance = governance or GovernanceMiddleware()

    def run(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> ControllerResult:
        config = apply_output_dir(config, output_dir)
        if not config.controller.enabled:
            return ControllerResult(ticks=[ControllerTickResult(status="disabled")])
        result = ControllerResult()
        max_ticks = config.controller.max_ticks or 1
        for tick in range(max_ticks):
            tick_result = self.run_once(config, output_dir=output_dir, verbose=verbose)
            result.ticks.append(tick_result)
            if tick_result.status in {"blocked", "run_failed"}:
                break
            if tick + 1 < max_ticks:
                time.sleep(config.controller.interval_seconds)
        return result

    def run_once(
        self,
        config: RunConfig,
        output_dir: Path | None = None,
        verbose: bool = False,
    ) -> ControllerTickResult:
        if not config.discovery.candidates:
            raise ValueError("controller requires a configured target repository")
        candidate = config.discovery.candidates[0]
        state = load_governance_state(config)
        decision = self.governance.evaluate_run_start(
            config=config,
            target_owner=candidate.owner,
            target_repo=candidate.repo,
            state=state,
        )
        if not decision.passed:
            record_governance_attempt(
                state,
                repository=decision.target_repository,
                status="skipped",
                decision_id=decision.id,
                action=decision.action,
            )
            save_governance_state(config, state)
            return ControllerTickResult(status="blocked", decision=decision)

        run_result = self.launcher.run(config, output_dir=output_dir, verbose=verbose)
        state = load_governance_state(config)
        record_governance_attempt(
            state,
            repository=decision.target_repository,
            status="prepared",
            decision_id=decision.id,
            action=decision.action,
        )
        save_governance_state(config, state)
        status = "run_completed" if run_result.status == "completed" else "run_failed"
        return ControllerTickResult(
            status=status,
            decision=decision,
            run_result=run_result,
        )
