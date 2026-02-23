from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from core.agent_api import _connect_with_retry, db_dsn_from_env
from core.cognitive_memory_api import CognitiveMemory, MemoryType

logger = logging.getLogger(__name__)


def embedding_service_diagnosis(url: str | None) -> tuple[str, list[str]]:
    """Identify the embedding backend from its URL and return (name, fix_steps)."""
    url = (url or "").lower()
    if ":11434" in url:
        return "Ollama", [
            "Install Ollama — https://ollama.com/download",
            "Start it:       ollama serve",
            "Pull the model:  ollama pull embeddinggemma:300m-qat-q4_0",
        ]
    if "embeddings:" in url or "text-embeddings" in url:
        return "TEI (Text Embeddings Inference)", [
            "Uncomment the 'embeddings' service in docker-compose.yml",
            "Then run: docker compose up -d",
        ]
    if "api.openai.com" in url:
        return "OpenAI API", [
            "Check that OPENAI_API_KEY is set in your .env",
            "Verify your API key is valid and has embeddings access",
        ]
    if "localhost" in url or "127.0.0.1" in url or "host.docker.internal" in url:
        return "local embedding service", [
            f"Ensure the service at {url} is running",
            "Or set EMBEDDING_SERVICE_URL in .env to a different endpoint",
        ]
    return "embedding service", [
        f"Ensure the service at {url} is reachable from the DB container",
        "Or set EMBEDDING_SERVICE_URL in .env to a different endpoint",
    ]


def _coerce_json_value(val: Any) -> Any:
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return val
        try:
            return json.loads(s)
        except Exception:
            return val
    return val


