"""
Hexis API Server

FastAPI app that wraps the canonical AgentLoop for chat, exposing SSE
streaming in the same event format the Next.js frontend already consumes.

Endpoints:
    POST /api/chat  — SSE streaming chat via AgentLoop.stream()
    GET  /api/status — Rich agent status
    GET  /health     — Simple health check
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from core.agent_api import db_dsn_from_env, get_agent_profile_context, pool_sizes_from_env
from core.agent_loop import AgentEvent
from core.cli_api import status_payload_rich
from core.cognitive_memory_api import CognitiveMemory
from core.gateway import EventSource, Gateway
from core.tools import create_default_registry
from services.agent import stream_agent
from services.chat import _remember_conversation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App state (set during lifespan)
# ---------------------------------------------------------------------------

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    return db_dsn_from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    dsn = _dsn()
    _min, _max = pool_sizes_from_env(2, 10)
    _pool = await asyncpg.create_pool(dsn, min_size=_min, max_size=_max)
    from core.usage import set_usage_pool
    set_usage_pool(_pool)
    logger.info("Hexis API started (pool created)")
    yield
    if _pool:
        await _pool.close()
        logger.info("Pool closed")


app = FastAPI(title="Hexis API", lifespan=lifespan)

# CORS — allow Next.js dev server and configurable origins
_cors_origins = os.getenv("HEXIS_CORS_ORIGINS", "http://localhost:3477,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional Bearer token authentication
_API_KEY = (os.getenv("HEXIS_API_KEY") or "").strip() or None


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _API_KEY and request.url.path != "/health":
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != _API_KEY:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


if _API_KEY:
    app.add_middleware(_BearerAuthMiddleware)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] | None = None
    prompt_addenda: list[str] | None = None


class ConsentLlmConfig(BaseModel):
    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None
    api_key: str | None = None


class InitConsentRequest(BaseModel):
    role: Literal["conscious", "subconscious"] = "conscious"
    llm: ConsentLlmConfig | None = None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    checks = {"db": False}
    try:
        if _pool:
            async with _pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["db"] = True
    except Exception:
        pass
    ok = all(checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status_code=200 if ok else 503,
    )


@app.get("/api/status")
async def status():
    try:
        payload = await status_payload_rich()
        return JSONResponse(payload)
    except Exception as e:
        logger.error("Status failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/webhook/{source}")
async def webhook(source: str, request: Request):
    """Accept an external webhook payload and submit to the gateway for async processing."""
    pool = _pool
    if pool is None:
        return JSONResponse({"error": "Server not ready"}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    try:
        gateway = Gateway(pool)
        event_id = await gateway.submit(
            EventSource.WEBHOOK,
            f"webhook:{source}",
            payload,
        )
        return JSONResponse({"status": "accepted", "event_id": event_id}, status_code=202)
    except Exception as e:
        logger.error("Webhook submit failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/events/stream")
async def event_stream():
    """SSE stream of gateway events for real-time dashboard updates.

    Uses pg_notify on the 'gateway_events' channel. Each notification triggers
    a fetch of the event row and yields it as an SSE event.
    """
    pool = _pool
    if pool is None:
        return StreamingResponse(
            _sse_iter_error("Server not ready"),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _sse_event_stream(pool),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_iter_error(msg: str) -> AsyncIterator[str]:
    yield _sse_event("error", {"message": msg})


async def _sse_event_stream(pool: asyncpg.Pool) -> AsyncIterator[str]:
    """Generator that listens for pg_notify and yields SSE events."""
    import asyncio

    queue: asyncio.Queue[str] = asyncio.Queue()

    def _on_notify(conn, pid, channel, payload):
        queue.put_nowait(payload)

    conn = await pool.acquire()
    try:
        await conn.add_listener("gateway_events", _on_notify)
        yield _sse_event("connected", {"message": "Listening for gateway events"})

        while True:
            try:
                # Wait for notification with a 30s keepalive timeout
                event_id_str = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive comment
                yield ": keepalive\n\n"
                continue

            # Fetch the event from DB
            try:
                row = await pool.fetchrow(
                    "SELECT id, source, status, session_key, payload, result, error, "
                    "correlation_id, created_at, started_at, completed_at "
                    "FROM gateway_events WHERE id = $1",
                    int(event_id_str),
                )
                if row:
                    event_data = {
                        "id": row["id"],
                        "source": row["source"],
                        "status": row["status"],
                        "session_key": row["session_key"],
                        "correlation_id": str(row["correlation_id"]),
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    }
                    yield _sse_event("gateway_event", event_data)
            except Exception:
                logger.debug("Failed to fetch event %s", event_id_str, exc_info=True)

    except asyncio.CancelledError:
        pass
    finally:
        try:
            await conn.remove_listener("gateway_events", _on_notify)
        except Exception:
            pass
        await pool.release(conn)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        _stream_chat(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_chat(req: ChatRequest) -> AsyncIterator[str]:
    """
    Run the unified agent in streaming mode and yield SSE events that match
    the format the Next.js frontend already parses.

    Event mapping:
        AgentEvent.PHASE_CHANGE  → phase_start/phase_end  {phase}
        AgentEvent.LOOP_START    → phase_start  {phase: "conscious_final"}
        AgentEvent.TEXT_DELTA    → token        {phase: "conscious_final", text}
        AgentEvent.TOOL_START    → log          {id, kind: "tool_call", title, detail}
        AgentEvent.TOOL_RESULT   → log          {id, kind: "tool_result", title, detail}
        AgentEvent.LOOP_END      → done         {assistant: full_text}
        AgentEvent.ERROR         → error        {message}
    """
    pool = _pool
    if pool is None:
        yield _sse_event("error", {"message": "Server not ready (no DB pool)"})
        return

    dsn = _dsn()
    user_message = req.message
    history = req.history or []
    session_id = str(uuid.uuid4())

    # Record chat event for audit trail (record-and-dispatch mode)
    try:
        gateway = Gateway(pool)
        await gateway.record(
            EventSource.CHAT,
            f"chat:api:{session_id}",
            {"message": user_message[:500]},
        )
    except Exception:
        logger.debug("Gateway record failed (non-fatal)", exc_info=True)

    try:
        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(pool=pool)

        full_text = ""
        conscious_started = False

        async for event in stream_agent(
            pool,
            registry,
            user_message=user_message,
            mode="chat",
            history=history,
            session_id=session_id,
            agent_profile=agent_profile,
            dsn=dsn,
        ):
            if event.event == AgentEvent.PHASE_CHANGE:
                phase = event.data.get("phase", "")
                status = event.data.get("status", "")
                if phase == "memory_recall":
                    count = event.data.get("count", 0)
                    yield _sse_event("log", {
                        "id": str(uuid.uuid4()),
                        "kind": "memory_recall",
                        "title": "Memory Recall",
                        "detail": f"Retrieved {count} relevant memories",
                    })
                elif phase == "subconscious":
                    if status == "start":
                        yield _sse_event("phase_start", {"phase": "subconscious"})
                    elif status == "end":
                        yield _sse_event("phase_end", {"phase": "subconscious"})

            elif event.event == AgentEvent.LOOP_START:
                if not conscious_started:
                    yield _sse_event("phase_start", {"phase": "conscious_final"})
                    conscious_started = True

            elif event.event == AgentEvent.TEXT_DELTA:
                if not conscious_started:
                    yield _sse_event("phase_start", {"phase": "conscious_final"})
                    conscious_started = True
                text = event.data.get("text", "")
                if text:
                    full_text += text
                    yield _sse_event("token", {
                        "phase": "conscious_final",
                        "text": text,
                    })

            elif event.event == AgentEvent.TOOL_START:
                yield _sse_event("log", {
                    "id": str(uuid.uuid4()),
                    "kind": "tool_call",
                    "title": event.data.get("tool_name", "tool"),
                    "detail": json.dumps(event.data.get("arguments", {}))[:500],
                })

            elif event.event == AgentEvent.TOOL_RESULT:
                tool_name = event.data.get("tool_name", "tool")
                success = event.data.get("success", False)
                error = event.data.get("error")
                detail = f"{'OK' if success else 'FAILED'}"
                if error:
                    detail += f": {error}"
                yield _sse_event("log", {
                    "id": str(uuid.uuid4()),
                    "kind": "tool_result",
                    "title": tool_name,
                    "detail": detail,
                })

            elif event.event == AgentEvent.ERROR:
                yield _sse_event("error", {
                    "message": event.data.get("error", "Unknown error"),
                })

        # Signal phase end and completion
        if conscious_started:
            yield _sse_event("phase_end", {"phase": "conscious_final"})

        # Memory formation
        if full_text:
            try:
                mem_client = CognitiveMemory(pool)
                await _remember_conversation(
                    mem_client,
                    user_message=user_message,
                    assistant_message=full_text,
                )
                yield _sse_event("log", {
                    "id": str(uuid.uuid4()),
                    "kind": "memory_write",
                    "title": "Memory Formation",
                    "detail": "Conversation stored as episodic memory",
                })
            except Exception as e:
                logger.error("Memory formation failed: %s", e)

        yield _sse_event("done", {"assistant": full_text})

    except Exception as e:
        logger.exception("Chat stream error")
        yield _sse_event("error", {"message": str(e)})


def _resolve_fallback_api_key(provider: str, role: str) -> str | None:
    # Prefer role-specific keys set by the UI init wizard.
    role_env = "HEXIS_LLM_CONSCIOUS_API_KEY" if role == "conscious" else "HEXIS_LLM_SUBCONSCIOUS_API_KEY"
    value = (os.getenv(role_env) or "").strip()
    if value:
        return value

    # Then provider-specific conventional env vars.
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "grok": "XAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai_compatible": "OPENAI_API_KEY",
        "openai-chat-completions-endpoint": "OPENAI_API_KEY",
    }
    env_name = mapping.get(provider)
    if not env_name:
        return None
    value = (os.getenv(env_name) or "").strip()
    return value or None


async def _fetch_consent_record(conn, *, provider: str | None, model: str | None, endpoint: str | None) -> dict[str, Any] | None:
    if not provider and not model and not endpoint:
        return None
    row = await conn.fetchrow(
        """
        SELECT decision, signature, provider, model, endpoint, decided_at, response
        FROM consent_log
        WHERE ($1::text IS NULL OR provider = $1::text)
          AND ($2::text IS NULL OR model = $2::text)
          AND ($3::text IS NULL OR endpoint = $3::text)
        ORDER BY decided_at DESC
        LIMIT 1
        """,
        provider,
        model,
        endpoint,
    )
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


async def _apply_existing_consent(conn, record: dict[str, Any]) -> dict[str, Any]:
    status_raw = await conn.fetchval("SELECT get_init_status() as status")
    status = status_raw if isinstance(status_raw, dict) else (json.loads(status_raw) if isinstance(status_raw, str) else {})
    if isinstance(status, dict) and status.get("stage") == "complete":
        return {"status": status}

    payload = {
        "decision": record.get("decision"),
        "signature": record.get("signature"),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "endpoint": record.get("endpoint"),
        "memories": [],
    }
    result_raw = await conn.fetchval("SELECT init_consent($1::jsonb) as result", json.dumps(payload))
    _ = result_raw  # kept for parity/debugging; init status is what the UI cares about.
    next_status_raw = await conn.fetchval("SELECT get_init_status() as status")
    next_status = (
        next_status_raw
        if isinstance(next_status_raw, dict)
        else (json.loads(next_status_raw) if isinstance(next_status_raw, str) else {})
    )
    return {"status": next_status}


@app.post("/api/init/consent/request")
async def init_consent_request(req: InitConsentRequest):
    pool = _pool
    if pool is None:
        return JSONResponse({"error": "Server not ready (no DB pool)"}, status_code=503)

    from core.llm import normalize_provider, chat_completion

    role = req.role if req.role in {"conscious", "subconscious"} else "conscious"
    llm = req.llm or ConsentLlmConfig()

    provider = normalize_provider((llm.provider or "").strip().lower() or "openai")
    model = (llm.model or "").strip()
    endpoint = (llm.endpoint or "").strip() or None
    api_key = (llm.api_key or "").strip() or None

    # Mirror the UI init behavior: some providers ignore endpoints.
    if provider in {"anthropic", "grok", "gemini"}:
        endpoint = None

    if not model:
        return JSONResponse({"error": "Missing model"}, status_code=400)

    if provider == "openai_compatible" and not endpoint:
        return JSONResponse({"error": "Missing endpoint"}, status_code=400)

    test_decision_raw = (os.getenv("HEXIS_TEST_CONSENT_DECISION") or "").strip().lower()
    use_mock_consent = os.getenv("HEXIS_CONSENT_MOCK") == "1" or bool(test_decision_raw)

    # Check for existing consent records.
    existing: dict[str, Any] | None = None
    # Resolve API key for non-OAuth providers
    if not api_key:
        api_key = _resolve_fallback_api_key(provider, role)

    # Fail early if we need a key (unless mocked).
    if not use_mock_consent and provider in {"openai", "anthropic", "grok", "gemini"} and not api_key:
        return JSONResponse({"error": "Missing API key"}, status_code=400)

    async with pool.acquire() as conn:
        existing = await _fetch_consent_record(conn, provider=provider, model=model, endpoint=endpoint)
        if existing:
            if role == "conscious":
                applied = await _apply_existing_consent(conn, existing)
                return JSONResponse({"consent_record": existing, "reused": True, "status": applied.get("status")})
            return JSONResponse({"consent_record": existing, "reused": True, "status": None})

    # No existing record; request consent from the configured provider/model.
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "services", "prompts", "consent.md")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            consent_text = f.read()
    except OSError:
        consent_text = "Consent prompt missing. Respond with JSON only."

    system_prompt = (
        consent_text.strip()
        + "\n\nReturn STRICT JSON only with keys:\n"
        + "{\n"
        + '  "decision": "consent"|"decline"|"abstain",\n'
        + '  "signature": "required if decision=consent",\n'
        + '  "memories": [\n'
        + '    {"type": "semantic|episodic|procedural|strategic", "content": "...", "importance": 0.5}\n'
        + "  ]\n"
        + "}\n"
        + "If you consent, include a signature string and any memories you wish to pass along."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Respond with JSON only."},
    ]

    sign_consent_tool = {
        "type": "function",
        "function": {
            "name": "sign_consent",
            "description": "Records the agent's consent decision for initialization, including a signature if consenting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["consent", "decline", "abstain"]},
                    "signature": {"type": "string"},
                    "memories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["semantic", "episodic", "procedural", "strategic"],
                                },
                                "content": {"type": "string"},
                                "importance": {"type": "number"},
                            },
                            "required": ["type", "content"],
                        },
                    },
                },
                "required": ["decision"],
            },
        },
    }

    raw_text = ""
    args: dict[str, Any] = {}
    request_id: str | None = None

    if use_mock_consent:
        decision = test_decision_raw if test_decision_raw in {"consent", "decline", "abstain"} else "consent"
        signature = (os.getenv("HEXIS_TEST_CONSENT_SIGNATURE") or "test-consent").strip()
        payload = {"decision": decision, "signature": signature if decision == "consent" else None, "memories": []}
        args = payload
        raw_text = json.dumps(payload)
        request_id = "mock-consent"
    else:
        result = await chat_completion(
            provider=provider,
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            messages=messages,
            tools=[sign_consent_tool],
            temperature=0.2,
            max_tokens=1400,
        )
        content_text = str(result.get("content") or "")
        tool_calls = result.get("tool_calls") or []
        for tc in tool_calls:
            if tc.get("name") == "sign_consent":
                tc_args = tc.get("arguments")
                if isinstance(tc_args, dict):
                    args = tc_args
                break
        if not args:
            from core.llm_json import extract_json_object
            args = extract_json_object(content_text)
        raw_text = json.dumps(args) if args else content_text

    decision = str(args.get("decision") or "abstain").strip().lower()
    if decision not in {"consent", "decline", "abstain"}:
        decision = "abstain"
    signature = args.get("signature") if isinstance(args.get("signature"), str) else None
    memories = args.get("memories") if isinstance(args.get("memories"), list) else []

    payload = {
        "decision": decision,
        "signature": signature,
        "memories": memories,
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "request_id": request_id,
        "consent_scope": role,
        "apply_agent_config": role == "conscious",
        "raw_response": raw_text,
    }

    async with pool.acquire() as conn:
        if role == "conscious":
            result_raw = await conn.fetchval("SELECT init_consent($1::jsonb) as result", json.dumps(payload))
        else:
            result_raw = await conn.fetchval("SELECT record_consent_response($1::jsonb) as result", json.dumps(payload))

        result = (
            result_raw
            if isinstance(result_raw, dict)
            else (json.loads(result_raw) if isinstance(result_raw, str) else result_raw)
        )
        status_raw = await conn.fetchval("SELECT get_init_status() as status")
        status = (
            status_raw
            if isinstance(status_raw, dict)
            else (json.loads(status_raw) if isinstance(status_raw, str) else {})
        )
        consent_record = await _fetch_consent_record(conn, provider=provider, model=model, endpoint=endpoint)

    return JSONResponse(
        {
            "decision": decision,
            "contract": payload,
            "result": result,
            "consent_record": consent_record,
            "status": status,
        }
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    from core.config_loader import load_config
    load_config()

    parser = argparse.ArgumentParser(description="Hexis API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=43817, help="Port (default: 43817)")
    args = parser.parse_args(argv)

    import uvicorn
    uvicorn.run(
        "apps.hexis_api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
