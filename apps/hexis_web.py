"""
Hexis Web API Server

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
from typing import Any, AsyncIterator

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core.agent_api import db_dsn_from_env, get_agent_profile_context
from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
from core.cli_api import status_payload_rich
from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt
from core.llm_config import load_llm_config
from core.tools import ToolContext, create_default_registry
from services.chat import _build_system_prompt, _remember_conversation

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
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    logger.info("Hexis Web API started (pool created)")
    yield
    if _pool:
        await _pool.close()
        logger.info("Pool closed")


app = FastAPI(title="Hexis Web API", lifespan=lifespan)

# CORS — allow Next.js dev server and configurable origins
_cors_origins = os.getenv("HEXIS_CORS_ORIGINS", "http://localhost:3477,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] | None = None
    prompt_addenda: list[str] | None = None


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
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    try:
        payload = await status_payload_rich()
        return JSONResponse(payload)
    except Exception as e:
        logger.error("Status failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


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
    Run the AgentLoop in streaming mode and yield SSE events that match
    the format the Next.js frontend already parses.

    Event mapping:
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

    try:
        # Load LLM config from DB
        async with pool.acquire() as conn:
            llm_config = await load_llm_config(conn, "llm.chat", fallback_key="llm")

        # Build registry, profile, system prompt
        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(dsn)
        system_prompt = await _build_system_prompt(agent_profile, registry)

        # Hydrate memory context
        async with CognitiveMemory.connect(dsn) as mem_client:
            context = await mem_client.hydrate(
                user_message,
                memory_limit=10,
                include_partial=True,
                include_identity=True,
                include_worldview=True,
                include_emotional_state=True,
                include_drives=True,
            )
            if context.memories:
                await mem_client.touch_memories([m.id for m in context.memories])

            memory_context = format_context_for_prompt(context)

            # Log memory recall
            if context.memories:
                yield _sse_event("log", {
                    "id": str(uuid.uuid4()),
                    "kind": "memory_recall",
                    "title": "Memory Recall",
                    "detail": f"Retrieved {len(context.memories)} relevant memories",
                })

            if memory_context:
                enriched_user_message = f"{memory_context}\n\n[USER MESSAGE]\n{user_message}"
            else:
                enriched_user_message = user_message

            # Configure agent loop
            loop_config = AgentLoopConfig(
                tool_context=ToolContext.CHAT,
                system_prompt=system_prompt,
                llm_config=llm_config,
                registry=registry,
                pool=pool,
                energy_budget=None,
                max_iterations=6,
                timeout_seconds=120.0,
                temperature=0.7,
                max_tokens=1200,
                session_id=session_id,
            )

            agent = AgentLoop(loop_config)
            full_text = ""

            # Signal conscious_final phase start
            yield _sse_event("phase_start", {"phase": "conscious_final"})

            async for event in agent.stream(enriched_user_message, history=history):
                if event.event == AgentEvent.TEXT_DELTA:
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
            yield _sse_event("phase_end", {"phase": "conscious_final"})

            # Memory formation
            if full_text:
                try:
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Hexis Web API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=3478, help="Port (default: 3478)")
    args = parser.parse_args(argv)

    import uvicorn
    uvicorn.run(
        "apps.hexis_web:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
