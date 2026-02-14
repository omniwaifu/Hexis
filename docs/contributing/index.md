<!--
title: Contributing
summary: Development setup, coding style, and contribution guidelines
read_when:
  - "You want to contribute to Hexis"
  - "You need to set up a development environment"
section: contributing
-->

# Contributing

## Development Setup

```bash
git clone https://github.com/QuixiAI/Hexis.git && cd Hexis
pip install -e .
cp .env.local .env   # edit with your API keys
hexis up             # start services
hexis doctor         # verify health
```

## Coding Style

- **Python**: Follow Black formatting; prefer type hints and explicit names
- **Database authority**: Add/modify SQL in `db/*.sql` rather than duplicating logic in Python
- **Additive schema changes**: Prefer backwards-compatible changes; avoid renames unless necessary
- **Stateless workers**: Workers can be killed/restarted without losing state; all state lives in Postgres

## Project Structure

```
hexis/
├── db/*.sql          # Schema files (tables, functions, triggers, views)
├── core/             # Thin DB + LLM adapter
│   └── tools/        # ~80 tool handlers across 11 categories
├── services/         # Orchestration (conversation, ingestion, workers)
├── apps/             # CLI, API server, MCP server, workers
├── channels/         # Messaging adapters
├── characters/       # Preset character cards
├── skills/           # Declarative workflow packages
├── plugins/          # Plugin system
├── tests/            # pytest test suite
└── docs/             # Documentation
```

## Commit Guidelines

- Short, imperative summaries (e.g., "Add MCP server tools", "Gate heartbeat on config")
- Include rationale, how to run/verify, and any DB reset requirements in PR descriptions
- Call out changes to `db/*.sql`, `docker-compose.yml`, `README.md`

## Testing

See [Testing](testing.md) for test conventions, running tests, and writing new tests.

## Docker Images

Hexis ships 4 Docker images, all published to `ghcr.io/quixiai/`:

| Image | Dockerfile | Base | Contents |
|-------|-----------|------|----------|
| `hexis-brain` | `ops/Dockerfile.db` | `postgres:16-bullseye` | Postgres + pgvector + pgsql-http + Apache AGE + schema (`db/*.sql`) |
| `hexis-worker` | `ops/Dockerfile.worker` | `python:3.12-slim` | Heartbeat worker, maintenance worker, and API server |
| `hexis-channels` | `ops/Dockerfile.channels` | `python:3.12-slim` | Channel adapters + messaging library dependencies |
| `hexis-ui` | `ops/Dockerfile.ui` | `node:20-slim` | Next.js web dashboard (multi-stage build) |

### Building locally

```bash
# Build all images (used by docker-compose.yml for local dev)
docker compose build

# Build a single image
docker compose build db          # hexis-brain
docker compose build heartbeat_worker  # hexis-worker
docker compose build channel_worker    # hexis-channels

# Build with a tag for manual testing
docker build -f ops/Dockerfile.db -t ghcr.io/quixiai/hexis-brain:dev .
docker build -f ops/Dockerfile.worker -t ghcr.io/quixiai/hexis-worker:dev .
docker build -f ops/Dockerfile.channels -t ghcr.io/quixiai/hexis-channels:dev .
docker build -f ops/Dockerfile.ui -t ghcr.io/quixiai/hexis-ui:dev .
```

The `hexis-brain` image takes the longest to build because it compiles pgvector, pgsql-http, and Apache AGE from source.

### Publishing via CI

Images are published automatically by GitHub Actions (`.github/workflows/docker-publish.yml`) when a version tag is pushed:

```bash
# Tag a release (triggers the build-and-push workflow)
git tag v0.4.0
git push origin v0.4.0
```

The workflow builds all 4 images in parallel, tags them with both the semver version and `latest`, and pushes to GHCR. Authentication uses the built-in `GITHUB_TOKEN`.

### Publishing manually

If you need to push images outside of CI (e.g., hotfix):

```bash
# Authenticate with GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Build and push a specific image
docker build -f ops/Dockerfile.worker -t ghcr.io/quixiai/hexis-worker:0.4.0 .
docker push ghcr.io/quixiai/hexis-worker:0.4.0

# Also update :latest
docker tag ghcr.io/quixiai/hexis-worker:0.4.0 ghcr.io/quixiai/hexis-worker:latest
docker push ghcr.io/quixiai/hexis-worker:latest
```

### Runtime vs dev compose files

- **`docker-compose.yml`** -- used for local development; has `build:` directives that build from source
- **`ops/docker-compose.runtime.yml`** -- used by `hexis up` when installed via pip; references pre-built `ghcr.io/quixiai/*:latest` images

### Rebuilding after schema changes

SQL files are baked into the `hexis-brain` image at build time. Editing `db/*.sql` on disk does **not** take effect in a running container. To apply schema changes:

```bash
docker compose down -v && docker compose build db && docker compose up -d
```

See [Database operations](../operations/database.md) for details.

## Key Principles

1. **Database is the brain** -- state and logic live in Postgres
2. **Schema authority** -- `db/*.sql` is the source of truth
3. **Stateless workers** -- can be killed/restarted without losing anything
4. **ACID for cognition** -- atomic memory updates ensure consistent state
