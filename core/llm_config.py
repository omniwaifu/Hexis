from __future__ import annotations

import json
import os
from typing import Any

from core.llm import normalize_llm_config


DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")


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
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider in {"openai_codex"}:
        provider = "openai-codex"
        cfg["provider"] = provider

    if "model" not in cfg:
        # Codex models are distinct from Platform/OpenAI API models; pick a sane default.
        cfg["model"] = "gpt-5.2-codex" if provider == "openai-codex" else default_model

    # If the provider is OpenAI Codex (ChatGPT subscription), pull OAuth credentials
    # from the shared token sink and refresh if needed.
    if provider == "openai-codex":
        from core.openai_codex_oauth import ensure_fresh_openai_codex_credentials

        creds = await ensure_fresh_openai_codex_credentials(conn)
        cfg["api_key"] = creds.access
        # Base URL for Codex backend (not OpenAI Platform /v1).
        cfg.setdefault("endpoint", "https://chatgpt.com/backend-api")

    return normalize_llm_config(cfg, default_model=default_model)
