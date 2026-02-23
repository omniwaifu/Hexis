from __future__ import annotations

import json
import os
from typing import Any, Callable, Coroutine

from core.llm import normalize_llm_config, normalize_provider


DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")


# ---------------------------------------------------------------------------
# Per-provider config loaders (inject api_key / endpoint / auth_mode)
# ---------------------------------------------------------------------------

async def _load_chutes(conn, cfg: dict[str, Any]) -> None:
    from core.auth.chutes import CHUTES_DEFAULT_ENDPOINT, ensure_fresh_credentials

    creds = await ensure_fresh_credentials()
    cfg["api_key"] = creds.access
    cfg.setdefault("endpoint", CHUTES_DEFAULT_ENDPOINT)


async def _load_qwen_portal(conn, cfg: dict[str, Any]) -> None:
    from core.auth.qwen_portal import QWEN_PORTAL_DEFAULT_ENDPOINT, ensure_fresh_credentials

    creds = await ensure_fresh_credentials()
    cfg["api_key"] = creds.access
    cfg.setdefault("endpoint", creds.resource_url or QWEN_PORTAL_DEFAULT_ENDPOINT)


async def _load_minimax_portal(conn, cfg: dict[str, Any]) -> None:
    from core.auth.minimax_portal import default_endpoint, ensure_fresh_credentials

    creds = await ensure_fresh_credentials()
    cfg["api_key"] = creds.access
    cfg.setdefault("endpoint", creds.resource_url or default_endpoint(creds.region))


_PROVIDER_CONFIG_LOADERS: dict[
    str,
    Callable[..., Coroutine[Any, Any, None]],
] = {
    "chutes": _load_chutes,
    "qwen-portal": _load_qwen_portal,
    "minimax-portal": _load_minimax_portal,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_llm_config(
    conn,
    key: str,
    *,
    default_provider: str = DEFAULT_LLM_PROVIDER,
    default_model: str = DEFAULT_LLM_MODEL,
    fallback_key: str | None = None,
) -> dict[str, Any]:
    cfg = await conn.fetchval("SELECT get_config($1)", key)
    if cfg is None and fallback_key:
        cfg = await conn.fetchval("SELECT get_config($1)", fallback_key)

    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = None

    if not isinstance(cfg, dict):
        cfg = {}

    if "provider" not in cfg:
        cfg["provider"] = default_provider
    provider = normalize_provider(str(cfg.get("provider") or ""))
    cfg["provider"] = provider

    if "model" not in cfg:
        cfg["model"] = default_model

    # Run the provider-specific config loader (inject api_key, endpoint, auth_mode).
    loader = _PROVIDER_CONFIG_LOADERS.get(provider)
    if loader:
        await loader(conn, cfg)

    return normalize_llm_config(cfg, default_model=default_model)


async def resolve_llm_config(
    pool_or_conn,
    key: str = "llm.chat",
    *,
    fallback_key: str | None = "llm",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper around :func:`load_llm_config`.

    Accepts either an ``asyncpg.Pool`` or an ``asyncpg.Connection``.  When a
    pool is provided, a connection is acquired automatically.  This makes it
    usable from both tool handlers (which have pools) and CLI paths (which
    already hold a connection).

    Optional *overrides* are merged **after** credential resolution so that
    callers can patch fields (e.g. model) without interfering with the auth
    flow.
    """

    async def _load(conn) -> dict[str, Any]:
        cfg = await load_llm_config(conn, key, fallback_key=fallback_key)
        if overrides:
            cfg.update(overrides)
        return cfg

    # Duck-type: pools have .acquire(), connections don't.
    if hasattr(pool_or_conn, "acquire"):
        async with pool_or_conn.acquire() as conn:
            return await _load(conn)
    return await _load(pool_or_conn)
