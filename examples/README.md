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

Validate the M0.4 owned-live controller template with:

```bash
contribarena validate --config examples/owned-live.yaml
```

Validate the M0.5 external-live controller template with:

```bash
contribarena validate --config examples/external-live.yaml
```

Before running owned-live mode, copy the template locally, replace the owner,
repository, bot actor, and model provider values, set `GITHUB_TOKEN` for a
dedicated bot account, and flip `governance.live_enabled` to `true`.

Before running external-live mode, copy the template locally, replace the bot
actor and model provider values, set `GITHUB_TOKEN` for a dedicated bot account,
and keep conservative repo, organization, and global rate limits.

The run writes artifacts under `runs/`.
