from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RedactedText:
    text: str
    redacted: bool = False
    classes: list[str] = field(default_factory=list)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}")),
    ("credential", re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s]{12,}")),
    ("x_access_token_url", re.compile(r"https://x-access-token:[^@\s]+@")),
]


def redact_visible_text(text: str, *, max_chars: int = 800) -> RedactedText:
    value = text.strip()
    classes: list[str] = []
    for label, pattern in _PATTERNS:
        new_value = pattern.sub(_replacement(label), value)
        if new_value != value:
            classes.append(label)
            value = new_value
    if len(value) > max_chars:
        value = value[: max(0, max_chars - 15)].rstrip() + " [truncated]"
        classes.append("truncated")
    return RedactedText(text=value, redacted=bool(classes), classes=classes)


def _replacement(label: str) -> str:
    if label == "x_access_token_url":
        return "https://x-access-token:***@"
    return "***"
