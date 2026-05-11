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

The run writes artifacts under `runs/`.
