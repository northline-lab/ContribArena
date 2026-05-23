from __future__ import annotations

__all__ = ["ControllerResult", "ControllerTickResult", "LocalController", "RunResult", "Runner"]


def __getattr__(name: str) -> object:
    if name in {"ControllerResult", "ControllerTickResult", "LocalController"}:
        from .controller import ControllerResult, ControllerTickResult, LocalController

        return {
            "ControllerResult": ControllerResult,
            "ControllerTickResult": ControllerTickResult,
            "LocalController": LocalController,
        }[name]
    if name in {"RunResult", "Runner"}:
        from .runner import RunResult, Runner

        return {"RunResult": RunResult, "Runner": Runner}[name]
    raise AttributeError(name)
