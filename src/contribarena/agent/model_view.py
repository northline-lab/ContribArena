from __future__ import annotations

import json

MODEL_VIEW_MAX_STRING_CHARS = 6000
MODEL_VIEW_MAX_LIST_ITEMS = 30
TRUNCATION_MARKER = "[model-view truncated; full value remains in trace/artifacts]"


def to_model_json(value: object) -> str:
    """Return a bounded JSON projection for model-facing tool observations."""
    return json.dumps(project_model_view(value), ensure_ascii=True)


def project_model_view(value: object) -> object:
    return _project(_jsonable(value))


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _project(value: object) -> object:
    if isinstance(value, str):
        return _cap_string(value)
    if isinstance(value, list):
        projected = [_project(item) for item in value[:MODEL_VIEW_MAX_LIST_ITEMS]]
        if len(value) > MODEL_VIEW_MAX_LIST_ITEMS:
            projected.append(
                {
                    "truncated_items": len(value) - MODEL_VIEW_MAX_LIST_ITEMS,
                    "message": TRUNCATION_MARKER,
                }
            )
        return projected
    if isinstance(value, dict):
        return {key: _project(item) for key, item in value.items()}
    return value


def _cap_string(value: str) -> str:
    if len(value) <= MODEL_VIEW_MAX_STRING_CHARS:
        return value
    return value[:MODEL_VIEW_MAX_STRING_CHARS] + "\n" + TRUNCATION_MARKER
