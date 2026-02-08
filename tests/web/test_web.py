"""
Tests for the Hexis API server (apps/hexis_api.py).

Uses httpx.AsyncClient with the FastAPI app directly (no server needed).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import apps.hexis_api as web_module
from apps.hexis_api import app

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
async def client(db_pool):
    """Create an httpx async test client with the DB pool injected."""
    import httpx

    # Inject the real pool into the web module
    original_pool = web_module._pool
    web_module._pool = db_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
    web_module._pool = original_pool


async def test_health(client):
    """Health endpoint returns 200 with status ok."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_status(client):
    """Status endpoint returns agent info."""
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    # Should have at least instance and identity keys
    assert "instance" in data or "identity" in data or "memories" in data


async def test_chat_returns_sse_stream(client):
    """
    Chat endpoint returns an SSE stream with expected event types.

    We mock the LLM call and memory hydration to avoid needing a real
    API key or embedding service.
    """
    from contextlib import asynccontextmanager
    from core.cognitive_memory_api import HydratedContext

    mock_response = {
        "content": "Hello! I'm Hexis.",
        "tool_calls": [],
        "raw": {},
    }

    # Mock CognitiveMemory.connect() to return a mock client that avoids embeddings
    mock_mem = AsyncMock()
    mock_mem.hydrate = AsyncMock(return_value=HydratedContext(
        memories=[], partial_activations=[], identity=[], worldview=[],
        emotional_state=None, goals=None, urgent_drives=[],
    ))
    mock_mem.touch_memories = AsyncMock()
    mock_mem.remember = AsyncMock()

    @asynccontextmanager
    async def mock_connect(*args, **kwargs):
        yield mock_mem

    with patch("apps.hexis_api.CognitiveMemory.connect", side_effect=mock_connect):
        with patch("core.agent_loop.stream_chat_completion", new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = mock_response
            with patch("core.agent_loop.chat_completion", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = mock_response
                resp = await client.post(
                    "/api/chat",
                    json={"message": "Hello, who are you?"},
                )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    # Parse SSE events
    events = _parse_sse(resp.text)
    event_types = [e["event"] for e in events]

    # Must have phase_start and done at minimum
    assert "phase_start" in event_types, f"Expected phase_start in events: {event_types}"
    assert "done" in event_types, f"Expected done in events: {event_types}"

    # The done event should contain the full assistant text
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1
    done_payload = json.loads(done_events[0]["data"])
    assert "assistant" in done_payload


async def test_chat_missing_message(client):
    """Chat endpoint rejects requests without a message."""
    resp = await client.post("/api/chat", json={})
    assert resp.status_code == 422  # Pydantic validation error


def _parse_sse(text: str) -> list[dict[str, str]]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        if data:
            events.append({"event": event_type, "data": data})
    return events