async def status_payload(
    dsn: str | None = None,
    *,
    wait_seconds: int = 30,
    include_embedding_health: bool = True,
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        payload: dict[str, Any] = {"dsn": dsn}
        payload["db_time"] = str(await conn.fetchval("SELECT now()"))

        payload["agent_configured"] = bool(await conn.fetchval("SELECT is_agent_configured()"))
        payload["heartbeat_paused"] = bool(await conn.fetchval("SELECT is_paused FROM heartbeat_state WHERE id = 1"))
        payload["should_run_heartbeat"] = bool(await conn.fetchval("SELECT should_run_heartbeat()"))
        try:
            payload["maintenance_paused"] = bool(await conn.fetchval("SELECT is_paused FROM maintenance_state WHERE id = 1"))
            payload["should_run_maintenance"] = bool(await conn.fetchval("SELECT should_run_maintenance()"))
        except Exception:
            payload["maintenance_paused"] = None
            payload["should_run_maintenance"] = None

        payload["pending_external_calls"] = 0
        payload["pending_outbox_messages"] = 0

        payload["embedding_service_url"] = await conn.fetchval("SELECT get_config_text('embedding.service_url')")
        payload["embedding_dimension"] = int(await conn.fetchval("SELECT embedding_dimension()"))

        if include_embedding_health:
            try:
                payload["embedding_service_healthy"] = bool(
                    await conn.fetchval("SELECT check_embedding_service_health()")
                )
            except Exception as exc:
                payload["embedding_service_healthy"] = False
                payload["embedding_service_error"] = repr(exc)

        return payload
    finally:
        await conn.close()


async def config_rows(dsn: str | None = None, *, wait_seconds: int = 30) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        rows = await conn.fetch("SELECT key, value FROM config ORDER BY key")
        out: dict[str, Any] = {}
        for r in rows:
            out[str(r["key"])] = _coerce_json_value(r["value"])
        return out
    finally:
        await conn.close()


async def config_validate(dsn: str | None = None, *, wait_seconds: int = 30) -> tuple[list[str], list[str]]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        errors: list[str] = []
        warnings: list[str] = []

        rows = await conn.fetch("SELECT key, value FROM config ORDER BY key")
        cfg: dict[str, Any] = {str(r["key"]): _coerce_json_value(r["value"]) for r in rows}
        required_keys = [
            "agent.is_configured",
            "agent.objectives",
            "llm.heartbeat",
            "llm.chat",
        ]
        for key in required_keys:
            if key not in cfg:
                errors.append(f"Missing config key: {key}")

        is_conf = cfg.get("agent.is_configured")
        if is_conf is not True:
            if is_conf == "true":
                is_conf = True
        if is_conf is not True:
            errors.append("agent.is_configured is not true (run `hexis init`).")

        objectives = cfg.get("agent.objectives")
        if not isinstance(objectives, list) or not objectives:
            errors.append("agent.objectives must be a non-empty array (run `hexis init`).")

        def _validate_llm(name: str) -> None:
            val = cfg.get(name)
            if not isinstance(val, dict):
                errors.append(f"{name} must be an object (run `hexis init`).")
                return
            provider = str(val.get("provider") or "").strip().lower()
            model = str(val.get("model") or "").strip()
            endpoint = str(val.get("endpoint") or "").strip()
            api_key_env = str(val.get("api_key_env") or "").strip()

            if not provider:
                errors.append(f"{name}.provider is required")
            if not model and provider not in {"ollama"}:
                warnings.append(f"{name}.model is empty (will rely on worker defaults)")

            # OAuth providers with stored credentials
            _oauth_providers: dict[str, str] = {
                "chutes": "oauth.chutes",
                "qwen-portal": "oauth.qwen_portal",
                "minimax-portal": "oauth.minimax_portal",
            }
            oauth_key = _oauth_providers.get(provider)
            if oauth_key:
                oauth = cfg.get(oauth_key)
                if not isinstance(oauth, dict) or not oauth.get("access"):
                    errors.append(
                        f"{provider} is not configured (missing {oauth_key}). "
                        f"Run: `hexis auth {provider} login`"
                    )
                return

            if provider in {"openai", "anthropic", "openai_compatible", "grok", "gemini"}:
                if api_key_env:
                    if os.getenv(api_key_env) is None:
                        errors.append(f"{name}.api_key_env={api_key_env} is not set in environment")
                else:
                    if not endpoint or ("localhost" not in endpoint and "127.0.0.1" not in endpoint):
                        warnings.append(f"{name}.api_key_env not set (LLM calls may fail)")

        _validate_llm("llm.heartbeat")
        _validate_llm("llm.chat")
        if "llm.subconscious" in cfg:
            _validate_llm("llm.subconscious")

        interval = await conn.fetchval("SELECT get_config_float('heartbeat.heartbeat_interval_minutes')")
        if interval is None or float(interval) <= 0:
            errors.append("heartbeat.heartbeat_interval_minutes must be > 0")

        return errors, warnings
    finally:
        await conn.close()


async def demo(dsn: str | None = None, *, wait_seconds: int = 30) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        deadline = time.monotonic() + wait_seconds
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ok = await conn.fetchval("SELECT check_embedding_service_health()")
                if ok is True:
                    break
            except Exception as exc:  # pragma: no cover (timing-dependent)
                last = exc
            await asyncio.sleep(1)
        else:
            raise TimeoutError(f"Embedding service not healthy after {wait_seconds}s: {last!r}")
    finally:
        await conn.close()

    async with CognitiveMemory.connect(dsn) as mem:
        m1 = await mem.remember("Demo: the user prefers short, direct answers", type=MemoryType.SEMANTIC, importance=0.7)
        m2 = await mem.remember(
            "Demo: the user is working on the Hexis memory system",
            type=MemoryType.EPISODIC,
            importance=0.6,
        )
        held = await mem.hold("Demo: temporary context in working memory", ttl_seconds=600)

        recall = await mem.recall("What do I know about the user's preferences?", limit=5)
        hydrate = await mem.hydrate("Summarize what we know about the user", include_goals=False)
        working_hits = await mem.search_working("temporary context", limit=5)

        return {
            "remembered_ids": [str(m1), str(m2)],
            "working_memory_id": str(held),
            "recall_count": len(recall.memories),
            "hydrate_memory_count": len(hydrate.memories),
            "working_search_count": len(working_hits),
        }


# ---------------------------------------------------------------------------
# doctor -- comprehensive health check
# ---------------------------------------------------------------------------

async def _check(
    label: str,
    coro,
    *,
    warn_on_false: str | None = None,
) -> dict[str, Any]:
    """Run a single health check and return a result dict."""
    try:
        result = await coro
        if warn_on_false is not None and result is False:
            return {"label": label, "status": "WARN", "detail": warn_on_false}
        return {"label": label, "status": "OK", "detail": result}
    except Exception as exc:
        return {"label": label, "status": "FAIL", "detail": str(exc)}


async def doctor_payload(
    dsn: str | None = None,
    *,
    wait_seconds: int = 10,
) -> list[dict[str, Any]]:
    """
    Run comprehensive health checks and return a list of check results.

    Each result: {"label": str, "status": "OK"|"WARN"|"FAIL", "detail": Any}
    """
    dsn = dsn or db_dsn_from_env()
    checks: list[dict[str, Any]] = []

    # 1. PostgreSQL connectivity
    try:
        conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    except Exception as exc:
        checks.append({"label": "PostgreSQL", "status": "FAIL", "detail": str(exc)})
        return checks  # Can't do anything else without DB

    try:
        db_time = await conn.fetchval("SELECT now()")
        checks.append({"label": "PostgreSQL", "status": "OK", "detail": f"connected ({db_time})"})

        # 2. Embeddings service
        try:
            emb_url = await conn.fetchval(
                "SELECT current_setting('app.embedding_service_url', true)"
            )
            emb_model = await conn.fetchval(
                "SELECT current_setting('app.embedding_model_id', true)"
            )
            healthy = await conn.fetchval("SELECT check_embedding_service_health()")
            if healthy:
                checks.append({
                    "label": "Embeddings",
                    "status": "OK",
                    "detail": f"healthy — {emb_model} via {emb_url}",
                })
            else:
                svc_name, steps = embedding_service_diagnosis(emb_url)
                fix = "; ".join(steps)
                checks.append({
                    "label": "Embeddings",
                    "status": "FAIL",
                    "detail": (
                        f"Your config points to {svc_name} ({emb_url}) "
                        f"but it is not responding. {fix}"
                    ),
                })
        except Exception as exc:
            checks.append({
                "label": "Embeddings",
                "status": "FAIL",
                "detail": f"{exc}",
            })

        # 3. RabbitMQ (check config, not connectivity -- avoids dependency)
        try:
            rmq_url = await conn.fetchval(
                "SELECT value FROM config WHERE key = 'rabbitmq.url'"
            )
            if rmq_url:
                checks.append({"label": "RabbitMQ", "status": "OK", "detail": "configured"})
            else:
                checks.append({"label": "RabbitMQ", "status": "WARN", "detail": "not configured (outbox delivery disabled)"})
        except Exception:
            checks.append({"label": "RabbitMQ", "status": "WARN", "detail": "not configured"})

        # 4. Agent configured
        try:
            configured = bool(await conn.fetchval("SELECT is_agent_configured()"))
            if configured:
                profile = await conn.fetchval("SELECT get_agent_profile_context()")
                name = None
                if profile:
                    p = json.loads(profile) if isinstance(profile, str) else profile
                    name = p.get("name") or p.get("identity", {}).get("name")
                if not name:
                    init_profile = await conn.fetchval(
                        "SELECT value FROM config WHERE key = 'agent.init_profile'"
                    )
                    if init_profile:
                        ip = json.loads(init_profile) if isinstance(init_profile, str) else init_profile
                        name = (ip.get("agent") or {}).get("name")
                name = name or "unnamed"
                checks.append({
                    "label": "Agent configured",
                    "status": "OK",
                    "detail": f'yes (identity: "{name}")',
                })
            else:
                checks.append({
                    "label": "Agent configured",
                    "status": "WARN",
                    "detail": "no (run 'hexis init')",
                })
        except Exception as exc:
            checks.append({"label": "Agent configured", "status": "FAIL", "detail": str(exc)})

        # 5. Consent
        try:
            from core.consent import ConsentManager
            manager = ConsentManager()
            consents = manager.list_consents()
            valid = [c for c in consents if c.is_valid() and not c.revoked]
            if valid:
                models = [f"{c.model.provider}/{c.model.model_id}" for c in valid]
                checks.append({
                    "label": "Consent",
                    "status": "OK",
                    "detail": f"valid ({', '.join(models)})",
                })
            else:
                checks.append({
                    "label": "Consent",
                    "status": "WARN",
                    "detail": "no valid consents (run 'hexis consents request <model>')",
                })
        except Exception:
            checks.append({"label": "Consent", "status": "WARN", "detail": "consent system unavailable"})

        # 6. Heartbeat status
        try:
            hb_row = await conn.fetchrow(
                "SELECT current_energy, last_heartbeat_at, is_paused FROM heartbeat_state WHERE id = 1"
            )
            if hb_row:
                energy = hb_row["current_energy"]
                last_hb = hb_row["last_heartbeat_at"]
                paused = hb_row["is_paused"]
                max_energy = await conn.fetchval(
                    "SELECT get_config_int('heartbeat.max_energy')"
                ) or 20
                if paused:
                    checks.append({
                        "label": "Heartbeat",
                        "status": "WARN",
                        "detail": f"paused (energy: {energy}/{max_energy})",
                    })
                elif last_hb:
                    from datetime import timezone as tz
                    now = datetime.now(tz.utc)
                    if hasattr(last_hb, 'tzinfo') and last_hb.tzinfo is None:
                        last_hb = last_hb.replace(tzinfo=tz.utc)
                    ago = now - last_hb
                    ago_str = _format_timedelta(ago)
                    checks.append({
                        "label": "Heartbeat",
                        "status": "OK",
                        "detail": f"running (last: {ago_str} ago, energy: {energy}/{max_energy})",
                    })
                else:
                    checks.append({
                        "label": "Heartbeat",
                        "status": "WARN",
                        "detail": f"never run (energy: {energy}/{max_energy})",
                    })
            else:
                checks.append({"label": "Heartbeat", "status": "WARN", "detail": "state not initialized"})
        except Exception as exc:
            checks.append({"label": "Heartbeat", "status": "FAIL", "detail": str(exc)})

        # 7. Channels
        try:
            ch_rows = await conn.fetch("""
                SELECT channel_type, COUNT(*) AS sessions
                FROM channel_sessions
                GROUP BY channel_type
                ORDER BY channel_type
            """)
            if ch_rows:
                ch_list = [f"{r['channel_type']}" for r in ch_rows]
                checks.append({
                    "label": "Channels",
                    "status": "OK",
                    "detail": f"{len(ch_rows)} active ({', '.join(ch_list)})",
                })
            else:
                checks.append({
                    "label": "Channels",
                    "status": "WARN",
                    "detail": "0 sessions (no channel activity yet)",
                })
        except Exception:
            checks.append({"label": "Channels", "status": "WARN", "detail": "channel tables not available"})

        # 8. Tools
        try:
            import asyncpg
            from core.agent_api import pool_sizes_from_env
            _min, _max = pool_sizes_from_env(1, 2)
            pool = await asyncpg.create_pool(dsn, min_size=_min, max_size=_max)
            try:
                from core.tools import create_default_registry
                from core.tools.config import load_tools_config
                registry = create_default_registry(pool)
                config = await load_tools_config(pool)
                all_handlers = registry.list_all()
                approval_count = sum(1 for h in all_handlers if h.spec.requires_approval)
                checks.append({
                    "label": "Tools",
                    "status": "OK",
                    "detail": f"{len(all_handlers)} registered, {approval_count} requiring approval",
                })
            finally:
                await pool.close()
        except Exception as exc:
            checks.append({"label": "Tools", "status": "WARN", "detail": str(exc)})

        # 9. Skills
        try:
            from skills.loader import load_skills_from_dir, discover_skill_dirs
            total = 0
            names = []
            for d in discover_skill_dirs():
                for spec in load_skills_from_dir(d):
                    total += 1
                    names.append(spec.name)
            if total > 0:
                checks.append({
                    "label": "Skills",
                    "status": "OK",
                    "detail": f"{total} loaded ({', '.join(names)})",
                })
            else:
                checks.append({"label": "Skills", "status": "WARN", "detail": "0 installed"})
        except Exception as exc:
            checks.append({"label": "Skills", "status": "WARN", "detail": str(exc)})

        # 10. Schema (count applied SQL files by checking key tables/functions)
        try:
            table_count = await conn.fetchval("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """)
            func_count = await conn.fetchval("""
                SELECT COUNT(DISTINCT routine_name) FROM information_schema.routines
                WHERE routine_schema = 'public'
            """)
            checks.append({
                "label": "Schema",
                "status": "OK",
                "detail": f"{table_count} tables, {func_count} functions",
            })
        except Exception as exc:
            checks.append({"label": "Schema", "status": "FAIL", "detail": str(exc)})

        # 11. Memory stats
        try:
            mem_stats = await conn.fetch("""
                SELECT type, COUNT(*) AS cnt
                FROM memories
                WHERE status = 'active'
                GROUP BY type
                ORDER BY type
            """)
            total = sum(r["cnt"] for r in mem_stats)
            if total == 0:
                checks.append({
                    "label": "Memory",
                    "status": "WARN",
                    "detail": "0 memories (run 'hexis init' or 'hexis chat')",
                })
            else:
                parts = [f"{r['cnt']} {r['type']}" for r in mem_stats]
                checks.append({
                    "label": "Memory",
                    "status": "OK",
                    "detail": ", ".join(parts),
                })
        except Exception as exc:
            checks.append({"label": "Memory", "status": "FAIL", "detail": str(exc)})

    finally:
        await conn.close()

    return checks


def _format_timedelta(td) -> str:
    """Format a timedelta to a human-readable string like '3d 14h' or '2m'."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "just now"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return f"{total_seconds}s"


# ---------------------------------------------------------------------------
# enhanced status -- rich agent overview
# ---------------------------------------------------------------------------

async def status_payload_rich(
    dsn: str | None = None,
    *,
    wait_seconds: int = 30,
) -> dict[str, Any]:
    """
    Return a rich status payload with identity, energy, memory counts,
    channels, goals, and mood.
    """
    dsn = dsn or db_dsn_from_env()
    conn = await _connect_with_retry(dsn, wait_seconds=wait_seconds)
    try:
        payload: dict[str, Any] = {}

        # Instance info
        try:
            from core.instance import InstanceRegistry
            reg = InstanceRegistry()
            current = reg.get_current()
            inst = reg.get(current) if current else None
            payload["instance"] = current or "default"
            payload["database"] = inst.database if inst else "hexis_memory"
        except Exception:
            payload["instance"] = "default"
            payload["database"] = "hexis_memory"

        # Identity (agent name)
        try:
            init_profile = await conn.fetchval(
                "SELECT value FROM config WHERE key = 'agent.init_profile'"
            )
            name = None
            if init_profile:
                ip = json.loads(init_profile) if isinstance(init_profile, str) else init_profile
                name = (ip.get("agent") or {}).get("name") or ip.get("name")
            payload["identity"] = name or "unnamed"
        except Exception:
            payload["identity"] = None

        # Energy and heartbeat
        try:
            hb_row = await conn.fetchrow(
                "SELECT current_energy, last_heartbeat_at, is_paused, heartbeat_count FROM heartbeat_state WHERE id = 1"
            )
            max_energy = await conn.fetchval("SELECT get_config_int('heartbeat.max_energy')") or 20
            interval_min = await conn.fetchval("SELECT get_config_float('heartbeat.heartbeat_interval_minutes')") or 60

            if hb_row:
                payload["energy"] = hb_row["current_energy"]
                payload["max_energy"] = max_energy
                payload["heartbeat_paused"] = hb_row["is_paused"]
                payload["heartbeat_count"] = hb_row["heartbeat_count"]
                payload["heartbeat_interval_minutes"] = float(interval_min)

                last_hb = hb_row["last_heartbeat_at"]
                if last_hb:
                    from datetime import timezone as tz
                    now = datetime.now(tz.utc)
                    if hasattr(last_hb, 'tzinfo') and last_hb.tzinfo is None:
                        last_hb = last_hb.replace(tzinfo=tz.utc)
                    payload["last_heartbeat_ago"] = _format_timedelta(now - last_hb)
                else:
                    payload["last_heartbeat_ago"] = None

                # Estimate next regen time based on interval
                if last_hb and hb_row["current_energy"] < max_energy:
                    next_hb_seconds = float(interval_min) * 60
                    from datetime import timezone as tz
                    now = datetime.now(tz.utc)
                    if hasattr(last_hb, 'tzinfo') and last_hb.tzinfo is None:
                        last_hb = last_hb.replace(tzinfo=tz.utc)
                    elapsed = (now - last_hb).total_seconds()
                    remaining = max(0, next_hb_seconds - elapsed)
                    payload["next_regen_minutes"] = round(remaining / 60, 1)
                else:
                    payload["next_regen_minutes"] = None
        except Exception:
            payload["energy"] = None

        # Heartbeat active status
        try:
            payload["heartbeat_active"] = bool(
                await conn.fetchval("SELECT should_run_heartbeat()")
            )
        except Exception:
            payload["heartbeat_active"] = None

        # Memory counts by type
        try:
            mem_rows = await conn.fetch("""
                SELECT type, COUNT(*) AS cnt
                FROM memories WHERE status = 'active'
                GROUP BY type ORDER BY type
            """)
            payload["memories"] = {r["type"]: r["cnt"] for r in mem_rows}
        except Exception:
            payload["memories"] = {}

        # Active channels
        try:
            ch_rows = await conn.fetch("""
                SELECT channel_type, COUNT(*) AS sessions,
                       COUNT(*) FILTER (WHERE last_active > CURRENT_TIMESTAMP - INTERVAL '1 hour') AS active_1h
                FROM channel_sessions
                GROUP BY channel_type ORDER BY channel_type
            """)
            payload["channels"] = [
                {"type": r["channel_type"], "sessions": r["sessions"], "active_1h": r["active_1h"]}
                for r in ch_rows
            ]
        except Exception:
            payload["channels"] = []

        # Active goals
        try:
            goal_rows = await conn.fetch("""
                SELECT content, metadata->>'priority' AS priority
                FROM memories
                WHERE type = 'goal' AND status = 'active'
                ORDER BY importance DESC
                LIMIT 5
            """)
            payload["goals"] = [
                {"content": r["content"][:100], "priority": r["priority"]}
                for r in goal_rows
            ]
        except Exception:
            payload["goals"] = []

        # Mood / emotional state
        try:
            emo_raw = await conn.fetchval("SELECT value FROM state WHERE key = 'heartbeat_state'")
            if emo_raw:
                emo = json.loads(emo_raw) if isinstance(emo_raw, str) else emo_raw
                aff = emo.get("affective_state", {})
                if aff:
                    valence = aff.get("valence", 0.0)
                    arousal = aff.get("arousal", 0.0)
                    # Map valence to a simple mood word
                    if valence > 0.5:
                        mood = "enthusiastic" if arousal > 0.5 else "content"
                    elif valence > 0.2:
                        mood = "curious" if arousal > 0.3 else "calm"
                    elif valence > -0.2:
                        mood = "focused" if arousal > 0.3 else "neutral"
                    elif valence > -0.5:
                        mood = "concerned" if arousal > 0.3 else "subdued"
                    else:
                        mood = "distressed" if arousal > 0.5 else "withdrawn"
                    payload["mood"] = mood
                    payload["valence"] = round(valence, 2)
                else:
                    payload["mood"] = "neutral"
                    payload["valence"] = 0.0
            else:
                payload["mood"] = None
        except Exception:
            payload["mood"] = None

        # Scheduled tasks
        try:
            task_count = await conn.fetchval(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE status = 'active'"
            )
            payload["scheduled_tasks"] = task_count or 0
        except Exception:
            payload["scheduled_tasks"] = 0

        return payload
    finally:
        await conn.close()
