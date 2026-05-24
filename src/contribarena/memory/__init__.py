"""Run-local memory and history retrieval for ContribArena."""

__all__ = ["MemoryService"]


def __getattr__(name: str) -> object:
    if name == "MemoryService":
        from .service import MemoryService
        return MemoryService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
