from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = [
    "TEXT_SUFFIXES",
    "TRACE_RECORD_TYPES",
    "INTENT_ALIASES",
    "DEFAULT_PRIORITIES",
    "INTENT_PRIORITIES",
    "is_text_artifact",
    "split_text",
    "record_type",
    "record_id",
    "query_terms",
    "resolve_intent",
    "include_record_type",
    "record_priority",
    "title_for_record",
    "reason_for_record",
    "snippet",
]

TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".log",
    ".diff",
    ".patch",
}

TRACE_RECORD_TYPES = {"trace_event"}

INTENT_ALIASES = {
    "ci": "verification",
    "test": "verification",
    "tests": "verification",
    "verify": "verification",
    "verification": "verification",
    "guidance": "repo_context",
    "repo": "repo_context",
    "repo_context": "repo_context",
    "failure": "failure",
    "error": "failure",
    "tool_error": "failure",
    "provider_error": "failure",
    "external": "external_write",
    "external_write": "external_write",
    "lifecycle": "external_write",
    "pr": "external_write",
    "unknown": "unknown",
}

DEFAULT_PRIORITIES = {
    "quality_report": 95,
    "postmortem": 90,
    "memory_event": 85,
    "working_memory": 80,
    "repo_guidance": 75,
    "memory_context": 70,
    "verification": 65,
    "quality_gate": 60,
    "governance_decision": 55,
    "ci_status": 50,
    "live_action": 45,
    "review_event": 45,
    "tool_result": 35,
    "artifact": 25,
    "trace_event": 0,
}

INTENT_PRIORITIES = {
    "verification": {
        "verification": 100,
        "quality_report": 95,
        "ci_status": 90,
        "quality_gate": 80,
        "postmortem": 60,
        "artifact": 35,
    },
    "repo_context": {
        "repo_guidance": 100,
        "working_memory": 95,
        "memory_event": 90,
        "memory_context": 80,
        "quality_report": 55,
        "artifact": 35,
    },
    "failure": {
        "postmortem": 100,
        "quality_gate": 95,
        "quality_report": 85,
        "tool_result": 75,
        "verification": 70,
        "trace_event": 20,
        "artifact": 30,
    },
    "external_write": {
        "live_action": 100,
        "review_event": 95,
        "governance_decision": 90,
        "ci_status": 80,
        "repo_guidance": 45,
        "quality_report": 40,
        "artifact": 25,
    },
}


def is_text_artifact(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or "." not in path.name


def split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current and current_len + len(line) > max_chars:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line[:max_chars])
        current_len += min(len(line), max_chars)
    if current:
        chunks.append("".join(current))
    return chunks


def record_type(path: Path) -> str:
    name = path.name
    if name == "trace.jsonl":
        return "trace_event"
    if name == "workspace_command.json":
        return "tool_result"
    if name == "quality_gate.json":
        return "quality_gate"
    if name == "quality_report.md":
        return "quality_report"
    if name == "test_log.txt":
        return "verification"
    if name == "ci_status.json":
        return "ci_status"
    if name == "live_action_log.jsonl":
        return "live_action"
    if name == "pr_review_log.jsonl":
        return "review_event"
    if name == "postmortem.md":
        return "postmortem"
    if name == "memory_events.jsonl":
        return "memory_event"
    if name == "working_memory.json":
        return "working_memory"
    if name == "memory_context.json":
        return "memory_context"
    if name == "repo_guidance.json":
        return "repo_guidance"
    if name == "governance_decision.json":
        return "governance_decision"
    return "artifact"


def record_id(run_id: str, source: str, index: int) -> str:
    raw = f"{run_id}:{source}:{index}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def query_terms(query: str) -> list[str]:
    terms = []
    for raw in query.replace('"', " ").split():
        cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})
        if cleaned:
            terms.append(f'"{cleaned}"')
    return terms[:8]


def resolve_intent(intent: str, query: str) -> str:
    normalized = INTENT_ALIASES.get(intent.strip().lower(), "unknown")
    if normalized != "unknown":
        return normalized
    lowered = query.lower()
    if any(term in lowered for term in ("test", "pytest", "verify", "ci", "compileall")):
        return "verification"
    if any(term in lowered for term in ("guidance", "contributing", "agents.md", "template")):
        return "repo_context"
    if any(term in lowered for term in ("fail", "error", "blocked", "exception", "warning")):
        return "failure"
    if any(term in lowered for term in ("pr", "push", "fork", "label", "lifecycle", "review")):
        return "external_write"
    return "unknown"


def include_record_type(record_type: str, intent: str) -> bool:
    if record_type not in TRACE_RECORD_TYPES:
        return True
    return intent == "failure"


def record_priority(record_type: str, intent: str) -> int:
    return INTENT_PRIORITIES.get(intent, {}).get(
        record_type,
        DEFAULT_PRIORITIES.get(record_type, DEFAULT_PRIORITIES["artifact"]),
    )


def title_for_record(source_path: str, record_type: str) -> str:
    labels = {
        "quality_report": "Quality report",
        "postmortem": "Postmortem",
        "memory_event": "Memory event",
        "working_memory": "Working memory",
        "repo_guidance": "Repository guidance artifact",
        "memory_context": "Memory context",
        "verification": "Verification log",
        "quality_gate": "Quality gate",
        "governance_decision": "Governance decision",
        "ci_status": "CI status",
        "live_action": "Live action log",
        "review_event": "PR review log",
        "tool_result": "Tool result",
        "trace_event": "Trace event",
        "artifact": "Run artifact",
    }
    return f"{labels.get(record_type, 'Run artifact')}: {source_path}"


def reason_for_record(record_type: str, intent: str) -> str:
    if intent == "verification":
        return f"matched verification query; {record_type} is a relevant evidence source"
    if intent == "repo_context":
        return f"matched repository-context query; {record_type} can carry guidance or memory facts"
    if intent == "failure":
        return f"matched failure query; {record_type} can explain prior failures or recovery"
    if intent == "external_write":
        return f"matched external-write query; {record_type} can describe PR, CI, or lifecycle state"
    return f"matched query; {record_type} is indexed run evidence"


def snippet(text: str, limit: int = 800) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 24] + "\n[history result truncated]"
