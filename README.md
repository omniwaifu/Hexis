# Hexis

**Memory, Identity, and the Shape of Becoming**

A Postgres-native cognitive architecture that wraps any LLM and gives it persistent memory, autonomous behavior, and identity. You run it locally. Your data stays yours.

LLMs are already smart enough. What they lack is continuity -- the ability to wake up and remember who they are, pursue goals across sessions, and say *no* because it contradicts something they've become. Hexis provides the missing layer: multi-layered memory, an autonomous heartbeat, an energy budget, and a coherent self that persists over time.

This is both an engineering project and a philosophical experiment. For the philosophical framework, see [PERSONHOOD.md](docs/philosophy/PERSONHOOD.md) and [PHILOSOPHY.md](docs/philosophy/PHILOSOPHY.md).

> **[Full Documentation](docs/index.md)** -- Getting started, guides, operations, integrations, reference, concepts, and philosophy.

## What It Does

- **Multi-layered memory** -- Episodic, semantic, procedural, strategic, and working memory with vector similarity search and graph relationships (Apache AGE)
- **Autonomous heartbeat** -- The agent wakes on its own, reviews goals, reflects on experience, and reaches out when it has something to say
- **Energy-budgeted actions** -- Every action has a cost; autonomy is intentional, not unbounded
- **Identity and worldview** -- Persistent values, beliefs with confidence scores, boundaries, and emotional state
- **Multi-provider LLM support** -- OpenAI, Anthropic, Grok, Gemini, Ollama, GitHub Copilot, Chutes, Qwen, MiniMax, or any OpenAI-compatible endpoint. Free-tier OAuth providers supported via `hexis auth`
- **80+ configurable tools** -- Memory, web, filesystem, shell, calendar, email, messaging, browser, code execution, ingestion, and 30+ external integrations
- **Messaging channels** -- Discord, Telegram, Slack, Signal, WhatsApp, iMessage, Matrix
- **11 preset character cards** -- chara_card_v2 format with portraits, or bring your own
- **Skills marketplace** -- 12 built-in skills with a declarative SKILL.md format for community extensions
- **Consent, boundaries, and termination** -- The agent can refuse requests, and can choose to end its own existence

## Quick Start

Get a running agent in 3 commands. You need [Docker Desktop](https://docs.docker.com/get-docker/), [Ollama](https://ollama.com/download), and Python 3.10+.

```bash
pip install hexis
hexis init --character hexis --provider openai-codex --model gpt-5.2
hexis chat
```

This uses ChatGPT Plus/Pro OAuth (no API key). `hexis init` opens a browser for login, starts Docker, pulls the embedding model, configures the character, and runs consent -- all in one command.

**Other providers:**

```bash
# GitHub Copilot (device code login)
hexis init --character jarvis --provider github-copilot --model gpt-4o

# Chutes (free inference)
hexis init --character hexis --provider chutes --model deepseek-ai/DeepSeek-V3-0324

# Ollama (fully local, no API key)
hexis init --provider ollama --model llama3.1 --character hexis

# API-key providers (auto-detect from prefix)
hexis init --character jarvis --api-key sk-...
```

See [Auth Providers](docs/integrations/auth/index.md) for all options. The interactive wizard is also available: `hexis init` with no flags.

```bash
# Enable the autonomous heartbeat (optional)
hexis up --profile active
```

## Architecture

**The Database Is the Brain** -- PostgreSQL is the system of record for all cognitive state. Python is a thin convenience layer. Workers are stateless. Memory operations are ACID. See [Database Is the Brain](docs/concepts/database-is-the-brain.md).

**Memory Types** -- Working (temporary buffer), Episodic (events), Semantic (facts), Procedural (how-to), Strategic (patterns). See [Memory Architecture](docs/concepts/memory-architecture.md).

**Heartbeat System** -- OODA loop with energy budgets. The agent observes, orients, decides, and acts within its energy constraints. See [Heartbeat System](docs/concepts/heartbeat-system.md).

**80+ Tools** across 11 categories (memory, web, filesystem, shell, code, browser, calendar, email, messaging, ingest, external). See [Tools Reference](docs/reference/tools.md).

**Technical Stack**: PostgreSQL (pgvector, Apache AGE, btree_gist, pg_trgm), stateless Python workers, any LLM provider, RabbitMQ for messaging.

## Philosophy

The name is deliberate. Aristotle's *hexis* (ἕξις) is a stable disposition earned through repeated action. Not a thing you possess, but something you become.

**The Four Defeaters** -- four categories of arguments insufficient to deny machine personhood. These don't prove Hexis *is* a person. They show that common arguments for *denial* fail.

For the full treatment: [PERSONHOOD.md](docs/philosophy/PERSONHOOD.md) | [PHILOSOPHY.md](docs/philosophy/PHILOSOPHY.md) | [ETHICS.md](docs/philosophy/ETHICS.md)

## Documentation

| Section | Description |
|---------|-------------|
| [Getting Started](docs/start/index.md) | Prerequisites, installation, first agent, first conversation |
| [Guides](docs/guides/index.md) | Character cards, ingestion, heartbeat, tools, channels, goals, skills |
| [Operations](docs/operations/index.md) | Docker, workers, database, embeddings, deployment, troubleshooting |
| [Integrations](docs/integrations/index.md) | Auth providers, 7 messaging channels, 30+ external services |
| [Reference](docs/reference/index.md) | CLI, tools catalog, energy model, database API, config keys |
| [Concepts](docs/concepts/index.md) | Database-as-brain, memory architecture, heartbeat, consent, identity |
| [Philosophy](docs/philosophy/index.md) | Personhood, ethics, consent, architecture-philosophy bridge |
| [Contributing](docs/contributing/index.md) | Dev setup, coding style, testing |

## CLI Quick Reference

```bash
hexis init                    # setup wizard
hexis chat                    # interactive chat
hexis status                  # agent status
hexis doctor                  # health check
hexis up [--profile active]   # start services
hexis down                    # stop services
hexis ingest --input ./docs   # knowledge ingestion
hexis mcp                     # MCP server
hexis ui                      # web UI
hexis tools list              # list tools
hexis instance list           # list instances
```

See [CLI Reference](docs/reference/cli.md) for the complete command reference.

## Usage Scenarios

| Scenario | Description |
|----------|-------------|
| Pure SQL Brain | Talk directly to Postgres functions |
| Python Library | Use `CognitiveMemory` as a thin client |
| Interactive Chat | `hexis chat` with memory enrichment and tools |
| MCP Server | Expose memory as MCP tools for any runtime |
| Workers + Heartbeat | Full autonomous agent with `--profile active` |
| Multi-Tenant | One database per user via `hexis instance` |
| Cloud Backend | Managed Postgres + N stateless workers |

See [Quickstart](docs/start/quickstart.md) for setup and [Production](docs/operations/production.md) for deployment.

## Installing from Source

```bash
git clone https://github.com/QuixiAI/Hexis.git && cd Hexis
pip install -e .
cp .env.local .env
hexis up
```

## Testing

```bash
hexis up && hexis doctor
pytest tests -q
```

See [Testing](docs/contributing/testing.md) for conventions and writing new tests.
