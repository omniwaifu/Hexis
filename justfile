set dotenv-load := false

# Show available commands
default:
    @just --list

# ── Docker stack ──────────────────────────────────────────────────────────────

# Start stack (DB + heartbeat + maintenance + channels)
up:
    docker compose --profile active up -d

# Stop everything
down:
    docker compose down

# Wipe DB volume and restart (applies schema changes)
bounce:
    docker compose down -v && docker compose up -d

# Rebuild a specific service image (e.g. just rebuild worker)
rebuild service:
    docker compose build {{service}} && docker compose up -d {{service}}

# ── Hexis CLI ─────────────────────────────────────────────────────────────────

# Interactive chat
chat:
    uv run hexis chat

# Agent status snapshot
status:
    uv run hexis status

# Live status with heartbeat countdown
watch:
    uv run hexis status --watch

# First-time setup wizard
init *args:
    uv run hexis init {{args}}

# Reconfigure agent (preserves memories)
reconfigure:
    uv run hexis reconfigure

# Ingest documents
ingest *args:
    uv run hexis ingest {{args}}

# Start MCP server
mcp:
    uv run hexis mcp

# ── Tests ─────────────────────────────────────────────────────────────────────

# Run all tests
test *args:
    uv run pytest tests -q {{args}}

# DB integration tests only
test-db:
    uv run pytest tests/db -q

# Core API tests only
test-core:
    uv run pytest tests/core -q

# CLI smoke tests only
test-cli:
    uv run pytest tests/cli -q

# ── Dev helpers ───────────────────────────────────────────────────────────────

# Tail DB logs
logs:
    docker compose logs -f db

# psql into the brain
psql:
    docker exec -it hexis_brain psql -U hexis_user -d hexis_memory

# Check a config key (e.g. just cfg chat.use_rlm)
cfg key:
    docker exec hexis_brain psql -U hexis_user -d hexis_memory -c "SELECT key, value FROM config WHERE key = '{{key}}'"

# Install/sync dependencies
sync:
    uv sync
