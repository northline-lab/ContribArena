from __future__ import annotations

import re
from typing import Any


_TOKEN_PATTERNS = (
    re.compile(r"https://x-access-token:[^@\s]+@", re.IGNORECASE),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(authorization:\s*)[^\n\r]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*=\s*)[^\s]+"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
)


def redact_text(text: str, *, max_chars: int | None = None) -> str:
    redacted = text
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(_redaction, redacted)
    if max_chars is not None and len(redacted) > max_chars:
        return redacted[: max(0, max_chars - 24)] + "\n[memory text truncated]"
    return redacted


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_payload(item) for key, item in value.items()}
    return value


def _redaction(match: re.Match[str]) -> str:
    if match.re.pattern.startswith("https://x-access-token"):
        return "https://x-access-token:***@"
    if match.lastindex:
        return match.group(1) + "***"
    return "***"
