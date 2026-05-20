# Examples

Run the fixed-candidate skeleton with:

```bash
docker build -t contribarena/workspace:latest -f docker/workspace/Dockerfile .
contribarena run --config examples/quickstart.yaml
```

Validate a search-based M0.1 config with:

```bash
contribarena validate --config examples/github-search.yaml
```

Validate the M0.2.2 issue-solving config with:

```bash
contribarena validate --config examples/issue-solving.yaml
```

Validate the M0.4 owned-live template with:

```bash
contribarena validate --config examples/owned-live.yaml
```

Validate the Season 1 external-live template with:

```bash
contribarena validate --config examples/external-live.yaml
```

Before running owned-live mode, copy the template locally, replace the owner,
repository, bot actor, and model provider values, set `GITHUB_TOKEN` for a
dedicated bot account, and flip `governance.live_enabled` to `true`.

Before running external-live mode, copy the template locally, replace the bot
actor and model provider values, set `GITHUB_TOKEN` for a dedicated bot account,
and keep conservative repo, organization, and global rate limits. Season 1 is
started by configuration only: switch the season discovery profile to external,
then run `contribarena season start`.

The run writes artifacts under `runs/`.

Run owned-live or external-live seasons through the season runtime:

```bash
PYTHONPATH=src python -m contribarena season start --config owned-live.local.yaml
PYTHONPATH=src python -m contribarena season start --config external-live.local.yaml --verbose
```
