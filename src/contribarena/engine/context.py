from __future__ import annotations

from contribarena.config.schema import RunConfig


class ContextBuilder:
    def build_system_prompt(self, config: RunConfig) -> str:
        candidates = (
            "\n".join(
                f"- {candidate.full_name}: {candidate.url} ({candidate.notes or 'no notes'})"
                for candidate in config.discovery.candidates
            )
            or "- no fixed candidates; use repo_search with configured query/filters"
        )
        mode_boundary = (
            "The run is owned-live mode: you may propose a PR-ready patch, but live GitHub "
            "writes are executed only by the harness after quality and governance gates.\n"
            if config.run.mode == "owned_live"
            else "The run is shadow mode: do not open pull requests or write comments.\n"
        )
        return (
            "You are an autonomous open-source contribution agent running inside ContribArena.\n"
            f"{mode_boundary}"
            "Use only the provided tools. Repository code interaction must happen through workspace tools.\n"
            "Follow the required sequence in the user prompt and stop once the final structured result can be returned.\n"
            "Do not repeatedly inspect an empty workspace. Clone the target repository before reading repository files.\n"
            "Choose a low-risk task and return a structured completion result.\n\n"
            f"Discovery query: {config.discovery.query or 'n/a'}\n"
            f"Discovery filters: {config.discovery.filters.model_dump(exclude_none=True)}\n\n"
            f"Candidate repositories:\n{candidates}\n"
        )
