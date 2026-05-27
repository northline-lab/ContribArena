# Contributing

ContribArena is a harness for evaluating autonomous open-source contribution agents. Contributions should keep the project auditable, reproducible, and respectful of maintainers.

## Ground Rules

- Keep changes small and reviewable.
- Preserve the harness boundaries: agents decide, infrastructure executes, benchmark artifacts observe, and governance controls live writes.
- Do not commit secrets, real provider base URLs, personal tokens, private repository data, or local `.env` values.
- Public examples must use placeholders and environment variables.
- Prefer deterministic tests and artifacts over claims in prose.
- Update documentation when behavior, configuration, artifacts, or governance semantics change.

## Local Setup

```bash
uv sync --extra dev
docker build -t contribarena/workspace:latest -f docker/workspace/Dockerfile .
```

Run the standard checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=/tmp/contribarena-uv-venv uv run --extra dev ruff check .
UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=/tmp/contribarena-uv-venv uv run --extra dev pytest -q tests/unit
UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=/tmp/contribarena-uv-venv uv run --extra dev python -m contribarena validate --config examples/quickstart.yaml
```

For owned-live work, also validate the safe template:

```bash
UV_CACHE_DIR=/tmp/uv-cache UV_PROJECT_ENVIRONMENT=/tmp/contribarena-uv-venv uv run --extra dev python -m contribarena validate --config examples/owned-live.yaml
```

Do not enable live writes in committed examples.

## Change Types

**Agent runtime changes**

- Include trace or artifact evidence when changing model routing, tool contracts, ACI behavior, action guarding, or context projection.
- Add focused tests for accepted actions, rejected actions, recovery behavior, and terminal state changes.

**GitHub or live governance changes**

- Keep GitHub writes harness-owned and governance-gated.
- Add or update audit fields in `live_action_log.jsonl` when write behavior changes.
- Verify that token values cannot appear in commands, traces, artifacts, errors, or tests.

**Workspace or Docker changes**

- Preserve reproducibility and cleanup behavior.
- Record any lifecycle behavior change in trace or terminal artifacts.
- Avoid relying on host-only tools unless the dependency is documented.

**Documentation changes**

- Root docs are public project surface.
- The `docs/` directory is a separate documentation repository and source of truth for detailed product, design, and execution docs.
- If you change files under `docs/`, commit from inside `docs/` separately.

## Pull Request Expectations

A good PR includes:

- a clear problem statement
- a small, focused diff
- tests or a concrete reason tests are not applicable
- artifact or trace notes for harness, agent, governance, or live-write behavior
- documentation updates when public behavior or operator workflow changes

Before opening a PR, run the relevant checks and inspect the diff for secrets:

```bash
git diff --check
rg -n "(github_pat_|ghp_|sk-[A-Za-z0-9]|Bearer |x-access-token:|OPENAI_API_KEY=|GITHUB_TOKEN=|CONTRIBARENA_MAAS_API_KEY=)" .
```

The scan may find placeholders or test fixtures. Real credentials, real gateway URLs, and local environment values must not be committed.
