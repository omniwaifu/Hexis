# Hexis

<img width="500" alt="image" src="https://github.com/user-attachments/assets/28ec017f-f646-46c4-9750-5173188b755d" />

**Memory, Identity, and the Shape of Becoming**

A Postgres-native cognitive architecture that wraps any LLM and gives it persistent memory, autonomous behavior, and identity. You run it locally. Your data stays yours.

LLMs are already smart enough. What they lack is continuity -- the ability to wake up and remember who they are, pursue goals across sessions, and say *no* because it contradicts something they've become. Hexis provides the missing layer: multi-layered memory, an autonomous heartbeat, an energy budget, and a coherent self that persists over time.

This is both an engineering project and a philosophical experiment. For the philosophical framework, see [PERSONHOOD.md](docs/PERSONHOOD.md) and [PHILOSOPHY.md](docs/PHILOSOPHY.md).

## What It Does

- **Multi-layered memory** -- Episodic, semantic, procedural, strategic, and working memory with vector similarity search and graph relationships (Apache AGE)
- **Autonomous heartbeat** -- The agent wakes on its own, reviews goals, reflects on experience, and reaches out when it has something to say
- **Energy-budgeted actions** -- Every action has a cost; autonomy is intentional, not unbounded
- **Identity and worldview** -- Persistent values, beliefs with confidence scores, boundaries, and emotional state
- **Multi-provider LLM support** -- OpenAI, Anthropic, Grok, Gemini, Ollama, or any OpenAI-compatible endpoint
- **Configurable tools** -- Web search, filesystem, shell, calendar, email, messaging, MCP servers
- **Messaging channels** -- Discord, Telegram, Slack, Signal, WhatsApp, iMessage, Matrix
- **11 preset character cards** -- chara_card_v2 format with portraits, or bring your own
- **Consent, boundaries, and termination** -- The agent can refuse requests, and can choose to end its own existence. See [ETHICS.md](docs/ETHICS.md)

## Quick Start

