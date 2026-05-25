from __future__ import annotations

__all__ = [
    "ContribArenaError",
    "ConfigError",
    "InfrastructureError",
    "AgentError",
    "BudgetExhausted",
]


class ContribArenaError(Exception):
    exit_code = 1


class ConfigError(ContribArenaError):
    exit_code = 1


class InfrastructureError(ContribArenaError):
    exit_code = 2


class AgentError(ContribArenaError):
    exit_code = 3


class BudgetExhausted(ContribArenaError):
    exit_code = 4
