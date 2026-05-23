from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from contribarena.trace import TraceWriter

T = TypeVar("T")


def traced_tool(
    trace: TraceWriter, state: str, event: str, fn: Callable[..., T]
) -> Callable[..., T]:
    def wrapper(*args: object, **kwargs: object) -> T:
        trace.write(
            state, f"{event}.started", {"args": [str(arg) for arg in args], "kwargs": kwargs}
        )
        result = fn(*args, **kwargs)
        trace.write(state, f"{event}.finished", {"result": _safe_result(result)})
        return result

    return wrapper


def _safe_result(result: object) -> object:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")  # type: ignore[no-any-return]
    return str(result)