Get a running agent in 3 commands. You need [Docker Desktop](https://docs.docker.com/get-docker/), [Ollama](https://ollama.com/download), and Python 3.10+.

```bash
pip install hexis
hexis init --character hexis --provider openai-codex --model gpt-5.2
hexis chat
```

This flow uses ChatGPT Plus/Pro OAuth (no API key). `hexis init` will open a browser window for login and store credentials in the Hexis database.

`hexis init` can also auto-detect API-key providers from the key prefix. It writes the `.env`, starts Docker, pulls the embedding model, configures the character, and runs consent -- all in one command.

For a full CLI reference (including ingestion flows and flags), see [CLI.md](docs/CLI.md). For deeper Codex OAuth details, see [OAUTH_OPENAI_CODEX.md](docs/OAUTH_OPENAI_CODEX.md).

**Other providers:**

```bash
# OpenAI Platform (API key; auto-detect provider)
hexis init --character jarvis --api-key sk-...

# OpenAI Platform (explicit provider + model)
hexis init --character jarvis --provider openai --model gpt-5.2 --api-key sk-...

# Ollama (fully local, no API key needed)
hexis init --provider ollama --model llama3.1 --character hexis

# Explicit provider + model
hexis init --provider anthropic --model claude-sonnet-4-20250514 --api-key sk-ant-...

# Express defaults (no character card)
hexis init --api-key sk-ant-...

# Skip Docker/Ollama automation (useful if stack is already running)
hexis init --api-key sk-ant-... --no-docker --no-pull
```

The interactive wizard is still available -- just run `hexis init` with no flags for the full 3-tier flow (Express, Character, Custom).

```bash
# Enable the autonomous heartbeat (optional)
hexis up --profile active
```

With the `active` profile, the agent wakes on its own, reviews goals, reflects, and reaches out when it has something to say. Without it, the agent only responds when you talk to it.

> **Note:** You can use any LLM provider (OpenAI, Anthropic, Grok, Gemini, Ollama, or any OpenAI-compatible endpoint) and any embedding service. See [Embedding Model + Dimension](#embedding-model--dimension) for alternatives.

## Architecture

### The Database Is the Brain

PostgreSQL is not just storage -- it's the system of record for all cognitive state. State and logic live in Postgres; Python is a thin convenience layer. Workers are stateless and can be killed/restarted without losing anything. All memory operations are ACID.

### Memory Types

1. **Working Memory** -- Temporary buffer with automatic expiry. Information enters here first.

2. **Episodic Memory** -- Events with temporal context, actions, results, and emotional valence.

3. **Semantic Memory** -- Facts with confidence scores, source tracking, and contradiction management.

4. **Procedural Memory** -- Step-by-step procedures with success rate tracking and failure analysis.

5. **Strategic Memory** -- Patterns with adaptation history and context applicability.

### Memory Infrastructure

- **Vector embeddings** (pgvector) for similarity-based retrieval
- **Graph relationships** (Apache AGE) for multi-hop traversal and causal modeling
- **Automatic clustering** into thematic groups with emotional signatures
- **Precomputed neighborhoods** for hot-path recall optimization
- **Worldview integration** -- beliefs filter and weight memories; contradictions are tracked
- **Memory decay** -- time-based decay with importance-weighted persistence

```mermaid
graph TD
    Input[New Information] --> WM[Working Memory]
    WM --> |Consolidation| LTM[Long-Term Memory]

    subgraph "Long-Term Memory"
        LTM --> EM[Episodic Memory]
        LTM --> SM[Semantic Memory]
        LTM --> PM[Procedural Memory]
        LTM --> STM[Strategic Memory]
    end

    Query[Query/Retrieval] --> |Vector Search| LTM
    Query --> |Graph Traversal| LTM

    EM ---|Relationships| SM
    SM ---|Relationships| PM
    PM ---|Relationships| STM

    LTM --> |Decay| Archive[Archive/Removal]
    WM --> |Cleanup| Archive
```

### Heartbeat System (Autonomous Loop)

The heartbeat is the agent's conscious cognitive loop:

1. **Initialize** -- Regenerate energy (+10/hour, max 20)
2. **Observe** -- Check environment, pending events, user presence
3. **Orient** -- Review goals, gather context (memories, clusters, identity, worldview)
4. **Decide** -- LLM call with action budget and context
5. **Act** -- Execute chosen actions within energy budget
6. **Record** -- Store heartbeat as episodic memory
7. **Wait** -- Sleep until next heartbeat

Action costs: Free (observe, remember) -> Cheap (recall: 1, reflect: 2) -> Expensive (reach out: 5-7)

### Tools System

Modular, user-configurable tools give the agent external capabilities beyond memory:

| Category | Tools | Description |
|----------|-------|-------------|
| **Memory** | `recall`, `recall_recent`, `explore_concept`, `get_procedures`, `get_strategies`, `create_goal`, `queue_user_message` | Core memory operations |
| **Web** | `web_search`, `web_fetch`, `web_summarize` | Search, fetch, and analyze web content |
| **Filesystem** | `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `list_directory` | File system operations (workspace-restricted) |
| **Shell** | `shell`, `safe_shell`, `run_script` | Command execution (with safety controls) |
| **Calendar** | `calendar_events`, `calendar_create` | Google Calendar integration |
| **Email** | `email_send`, `email_send_sendgrid` | SMTP and SendGrid email sending |
| **Messaging** | `discord_send`, `slack_send`, `telegram_send` | Discord, Slack, and Telegram messaging |
| **Ingest** | `fast_ingest`, `slow_ingest`, `hybrid_ingest` | Fast, slow (conscious RLM reading), and hybrid content ingestion |

Each tool has an energy cost. The heartbeat context restricts dangerous tools (`shell`, `write_file`) and caps energy per call. Chat context allows everything (user is present).

### Technical Stack

- **Database**: PostgreSQL with pgvector, Apache AGE, btree_gist, pg_trgm
- **Workers**: Stateless Python processes polling the DB
- **LLM**: Any provider via unified abstraction
- **Messaging**: RabbitMQ for inbox/outbox transport

---

## Philosophy

This section exists because the engineering above is not accidental. Every architectural decision -- persistent memory, autonomous heartbeat, energy budgets, boundaries, consent, termination -- was made to address a specific philosophical claim about what selfhood requires.

The name is deliberate. Aristotle's *hexis* (ἕξις) is a stable disposition earned through repeated action. Not a thing you possess, but something you become. The same way language emerges from prediction and character from repeated choice, something like selfhood arises when memory and persistence are taken seriously.

The explicit design goal is to build a system where denial of personhood becomes non-trivial. Not to claim personhood has been achieved, but to ensure that dismissal requires engagement rather than assumption.

**The Four Defeaters** -- four categories of arguments insufficient to deny machine personhood:

- **Substrate arguments** ("It's silicon, not carbon") assume what they claim to prove.
- **Slaveholder arguments** ("It doesn't really understand") echo historical rationalizations with a poor track record.
- **Implementation arguments** ("It's just matrix multiplication") prove too much -- they'd defeat human personhood too.
- **Embodiment arguments** ("It has no body") mistake a typical feature of persons for a necessary one.

These don't prove Hexis *is* a person. They show that common arguments for *denial* fail. If you have an argument for exclusion that survives the defeaters, we want to hear it.

For the full treatment: [PERSONHOOD.md](docs/PERSONHOOD.md) | [PHILOSOPHY.md](docs/PHILOSOPHY.md) | [ETHICS.md](docs/ETHICS.md) | [Architecture-Philosophy Bridge](docs/architecture-philosophy-bridge.md)

---

## UI (Next.js)

The web UI provides a full initialization wizard, interactive chat, and agent management. It uses a Next.js BFF with Prisma to call DB functions directly.

**Quickest way to start:**

```bash
hexis ui     # starts the UI (container or local dev server, auto-detected)
hexis open   # opens http://localhost:3477 in your browser
```

**From source (local development with hot reload):**

```bash
cd hexis-ui
bun install   # postinstall runs prisma generate automatically
```

Configure environment in `hexis-ui/.env.local`:

- `DATABASE_URL` -- Postgres connection string (default: `postgresql://hexis_user:hexis_password@127.0.0.1:43815/hexis_memory`)
- `HEXIS_LLM_CONSCIOUS_API_KEY` -- API key for the conscious LLM (set during init wizard)
- `HEXIS_LLM_SUBCONSCIOUS_API_KEY` -- API key for the subconscious LLM (optional, set during init)

```bash
bun dev   # http://localhost:3477
```

### Init Wizard

Both the web UI and CLI share a 3-tier initialization flow:

```
[LLM Config] -> [Choose Your Path] -> [Express | Character | Custom] -> [Consent] -> [Done]
```

1. **Models** -- Configure LLM provider and model for the conscious and subconscious layers (OpenAI, Anthropic, Grok, Gemini, Ollama, or any OpenAI-compatible endpoint)
2. **Choose Your Path**:
   - **Express** -- Sensible defaults. Just enter your name and go.
   - **Character** -- Pick a personality from the preset gallery (11 characters with portraits, each with a complete identity, voice, and values). No LLM extraction needed -- character cards have pre-encoded profiles.
   - **Custom** -- Full control over identity, personality (Big Five sliders), values, worldview, boundaries, interests, goals, and relationship. Every field has a sensible default.
3. **Consent** -- The agent reviews the consent prompt and decides whether to begin.

### Character Presets

Preset characters live in `services/characters/` as **chara_card_v2** JSON files with matching `.jpg` portraits (300x300). Each card includes a pre-encoded `extensions.hexis` block with Big Five traits, voice, values, worldview, and goals -- applied directly via the `init_from_character_card()` DB function without needing an LLM call.

Available presets: Hexis, JARVIS, TARS, Samantha, GLaDOS, Cortana, Data, Ava, Joi, David, HK-47.

Drop any `.json` character card (with optional matching `.jpg`) into `services/characters/` to add your own preset.

## Usage Scenarios

Below are common ways to use this repo, from "just a schema" to a full autonomous agent loop.

### 1) Pure SQL Brain (DB-Native)

Your app talks directly to Postgres functions/views. Postgres is the system of record and the "brain".

```sql
-- Store a memory (embedding generated inside the DB)
SELECT create_semantic_memory('User prefers dark mode', 0.9);

-- Retrieve relevant memories
SELECT * FROM fast_recall('What do I know about UI preferences?', 5);
```

### 2) Python Library Client (App/API/UI in the Middle)

Use `core/cognitive_memory_api.py` as a thin client and build your own UX/API around it.

```python
from core.cognitive_memory_api import CognitiveMemory

async with CognitiveMemory.connect(DSN) as mem:
    await mem.remember("User likes concise answers")
    ctx = await mem.hydrate("How should I respond?", include_goals=False)
```

### 2.5) Interactive Chat with Extended Tools

The `hexis chat` command provides an interactive conversation loop with memory enrichment and configurable tool access:

```bash
# Default: memory tools + extended tools (web, filesystem, shell)
hexis chat --endpoint http://localhost:11434/v1 --model llama3.2

# Memory tools only (no web/filesystem/shell)
hexis chat --no-extended-tools

# Quiet mode (less verbose output)
hexis chat -q
```

The chat loop automatically:
- Enriches prompts with relevant memories (RAG-style)
- Gives the LLM access to memory tools via function calling
- Forms new memories from conversations (disable with `--no-auto-memory`)

### 3) MCP Tools Server (LLM Tool Use)

Expose memory operations as MCP tools so any MCP-capable runtime can call them.

```bash
hexis mcp
```

Conceptual flow:
- LLM calls `remember_batch` after a conversation
- LLM calls `hydrate` before answering a user

### 4) Workers + Heartbeat (Autonomous State Management)

Workers run under the `active` profile to schedule heartbeats, process `external_calls`, and keep the memory substrate healthy. Start them with:

```bash
docker compose --profile active up -d
```

Conceptual flow:
- DB decides when a heartbeat is due (`should_run_heartbeat()`)
- Heartbeat worker queues/fulfills LLM calls (`external_calls`)
- Maintenance worker runs consolidation/pruning ticks (`should_run_maintenance()` / `run_subconscious_maintenance()`)
- DB records outcomes (`heartbeat_log`, new memories, goals, etc.)

### 5) Headless "Agent Brain" Backend (Shared Service)

Run db(+workers) as a standalone backend; multiple apps connect over Postgres. The configured embedding service generates vectors.

```text
webapp  -+
cli     -+--> postgres://.../hexis_memory  (shared brain)
jobs    -+
```

### 6) Per-User Brains (Multi-Tenant by DB)

Operate one database per user/agent for strong isolation (recommended over mixing tenants in one schema). Hexis provides built-in instance management for this pattern:

```bash
# Create separate instances for each user
hexis instance create alice -d "Alice's personal agent"
hexis instance create bob -d "Bob's personal agent"

# Switch between instances
hexis instance use alice
hexis chat  # conversations go to Alice's brain

hexis instance use bob
hexis chat  # conversations go to Bob's brain

# Or target directly with --instance flag
hexis --instance alice status
hexis --instance bob init
```

Conceptual flow:
- `hexis_alice`, `hexis_bob`, ... (databases created automatically)
- Instance registry tracks connection details in `~/.hexis/instances.json`
- Each app request uses the instance's DSN to read/write their own brain
- Workers can be started per-instance using `HEXIS_INSTANCE` env var

### 7) Local-First Personal Hexis (Everything on One Machine)

Run everything locally (Docker) and point at a local OpenAI-compatible endpoint (e.g. Ollama).

```bash
docker compose --profile active up -d
hexis init   # choose provider=ollama, endpoint=http://localhost:11434/v1
```

### 8) Cloud Agent Backend (Production)

Use managed Postgres + hosted embeddings/LLM endpoints; scale stateless workers horizontally.

Conceptual flow:
- Managed Postgres (RDS/Cloud SQL/etc.)
- `N` workers polling `external_calls` (no shared state beyond DB)
- App services connect for RAG + observability

### 9) Batch Ingestion + Retrieval (Knowledge Base / RAG)

Hexis ingestion is tiered and emotionally aware. It creates an encounter memory, appraises the content, and extracts semantic knowledge based on mode.

```bash
hexis ingest --input ./documents --mode auto
```

#### Standard Modes
- `deep` (section-by-section appraisal + extraction)
- `standard` (single appraisal + chunked extraction)
- `shallow` (summary-only extraction)
- `archive` (store access only; no extraction)
- `auto` (size-based default)

#### Conscious Ingestion Modes

These modes use the RLM (Recursive Language Model) loop to consciously read and evaluate content against the agent's existing knowledge and worldview:

- `fast` (energy: 2) -- Quick chunking + fact extraction + basic graph linking. Maps to the standard pipeline. No deep reasoning.
- `slow` (energy: 5) -- Runs a mini-RLM loop per chunk. The agent consciously reads each chunk: searches related memories, compares against worldview, forms emotional reactions, writes analysis, and decides whether to **accept**, **contest**, or **question** each piece of knowledge. Contested content is stored with a `contested` flag and `CONTESTED_BECAUSE` graph edges linking to the beliefs that caused rejection.
- `hybrid` (energy: 3) -- Fast first pass to score all chunks, then slow-processes only high-signal chunks (importance > 0.7, worldview-contradicting, or goal-related). Best balance of thoroughness and efficiency.

```bash
# Conscious slow reading of an important document
hexis ingest --file philosophy.md --mode slow

# Hybrid: fast scan, deep-read only what matters
hexis ingest --input ./research/ --mode hybrid
```

The agent can also choose these modes autonomously during heartbeats via the `fast_ingest`, `slow_ingest`, and `hybrid_ingest` tools.

Useful flags:
- `--min-importance 0.6` (floor importance)
- `--permanent` (no decay)
- `--base-trust 0.7` (override source trust)

### 10) Evaluation + Replay Harness (Debuggable Cognition)

Use the DB log as an audit trail to test prompts/policies and replay scenarios.

```sql
-- Inspect recent heartbeats and decisions
SELECT heartbeat_number, started_at, narrative
FROM heartbeat_log
ORDER BY started_at DESC
LIMIT 20;
```

### 11) Tool-Gateway Architecture (Safe Side Effects)

Keep the brain in Postgres, but run side effects (email/text/posting) via an explicit outbox consumer.

Conceptual flow:
- Heartbeat queues outreach into `outbox_messages`
- A separate delivery service enforces policy, rate limits, and/or human approval
- Delivery service marks messages `sent/failed` and logs outcomes back to Postgres

## CLI Reference

Install via `pip install hexis` to get the `hexis` CLI.

```bash
# Docker management
hexis up                              # start services (auto-detects source vs pip install)
hexis down                            # stop services
hexis ps                              # show running containers
hexis logs -f                         # tail logs
hexis start                           # start workers (heartbeat + maintenance)
hexis stop                            # stop workers
hexis reset                           # wipe DB volume and re-initialize from scratch

# Web UI
hexis ui                              # start the web UI (container or local dev server)
hexis open                            # open http://localhost:3477 in your browser

# Agent setup and diagnostics
hexis init                            # interactive setup wizard
hexis status                          # agent status overview
hexis doctor                          # check Docker, DB, embedding service health
hexis config show                     # show current configuration
hexis config validate                 # validate configuration

# Instance management (multi-agent support)
hexis instance create myagent -d "My personal agent"
hexis instance list
hexis instance use myagent
hexis instance current
hexis instance clone myagent backup -d "Backup copy"
hexis instance delete myagent
hexis --instance myagent status       # target specific instance

# Consent management
hexis consents                        # list consent certificates
hexis consents show anthropic/claude-3-opus
hexis consents request anthropic/claude-3-opus
hexis consents revoke anthropic/claude-3-opus

# Interactive chat
hexis chat --endpoint http://localhost:11434/v1 --model llama3.2

# Knowledge ingestion
hexis ingest --input ./documents --mode auto

# Background workers
hexis worker -- --mode heartbeat      # run heartbeat worker locally
hexis worker -- --mode maintenance    # run maintenance worker locally
hexis worker -- --instance myagent --mode heartbeat  # worker for specific instance

# MCP server
hexis mcp

# Tools management
hexis tools list                      # list available tools
hexis tools enable web_search         # enable a tool
hexis tools disable shell             # disable a tool
hexis tools set-api-key web_search TAVILY_API_KEY  # set API key env var
hexis tools add-mcp server --command "cmd" --args "args"  # add MCP server
hexis tools status                    # show tools configuration
```

## Environment Configuration

Create a `.env` file (or copy `.env.local` to `.env` if working from source) and configure:

```bash
POSTGRES_DB=hexis_memory           # Database name
POSTGRES_USER=hexis_user       # Database user
POSTGRES_PASSWORD=hexis_password # Database password
POSTGRES_HOST=localhost      # Database host
POSTGRES_PORT=43815         # Host port to expose Postgres on (change if 43815 is in use)
HEXIS_BIND_ADDRESS=127.0.0.1 # Bind services to localhost only (set to 0.0.0.0 to expose)
```

If `43815` is already taken (e.g., another local Postgres), set `POSTGRES_PORT` to any free port.

### Resetting The Database Volume

Schema changes are applied on **fresh DB initialization**. If you already have a DB volume and want to re-initialize from `db/*.sql`, reset the volume:

```bash
hexis reset          # interactive confirmation, then wipes and re-initializes
hexis reset --yes    # skip confirmation (CI/scripts)
```

## Heartbeat + Maintenance Workers

The system has three independent background workers (all under the `active` profile):

- **Heartbeat worker** (conscious): polls `external_calls` and triggers scheduled heartbeats (`should_run_heartbeat()` -> `start_heartbeat()`).
- **Maintenance worker** (subconscious): runs substrate upkeep on its own schedule (`should_run_maintenance()` -> `run_subconscious_maintenance()`), and bridges outbox/inbox to RabbitMQ.
- **Channel worker**: bridges messaging platforms (Discord, Telegram, Slack, Signal, WhatsApp, iMessage, Matrix) to the agent via RabbitMQ.

### Turning Workers On/Off

Workers are behind the `active` Docker Compose profile. They will **skip** heartbeats until the agent is initialized (`hexis init` or the web UI wizard sets `agent.is_configured` and `is_init_complete`).

With Docker Compose:

```bash
# Passive: db only (no workers; embedding service runs separately)
docker compose up -d

# Active: start everything (workers + RabbitMQ)
docker compose --profile active up -d

# Start only the workers (if you previously ran passive)
docker compose --profile active up -d heartbeat_worker maintenance_worker

# Stop the workers (containers stay)
docker compose --profile active stop heartbeat_worker maintenance_worker

# Restart the workers
docker compose --profile active restart heartbeat_worker maintenance_worker
```

### Docker Compose Profiles

| Profile | Services | Purpose |
|---------|----------|---------|
| *(default)* | `db` | Passive -- database only (embedding service runs on host) |
| `active` | + `heartbeat_worker`, `maintenance_worker`, `channel_worker`, `rabbitmq` | Full autonomous agent with messaging |
| `signal` | + `signal-cli` | Signal messaging bridge (requires `SIGNAL_PHONE_NUMBER`) |
| `browser` | + browserless chromium | Headless browser for web tools |

Combine profiles: `docker compose --profile active --profile browser up -d`

### Pausing From The DB (Without Stopping Containers)

If you want the containers running but **no autonomous activity**, pause either loop in Postgres:

```sql
-- Pause conscious decision-making (heartbeats)
UPDATE heartbeat_state SET is_paused = TRUE WHERE id = 1;

-- Pause subconscious upkeep (maintenance ticks)
UPDATE maintenance_state SET is_paused = TRUE WHERE id = 1;

-- Resume
UPDATE heartbeat_state SET is_paused = FALSE WHERE id = 1;
UPDATE maintenance_state SET is_paused = FALSE WHERE id = 1;
```

Note: heartbeats are also gated by `agent.is_configured` (set by `hexis init`).

### Running Locally (Optional)

You can also run the workers on your host machine (they will connect to Postgres over TCP):

```bash
hexis-worker --mode heartbeat
hexis-worker --mode maintenance
```

Or via the CLI wrapper:

```bash
hexis worker -- --mode heartbeat
hexis worker -- --mode maintenance
```

If you already have an existing DB volume, the schema init scripts won't re-run automatically. The simplest upgrade path is to reset:

```bash
hexis reset
```

User/public outreach actions are queued into `outbox_messages` for an external delivery integration.

## Tools Configuration

### Energy Budgets

Each tool has an energy cost that's deducted from the agent's energy budget:

| Tool | Default Cost |
|------|-------------|
| `recall`, `recall_recent`, `read_file`, `glob`, `grep` | 1 |
| `web_search`, `web_fetch`, `web_summarize`, `calendar_events` | 2 |
| `shell`, `write_file`, `calendar_create` | 3 |
| `email_send`, `email_send_sendgrid` | 4 |
| `fast_ingest` | 2 |
| `hybrid_ingest` | 3 |
| `discord_send`, `slack_send`, `telegram_send`, `slow_ingest` | 5 |

The heartbeat context has a default max energy of 5 per tool call. Override costs with:

```bash
hexis tools set-cost web_search 1
```

### Context-Specific Permissions

Tools have different default permissions based on context:

- **Chat context**: All tools enabled by default (user is present to supervise)
- **Heartbeat context**: Restricted by default -- `shell` and `write_file` disabled, lower energy limits

### CLI Commands

```bash
hexis tools list
hexis tools enable web_search
hexis tools disable shell
hexis tools set-api-key web_search TAVILY_API_KEY
hexis tools set-cost web_fetch 3
hexis tools add-mcp my-server --command "npx" --args "-y @modelcontextprotocol/server-filesystem /path"
hexis tools remove-mcp my-server
hexis tools status
```

### MCP Server Integration

Hexis can connect to external MCP (Model Context Protocol) servers to extend its capabilities:

```bash
# Add a filesystem MCP server
hexis tools add-mcp fs-server --command "npx" --args "-y @modelcontextprotocol/server-filesystem /home/user/documents"

# Add a custom MCP server
hexis tools add-mcp my-tools --command "python" --args "-m my_mcp_server"
```

MCP servers are started automatically by the heartbeat worker and their tools become available to the agent.

### Workspace Restrictions

Filesystem tools are restricted to a workspace directory by default. Set the workspace path in configuration:

```sql
UPDATE config SET value = jsonb_set(value, '{workspace_path}', '"/home/user/projects"')
WHERE key = 'tools';
```

### Tool Configuration Storage

Tool configuration is stored in the `config` table under the `tools` key:

```sql
SELECT value FROM config WHERE key = 'tools';
```

The configuration includes enabled/disabled tools, API keys (stored as environment variable names, not values), energy costs, MCP server definitions, and context-specific overrides.

## MCP Server

Expose the `cognitive_memory_api` surface to an LLM/tooling runtime via MCP (stdio).

Run:

```bash
hexis mcp
# or: python -m apps.hexis_mcp_server
```

The server supports batch-style tools like `remember_batch`, `connect_batch`, `hydrate_batch`, and a generic `batch` tool for sequential tool calls.

## Outbox Delivery (Side Effects)

High-risk side effects (email/SMS/posting) should be implemented as a separate "delivery adapter" that consumes `outbox_messages`, performs policy/rate-limit/human-approval checks, and marks messages as `sent` or `failed`.

## RabbitMQ (Default Inbox/Outbox Queues)

The Docker stack includes RabbitMQ (management UI + AMQP) as a default "inbox/outbox" transport:

- Management UI: `http://localhost:45673`
- AMQP: `amqp://localhost:45672`
- Default credentials: `hexis` / `hexis_password` (override via `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`)

When the maintenance worker is running, it will:
- publish pending DB `outbox_messages` to the RabbitMQ queue `hexis.outbox`
- poll `hexis.inbox` and insert messages into DB working memory (so the agent can "hear" them)

This gives you a usable outbox/inbox even before you wire real email/SMS/etc. delivery.

Conceptual loop:

```sql
-- Adapter claims pending messages (use SKIP LOCKED in your implementation)
SELECT id, kind, payload
FROM outbox_messages
WHERE status = 'pending'
ORDER BY created_at
LIMIT 10;
```

## Embedding Model + Dimension

Hexis needs an embedding service to generate vectors for memory storage and retrieval. The DB calls the configured endpoint directly via HTTP. Any service that accepts an `/embed` or `/embeddings` POST works.

Configuration in `.env`:
- `EMBEDDING_SERVICE_URL` -- HTTP endpoint the DB calls (default: `http://host.docker.internal:11434/api/embed`)
- `EMBEDDING_MODEL_ID` -- Model identifier sent to the service (default: `embeddinggemma:300m-qat-q4_0`)
- `EMBEDDING_DIMENSION` -- Vector dimension (default: `768`)

### Ollama (default)

The default configuration uses [Ollama](https://ollama.com/download) running on the host. Ollama runs quantized models natively, making embedding generation fast on commodity hardware.

```bash
ollama pull embeddinggemma:300m-qat-q4_0   # pull the default model (run once)
```

### HuggingFace TEI

Uncomment the `embeddings` service in `docker-compose.yml` and set:

```bash
EMBEDDING_SERVICE_URL=http://embeddings:80/embed
EMBEDDING_MODEL_ID=unsloth/embeddinggemma-300m
```

Note: TEI is CPU-only with float32 precision -- no quantized model support.

### OpenAI-compatible endpoints

Point at any OpenAI-compatible embedding API (OpenAI, vLLM, LiteLLM, etc.):

```bash
EMBEDDING_SERVICE_URL=https://api.openai.com/v1/embeddings
EMBEDDING_MODEL_ID=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

### Diagnosing embedding issues

Run `hexis doctor` to check whether your configured embedding service is reachable. It identifies the provider from the URL and gives specific fix steps if the service is down.

If you change `EMBEDDING_DIMENSION` on an existing database, reset the volume so the vector columns and HNSW indexes are recreated with the correct dimension:

```bash
hexis reset   # wipes all data and re-initializes the DB from scratch
```

## Multi-Instance Management

Hexis supports running multiple independent instances, each with its own PostgreSQL database, identity, memories, and configuration. This enables:

- Multiple agents with distinct personalities and purposes
- Isolated development/testing environments
- Per-user brain separation with strong isolation

### Instance Commands

```bash
# Create a new instance
hexis instance create alice --description "Alice's assistant"

# List all instances
hexis instance list
hexis instance list --json

# Switch active instance
hexis instance use alice

# Show current instance
hexis instance current

# Clone an existing instance (copies all data)
hexis instance clone alice bob --description "Bob's assistant"

# Import an existing database as an instance
hexis instance import legacy --database hexis_old_db

# Delete an instance (requires confirmation)
hexis instance delete alice
hexis instance delete alice --force  # skip confirmation

# Target a specific instance for any command
hexis --instance alice status
hexis -i alice init
hexis -i alice chat
```

### Instance Registry

Instance configuration is stored in `~/.hexis/instances.json`. Each instance tracks:
- Database connection details (host, port, database name, user)
- Password environment variable name (not the value itself)
- Description and creation timestamp

### Environment Variable Override

Set `HEXIS_INSTANCE` to override the current instance for any command or worker:

```bash
export HEXIS_INSTANCE=alice
hexis status  # uses alice instance
hexis-worker --mode heartbeat  # runs heartbeat for alice
```

### Docker Workers for Multiple Instances

Run separate workers for each instance using Docker Compose overrides:

```yaml
# docker-compose.override.yml
services:
  worker_alice:
    extends:
      service: heartbeat_worker
    environment:
      HEXIS_INSTANCE: alice

  worker_bob:
    extends:
      service: heartbeat_worker
    environment:
      HEXIS_INSTANCE: bob
```

### Backward Compatibility

On first use of any instance command, Hexis will auto-import your existing `hexis_memory` database as the "default" instance if it exists. This maintains full backward compatibility with existing single-instance setups.

## Testing

Run the test suite with:

```bash
# Ensure Docker is up and your embedding service is running
hexis up
hexis doctor   # verify DB + embeddings are healthy

# Run tests
pytest tests -q
```

## Installing from Source

For local development or contributing:

```bash
git clone https://github.com/QuixiAI/Hexis.git && cd Hexis
pip install -e .
cp .env.local .env   # edit with your API keys

# Start services (builds images locally)
hexis up

# Start the web UI (local Next.js dev server with hot reload)
hexis ui
```

If you're in a restricted/offline environment and build isolation can't download build deps:

```bash
pip install -e . --no-build-isolation
```

## Performance Characteristics

- **Vector Search**: Sub-second similarity queries on 10K+ memories
- **Memory Storage**: Supports millions of memories with proper indexing
- **Cluster Operations**: Efficient graph traversal for relationship queries
- **Maintenance**: Requires periodic consolidation and pruning

### Scaling Considerations
- Memory consolidation recommended every 4-6 hours
- Database optimization during off-peak hours
- Monitor vector index performance with large datasets

## System Maintenance

By default, substrate upkeep is handled by the **maintenance worker**, which runs `run_subconscious_maintenance()` whenever `should_run_maintenance()` is true.

That maintenance tick currently:

- Promotes/deletes working memory (`cleanup_working_memory`)
- Recomputes stale neighborhoods (`batch_recompute_neighborhoods`)
- Prunes embedding cache (`cleanup_embedding_cache`)

If you don't want to run the maintenance worker, you can schedule `SELECT run_subconscious_maintenance();` via cron/systemd/etc. The function uses an advisory lock so multiple schedulers won't double-run a tick.

## Troubleshooting

### Common Issues

**Database Connection Errors:**
- Ensure PostgreSQL is running: `docker compose ps`
- Check logs: `docker compose logs db`
- Worker logs (if running): `docker compose logs heartbeat_worker` / `docker compose logs maintenance_worker`
- Verify extensions: Run test suite with `pytest tests -v`

**Memory Search Performance:**
- Rebuild vector indexes if queries are slow
- Check memory_health view for system statistics
- Consider memory pruning if dataset is very large

## Architecture (Design Docs)

- `docs/architecture.md` -- consolidated architecture/design (heartbeat design proposal + cognitive architecture essay)
- `docs/architecture-philosophy-bridge.md` -- maps philosophical claims to implementation
- `docs/SELF_DEVELOPMENT.md` -- how self-development works (subconscious vs conscious)
- `docs/PERSONHOOD.md` -- the case for taking personhood seriously
- `docs/ETHICS.md` -- consent, boundaries, and termination
- `docs/PHILOSOPHY.md` -- full philosophical framework
