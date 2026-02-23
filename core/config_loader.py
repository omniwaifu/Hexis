"""Load hexis.toml and populate os.environ. Drop-in for load_dotenv()."""
import os
import tomllib
from pathlib import Path

# TOML key path → env var name
_TOML_TO_ENV: dict[str, str] = {
    "postgres.host":            "POSTGRES_HOST",
    "postgres.port":            "POSTGRES_PORT",
    "postgres.db":              "POSTGRES_DB",
    "postgres.user":            "POSTGRES_USER",
    "postgres.password":        "POSTGRES_PASSWORD",
    "hexis.bind_address":       "HEXIS_BIND_ADDRESS",
    "llm.conscious_api_key":    "HEXIS_LLM_CONSCIOUS_API_KEY",
    "llm.subconscious_api_key": "HEXIS_LLM_SUBCONSCIOUS_API_KEY",
    "embedding.service_url":    "EMBEDDING_SERVICE_URL",
    "embedding.cache_dir":      "EMBEDDINGS_CACHE_DIR",
    "telegram.bot_token":       "TELEGRAM_BOT_TOKEN",
    "discord.bot_token":        "DISCORD_BOT_TOKEN",
    "slack.bot_token":          "SLACK_BOT_TOKEN",
    "slack.app_token":          "SLACK_APP_TOKEN",
}

_ENV_TO_TOML: dict[str, str] = {v: k for k, v in _TOML_TO_ENV.items()}

_SEARCH_PATHS = [
    Path.cwd() / "hexis.toml",
    Path.home() / ".config" / "hexis" / "config.toml",
]


def load_config(path: Path | None = None) -> None:
    """Read hexis.toml and set env vars. Skips keys already in os.environ."""
    candidates = [path] if path else _SEARCH_PATHS
    for p in candidates:
        if p and p.exists():
            with open(p, "rb") as f:
                data = tomllib.load(f)
            for toml_key, env_key in _TOML_TO_ENV.items():
                section, _, field = toml_key.partition(".")
                val = data.get(section, {}).get(field)
                if val is not None and env_key not in os.environ:
                    os.environ[env_key] = str(val)
            return  # first match wins


def update_config_value(toml_key: str, value: str, path: Path | None = None) -> None:
    """Write/update a single key in hexis.toml. Used by hexis init."""
    target = path or Path.cwd() / "hexis.toml"
    section, _, field = toml_key.partition(".")
    if target.exists():
        with open(target, "rb") as f:
            try:
                data: dict = tomllib.load(f)
            except Exception:
                data = {}
    else:
        data = {}
    data.setdefault(section, {})[field] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_to_toml(data))


def _to_toml(data: dict) -> str:
    lines = ["# hexis.toml — local config (do not commit)\n"]
    for section, fields in data.items():
        lines.append(f"\n[{section}]")
        for k, v in fields.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            else:
                lines.append(f"{k} = {v}")
    return "\n".join(lines) + "\n"
