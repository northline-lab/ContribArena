from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from contribarena.config.schema import BudgetConfig
from contribarena.errors import BudgetExhausted


@dataclass
class BudgetTracker:
    budget: BudgetConfig
    steps: int = 0

    def __post_init__(self) -> None:
        self.started_at = monotonic()

    def record_step(self) -> None:
        self.steps += 1
        if self.steps > self.budget.max_steps:
            raise BudgetExhausted(f"max_steps exceeded: {self.budget.max_steps}")
        if self.budget.max_wall_time_seconds is not None:
            elapsed = monotonic() - self.started_at
            if elapsed > self.budget.max_wall_time_seconds:
                raise BudgetExhausted(
                    f"max_wall_time_seconds exceeded: {self.budget.max_wall_time_seconds}"
                )
