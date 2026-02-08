# Hexis CLI

This document is a practical guide and reference for the `hexis` CLI.

## Compose Files (Why There Are Two)

Hexis supports both:

- **Source checkout**: `./docker-compose.yml`
- **`pip install hexis` runtime**: `./ops/docker-compose.runtime.yml`

The CLI auto-detects which one to use (based on whether you're in a source tree).

## Common Flows

### Quick Start (Codex OAuth)

```bash
pip install hexis
hexis init --character hexis --provider openai-codex --model gpt-5.2
hexis chat
```

Notes:

- `openai-codex` is **ChatGPT subscription OAuth** (no API key). See `docs/OAUTH_OPENAI_CODEX.md`.
- To enable autonomy (optional): `hexis up --profile active`

### Stack Lifecycle

```bash
hexis up
hexis ps
hexis logs -f api
hexis down
```

### OAuth Login / Status / Logout (Codex)

```bash
hexis auth openai-codex login
hexis auth openai-codex status
hexis auth openai-codex logout
```

If you use `hexis init --provider openai-codex`, init will trigger login automatically if needed.

## Ingestion Guide (Practical)

Ingestion is intentionally a small loop:

1. Ingest content (some may be archived depending on size/mode).
2. Check status for pending/archived items.
3. Process archived items when you're ready.

### Ingest A File/Folder/URL

These forms are supported:

```bash
# File
hexis ingest --file ./notes.md

# Directory (recursive by default)
hexis ingest --input ./docs

# URL
hexis ingest --url https://example.com/article

# Stdin
cat ./notes.md | hexis ingest --stdin --stdin-type markdown --stdin-title "notes"
```

Under the hood, `hexis ingest ...` forwards to `python -m services.ingest ...`.
For convenience/backwards-compat, `hexis ingest --file ...` automatically maps to
`python -m services.ingest ingest --file ...`.

### Modes (When To Use What)

`--mode auto` chooses based on size (word count):

- Small: `deep`
- Medium: `standard`
- Very large: `archive` (registers an encounter for later processing)

Manual options:

- `deep`: per-section appraisal; best for shorter/high-value docs.
- `standard`: best default.
- `shallow`: first-section skim; fewer extractions.
- `archive`: store an encounter only (cheap), process later.
- `fast`: alias of `standard`.
- `slow` / `hybrid`: uses the RLM slow-ingest loop; slower but more deliberate.

### “I Ingested A Big Folder And Nothing Happened”

If the content is large, `auto` can choose `archive`. That is expected. Use:

```bash
hexis ingest status --pending
hexis ingest process --all-archived --limit 10
```

### Dedup Behavior

Ingestion receipts are content-hash based. If you ingest the same content again, it will be skipped.
If you want “re-ingest”, change the content or explicitly process archived items.

### Tips

- Start with `--mode auto`. Only force `deep` when you know the content is small.
- Use `--shallow`-style ingestion (`--mode shallow`) for broad corpora to avoid ballooning memory.
- Use `--permanent` sparingly; it disables decay for created memories.
- Use `--base-trust` when ingesting low-trust sources (e.g., random web pages).

## CLI Reference

### `hexis` (global)

Global flags:

- `-h`, `--help`: show grouped help
- `-V`, `--version`: print version
- `-i`, `--instance`: target a specific instance (sets `HEXIS_INSTANCE` for subprocesses)

### `hexis ...` (commands)

The sections below are a snapshot of `argparse` help text for every `hexis` command/subcommand.

### `hexis api`

```text
usage: hexis api [-h] [--host HOST] [--port PORT]

options:
  -h, --help   show this help message and exit
  --host HOST  Bind address (default: 127.0.0.1)
  --port PORT  Port (default: 43817)
```

### `hexis auth`

```text
usage: hexis auth [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                  {openai-codex} ...

positional arguments:
  {openai-codex}
    openai-codex        ChatGPT Plus/Pro (Codex OAuth)

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis auth openai-codex`

```text
usage: hexis auth openai-codex [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                               {login,status,logout} ...

positional arguments:
  {login,status,logout}
    login               Login via browser OAuth (PKCE)
    status              Show current OAuth status
    logout              Delete stored OAuth credentials

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis auth openai-codex login`

```text
usage: hexis auth openai-codex login [-h] [--dsn DSN]
                                     [--wait-seconds WAIT_SECONDS] [--no-open]
                                     [--timeout-seconds TIMEOUT_SECONDS]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --no-open             Don't open browser automatically
  --timeout-seconds TIMEOUT_SECONDS
                        Callback wait timeout (default: 60)
```

### `hexis auth openai-codex logout`

```text
usage: hexis auth openai-codex logout [-h] [--dsn DSN]
                                      [--wait-seconds WAIT_SECONDS] [--yes]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --yes, -y             Skip confirmation prompt
```

### `hexis auth openai-codex status`

```text
usage: hexis auth openai-codex status [-h] [--dsn DSN]
                                      [--wait-seconds WAIT_SECONDS] [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
```

### `hexis channels`

```text
usage: hexis channels [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                      {start,status,setup} ...

positional arguments:
  {start,status,setup}
    start               Start channel adapters (foreground)
    status              Show channel session counts
    setup               Configure a channel

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis channels setup`

```text
usage: hexis channels setup [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                            {discord,telegram,slack,signal,whatsapp,imessage,matrix}

positional arguments:
  {discord,telegram,slack,signal,whatsapp,imessage,matrix}
                        Channel to configure

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis channels start`

```text
usage: hexis channels start [-h]
                            [--channel {discord,telegram,slack,signal,whatsapp,imessage,matrix}]

options:
  -h, --help            show this help message and exit
  --channel {discord,telegram,slack,signal,whatsapp,imessage,matrix}, -c {discord,telegram,slack,signal,whatsapp,imessage,matrix}
                        Start specific channel(s). Default: all configured.
```

### `hexis channels status`

```text
usage: hexis channels status [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                             [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
```

### `hexis chat`

```text
usage: hexis chat [-h] ...

positional arguments:
  args        Arguments forwarded to chat

options:
  -h, --help  show this help message and exit
```

### `hexis config`

```text
usage: hexis config [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                    {show,validate} ...

positional arguments:
  {show,validate}
    show                Print config table
    validate            Validate required config keys and environment
                        references

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis config show`

```text
usage: hexis config show [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                         [--json] [--no-redact]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
  --no-redact           Do not redact sensitive values (unsafe)
```

### `hexis config validate`

```text
usage: hexis config validate [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis consents`

```text
usage: hexis consents [-h] {list,show,request,revoke} ...

positional arguments:
  {list,show,request,revoke}
    list                List all consent certificates
    show                Show a specific consent certificate
    request             Request consent from a model
    revoke              Revoke consent for a model

options:
  -h, --help            show this help message and exit
```

### `hexis consents list`

```text
usage: hexis consents list [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      Output JSON
```

### `hexis consents request`

```text
usage: hexis consents request [-h] model

positional arguments:
  model       Model identifier (provider/model_id)

options:
  -h, --help  show this help message and exit
```

### `hexis consents revoke`

```text
usage: hexis consents revoke [-h] [--reason REASON] model

positional arguments:
  model            Model identifier (provider/model_id)

options:
  -h, --help       show this help message and exit
  --reason REASON  Revocation reason
```

### `hexis consents show`

```text
usage: hexis consents show [-h] model

positional arguments:
  model       Model identifier (provider/model_id)

options:
  -h, --help  show this help message and exit
```

### `hexis demo`

```text
usage: hexis demo [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS] [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN
  --wait-seconds WAIT_SECONDS
  --json
```

### `hexis doctor`

```text
usage: hexis doctor [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS] [--json]
                    [--demo]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
  --demo                Run end-to-end sanity check against the DB
```

### `hexis down`

```text
usage: hexis down [-h]

options:
  -h, --help  show this help message and exit
```

### `hexis goals`

```text
usage: hexis goals [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                   {list,create,update,complete} ...

positional arguments:
  {list,create,update,complete}
    list                List goals by priority
    create              Create a new goal
    update              Change goal priority
    complete            Mark a goal as completed

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis goals complete`

```text
usage: hexis goals complete [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                            [--reason REASON]
                            goal_id

positional arguments:
  goal_id               Goal UUID

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --reason REASON       Completion reason
```

### `hexis goals create`

```text
usage: hexis goals create [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                          [--description DESCRIPTION]
                          [--priority {active,queued,backburner}]
                          [--source {user_request,curiosity,identity,derived,external}]
                          title

positional arguments:
  title                 Goal title

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --description DESCRIPTION, -d DESCRIPTION
                        Goal description
  --priority {active,queued,backburner}
  --source {user_request,curiosity,identity,derived,external}
```

### `hexis goals list`

```text
usage: hexis goals list [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                        [--priority {active,queued,backburner,completed,abandoned}]
                        [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --priority {active,queued,backburner,completed,abandoned}
                        Filter by priority
  --json                Output JSON
```

### `hexis goals update`

```text
usage: hexis goals update [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                          --priority
                          {active,queued,backburner,completed,abandoned}
                          [--reason REASON]
                          goal_id

positional arguments:
  goal_id               Goal UUID

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --priority {active,queued,backburner,completed,abandoned}
  --reason REASON       Reason for change
```

### `hexis help`

```text
usage: hexis help [-h] [help_command]

positional arguments:
  help_command  Command to show help for

options:
  -h, --help    show this help message and exit
```

### `hexis ingest`

```text
usage: hexis ingest [-h] ...

positional arguments:
  args        Arguments forwarded to ingest

options:
  -h, --help  show this help message and exit
```

### `hexis init`

```text
usage: hexis init [-h] ...

positional arguments:
  args        Arguments forwarded to init wizard

options:
  -h, --help  show this help message and exit
```

### `hexis instance`

```text
usage: hexis instance [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                      {create,list,use,current,delete,clone,import} ...

positional arguments:
  {create,list,use,current,delete,clone,import}
    create              Create a new instance
    list                List all instances
    use                 Switch to a different instance
    current             Show current instance
    delete              Delete an instance
    clone               Clone an instance
    import              Import an existing database as an instance

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis instance clone`

```text
usage: hexis instance clone [-h] [--description DESCRIPTION] source target

positional arguments:
  source                Source instance name
  target                Target instance name

options:
  -h, --help            show this help message and exit
  --description DESCRIPTION, -d DESCRIPTION
                        Description for new instance
```

### `hexis instance create`

```text
usage: hexis instance create [-h] [--description DESCRIPTION] name

positional arguments:
  name                  Instance name

options:
  -h, --help            show this help message and exit
  --description DESCRIPTION, -d DESCRIPTION
                        Instance description
```

### `hexis instance current`

```text
usage: hexis instance current [-h]

options:
  -h, --help  show this help message and exit
```

### `hexis instance delete`

```text
usage: hexis instance delete [-h] [--force] [--reason REASON] name

positional arguments:
  name             Instance name to delete

options:
  -h, --help       show this help message and exit
  --force          Skip confirmation
  --reason REASON  Reason for deletion (shared with the agent)
```

### `hexis instance import`

```text
usage: hexis instance import [-h] [--database DATABASE]
                             [--description DESCRIPTION]
                             name

positional arguments:
  name                  Instance name

options:
  -h, --help            show this help message and exit
  --database DATABASE   Database name (defaults to hexis_{name})
  --description DESCRIPTION, -d DESCRIPTION
                        Instance description
```

### `hexis instance list`

```text
usage: hexis instance list [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      Output JSON
```

### `hexis instance use`

```text
usage: hexis instance use [-h] name

positional arguments:
  name        Instance name to switch to

options:
  -h, --help  show this help message and exit
```

### `hexis logs`

```text
usage: hexis logs [-h] [--follow] [services ...]

positional arguments:
  services      Service name(s)

options:
  -h, --help    show this help message and exit
  --follow, -f  Follow log output
```

### `hexis mcp`

```text
usage: hexis mcp [-h] ...

positional arguments:
  args        Arguments forwarded to MCP server

options:
  -h, --help  show this help message and exit
```

### `hexis open`

```text
usage: hexis open [-h] [--port PORT]

options:
  -h, --help   show this help message and exit
  --port PORT  Port (default: 3477)
```

### `hexis ps`

```text
usage: hexis ps [-h]

options:
  -h, --help  show this help message and exit
```

### `hexis recall`

```text
usage: hexis recall [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                    [--limit LIMIT]
                    [--type {episodic,semantic,procedural,strategic,worldview,goal}]
                    [--json]
                    query

positional arguments:
  query                 Search query

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --limit LIMIT         Max results (default: 10)
  --type {episodic,semantic,procedural,strategic,worldview,goal}
                        Filter by memory type
  --json                Output JSON
```

### `hexis reset`

```text
usage: hexis reset [-h] [--yes]

options:
  -h, --help  show this help message and exit
  --yes, -y   Skip confirmation prompt
```

### `hexis schedule`

```text
usage: hexis schedule [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                      {list,create,delete} ...

positional arguments:
  {list,create,delete}
    list                List scheduled tasks
    create              Create a scheduled task
    delete              Delete a scheduled task

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis schedule create`

```text
usage: hexis schedule create [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                             --kind {once,interval,daily,weekly} --action
                             {queue_user_message,create_goal}
                             [--payload PAYLOAD] --schedule SCHEDULE
                             [--timezone TIMEZONE] [--description DESCRIPTION]
                             name

positional arguments:
  name                  Task name

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --kind {once,interval,daily,weekly}
                        Schedule kind
  --action {queue_user_message,create_goal}
                        Action kind
  --payload PAYLOAD     Action payload JSON
  --schedule SCHEDULE   Schedule config JSON (e.g. '{"time":"09:00"}')
  --timezone TIMEZONE
  --description DESCRIPTION, -d DESCRIPTION
```

### `hexis schedule delete`

```text
usage: hexis schedule delete [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                             [--force]
                             task_id

positional arguments:
  task_id               Task UUID

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --force               Hard delete (not just disable)
```

### `hexis schedule list`

```text
usage: hexis schedule list [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                           [--status {active,paused,disabled}] [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --status {active,paused,disabled}
  --json                Output JSON
```

### `hexis start`

```text
usage: hexis start [-h]

options:
  -h, --help  show this help message and exit
```

### `hexis status`

```text
usage: hexis status [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS] [--json]
                    [--no-docker] [--raw]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
  --no-docker           Skip docker compose checks
  --raw                 Show raw status (legacy format)
```

### `hexis stop`

```text
usage: hexis stop [-h]

options:
  -h, --help  show this help message and exit
```

### `hexis tools`

```text
usage: hexis tools [-h]
                   {list,enable,disable,set-api-key,set-cost,add-mcp,remove-mcp,status}
                   ...

positional arguments:
  {list,enable,disable,set-api-key,set-cost,add-mcp,remove-mcp,status}
    list                List all available tools
    enable              Enable a tool
    disable             Disable a tool
    set-api-key         Set an API key
    set-cost            Set energy cost for a tool
    add-mcp             Add an MCP server
    remove-mcp          Remove an MCP server
    status              Show tools configuration

options:
  -h, --help            show this help message and exit
```

### `hexis tools add-mcp`

```text
usage: hexis tools add-mcp [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                           [--args [ARGS ...]] [--env [ENV ...]]
                           name command

positional arguments:
  name                  Server name
  command               Command to run (e.g. 'npx')

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --args [ARGS ...], -a [ARGS ...]
                        Arguments
  --env [ENV ...], -e [ENV ...]
                        Environment variables (KEY=VALUE)
```

### `hexis tools disable`

```text
usage: hexis tools disable [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                           tool_name

positional arguments:
  tool_name             Name of the tool to disable

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis tools enable`

```text
usage: hexis tools enable [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                          tool_name

positional arguments:
  tool_name             Name of the tool to enable

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis tools list`

```text
usage: hexis tools list [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                        [--json] [--context {heartbeat,chat,mcp}]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
  --context {heartbeat,chat,mcp}
                        Filter by context
```

### `hexis tools remove-mcp`

```text
usage: hexis tools remove-mcp [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                              name

positional arguments:
  name                  Server name

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis tools set-api-key`

```text
usage: hexis tools set-api-key [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                               key_name value

positional arguments:
  key_name              API key name (e.g. 'tavily')
  value                 API key value or env reference (e.g.
                        'env:TAVILY_API_KEY')

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis tools set-cost`

```text
usage: hexis tools set-cost [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                            tool_name cost

positional arguments:
  tool_name             Name of the tool
  cost                  Energy cost

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
```

### `hexis tools status`

```text
usage: hexis tools status [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                          [--json]

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --json                Output JSON
```

### `hexis ui`

```text
usage: hexis ui [-h] [--no-open] [--port PORT]

options:
  -h, --help   show this help message and exit
  --no-open    Don't open browser automatically
  --port PORT  Port (default: 3477)
```

### `hexis up`

```text
usage: hexis up [-h] [--build] [--profile PROFILE]

options:
  -h, --help            show this help message and exit
  --build               Build images before starting
  --profile PROFILE, -p PROFILE
                        Compose profile(s)
```

### `hexis worker`

```text
usage: hexis worker [-h] ...

positional arguments:
  args        Arguments forwarded to worker

options:
  -h, --help  show this help message and exit
```

## Forwarded Command Reference

Some `hexis` commands are thin wrappers that forward into another module which has its own flags.

### `hexis init` (wizard module)

```text
usage: hexis init [-h] [--dsn DSN] [--wait-seconds WAIT_SECONDS]
                  [--api-key API_KEY] [--provider PROVIDER] [--model MODEL]
                  [--character CHARACTER] [--name NAME] [--no-docker]
                  [--no-pull]

Interactive bootstrap for Hexis (3-tier: Express, Character, Custom).

options:
  -h, --help            show this help message and exit
  --dsn DSN             Postgres DSN; defaults to POSTGRES_* env vars
  --wait-seconds WAIT_SECONDS
  --api-key API_KEY     API key (auto-detects provider; triggers non-
                        interactive mode)
  --provider PROVIDER   LLM provider (auto-detected from --api-key if omitted)
  --model MODEL         LLM model (defaults per provider)
  --character CHARACTER
                        Character card name (e.g. 'hexis', 'jarvis'). Omit for
                        express defaults
  --name NAME           What the agent should call you (default: 'User')
  --no-docker           Skip Docker auto-start
  --no-pull             Skip Ollama embedding model pull
```

### `hexis chat` (chat module)

```text
usage: hexis chat [-h] [--dsn DSN]

Interactive streaming chat with your Hexis agent.

options:
  -h, --help  show this help message and exit
  --dsn DSN   Postgres DSN; defaults to POSTGRES_* env vars
```

### `hexis worker` (worker module)

```text
usage: hexis-worker [-h] [--mode {heartbeat,maintenance,both}]
                    [--instance INSTANCE]

Run Hexis background workers.

options:
  -h, --help            show this help message and exit
  --mode {heartbeat,maintenance,both}
                        Which worker to run.
  --instance INSTANCE, -i INSTANCE
                        Target a specific instance (overrides HEXIS_INSTANCE
                        env var).
```

### `hexis mcp` (MCP server module)

```text
usage: hexis-mcp [-h] [--dsn DSN]

MCP server exposing CognitiveMemory tools over stdio.

options:
  -h, --help  show this help message and exit
  --dsn DSN   Postgres DSN; defaults to POSTGRES_* env vars
```

### `hexis ingest` (ingestion module)

Top-level:

```text
usage: ingest.py [-h] {ingest,status,process} ...

Hexis Universal Ingestion Pipeline

positional arguments:
  {ingest,status,process}
    ingest              Ingest content into memory
    status              Show ingestion status
    process             Process archived content

options:
  -h, --help            show this help message and exit
```

Subcommands:

```text
usage: ingest.py ingest [-h]
                        [--file FILE | --input INPUT | --url URL | --stdin]
                        [--stdin-type {text,markdown,code,json,yaml,data}]
                        [--stdin-title STDIN_TITLE] [--title TITLE]
                        [--mode {auto,deep,standard,shallow,archive,fast,slow,hybrid}]
                        [--no-recursive] [--min-importance MIN_IMPORTANCE]
                        [--permanent] [--base-trust BASE_TRUST]
                        [--endpoint ENDPOINT] [--model MODEL]
                        [--api-key API_KEY] [--db-host DB_HOST]
                        [--db-port DB_PORT] [--db-name DB_NAME]
                        [--db-user DB_USER] [--db-password DB_PASSWORD]
                        [--quiet]

options:
  -h, --help            show this help message and exit
  --file FILE, -f FILE  Single file to ingest
  --input INPUT, -i INPUT
                        Directory to ingest
  --url URL, -u URL     URL to fetch and ingest
  --stdin               Read content from stdin
  --stdin-type {text,markdown,code,json,yaml,data}
                        Content type for stdin input
  --stdin-title STDIN_TITLE
                        Title for stdin content
  --title TITLE         Override document title
  --mode {auto,deep,standard,shallow,archive,fast,slow,hybrid}
                        Ingestion mode
  --no-recursive        Don't recurse into subdirectories
  --min-importance MIN_IMPORTANCE
                        Minimum importance floor
  --permanent           Mark memories as permanent (no decay)
  --base-trust BASE_TRUST
                        Base trust level for source
  --endpoint ENDPOINT, -e ENDPOINT
                        LLM endpoint
  --model MODEL, -m MODEL
                        LLM model name
  --api-key API_KEY     LLM API key
  --db-host DB_HOST     Database host
  --db-port DB_PORT     Database port
  --db-name DB_NAME     Database name
  --db-user DB_USER     Database user
  --db-password DB_PASSWORD
                        Database password
  --quiet, -q           Suppress verbose output
```

```text
usage: ingest.py status [-h] [--pending] [--json] [--endpoint ENDPOINT]
                        [--model MODEL] [--api-key API_KEY]
                        [--db-host DB_HOST] [--db-port DB_PORT]
                        [--db-name DB_NAME] [--db-user DB_USER]
                        [--db-password DB_PASSWORD] [--quiet]

options:
  -h, --help            show this help message and exit
  --pending             Show pending/archived ingestions
  --json                Output as JSON
  --endpoint ENDPOINT, -e ENDPOINT
                        LLM endpoint
  --model MODEL, -m MODEL
                        LLM model name
  --api-key API_KEY     LLM API key
  --db-host DB_HOST     Database host
  --db-port DB_PORT     Database port
  --db-name DB_NAME     Database name
  --db-user DB_USER     Database user
  --db-password DB_PASSWORD
                        Database password
  --quiet, -q           Suppress verbose output
```

```text
usage: ingest.py process [-h] [--content-hash CONTENT_HASH] [--all-archived]
                         [--limit LIMIT] [--endpoint ENDPOINT] [--model MODEL]
                         [--api-key API_KEY] [--db-host DB_HOST]
                         [--db-port DB_PORT] [--db-name DB_NAME]
                         [--db-user DB_USER] [--db-password DB_PASSWORD]
                         [--quiet]

options:
  -h, --help            show this help message and exit
  --content-hash CONTENT_HASH
                        Content hash of specific archived item
  --all-archived        Process all archived items
  --limit LIMIT         Max items to process
  --endpoint ENDPOINT, -e ENDPOINT
                        LLM endpoint
  --model MODEL, -m MODEL
                        LLM model name
  --api-key API_KEY     LLM API key
  --db-host DB_HOST     Database host
  --db-port DB_PORT     Database port
  --db-name DB_NAME     Database name
  --db-user DB_USER     Database user
  --db-password DB_PASSWORD
                        Database password
  --quiet, -q           Suppress verbose output
```
