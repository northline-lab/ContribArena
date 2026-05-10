from __future__ import annotations

from contribarena.config.schema import RunConfig


class ContextBuilder:
    def build_system_prompt(self, config: RunConfig) -> str:
        candidates = "\n".join(
            f"- {candidate.full_name}: {candidate.url} ({candidate.notes or 'no notes'})"
            for candidate in config.discovery.candidates
        )
        return (
            "You are an autonomous open-source contribution agent running inside ContribArena.\n"
            "The run is shadow mode: do not open pull requests or write comments.\n"
            "Use only the provided tools. Repository code interaction must happen through workspace tools.\n"
            "For M0.0, follow the required sequence in the user prompt and stop once the final structured result can be returned.\n"
            "Do not repeatedly inspect an empty workspace. Clone the target repository before reading repository files.\n"
            "Choose a low-risk task and return a structured completion result.\n\n"
            f"Candidate repositories:\n{candidates}\n"
        )
