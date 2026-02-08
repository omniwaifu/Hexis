# OpenAI Codex OAuth (ChatGPT Subscription)

Hexis supports **ChatGPT subscription auth** (OpenAI Codex) via OAuth + PKCE.

This is **not** OpenAI Platform API-key auth.

## Login

1. Start your stack so Postgres is reachable.
2. Run:

```bash
hexis auth openai-codex login
```

This opens a browser to `auth.openai.com`, captures the callback on
`http://localhost:1455/auth/callback`, and stores `{access, refresh, expires_ms, account_id}`
in the Postgres `config` table under `oauth.openai_codex`.

If the callback can't bind (or you are remote/headless), copy the browser's redirect URL and
paste it back into the CLI prompt.

## Configure The LLM Provider

Set your LLM provider config (via the init UI or by writing config) to:

- `llm.chat.provider = "openai-codex"`
- `llm.chat.model = "gpt-5.2"` (recommended; matches the README quick start)
- (optional) `llm.chat.model = "gpt-5.2-codex"` (or another Codex model id)

No API key is required in the UI for `openai-codex`; the Python server pulls the OAuth token
from the DB and refreshes it automatically when needed.

## Status / Logout

```bash
hexis auth openai-codex status
hexis auth openai-codex logout
```
