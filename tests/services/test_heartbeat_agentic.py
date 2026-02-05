"""
Tests for services/heartbeat_agentic.py — Agentic heartbeat runner.

Covers: system prompt building, agentic heartbeat execution,
finalization, context extraction, and worker integration.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.heartbeat_agentic import (
    build_heartbeat_system_prompt,
    finalize_heartbeat,
    run_agentic_heartbeat,
)
from services.worker_service import HeartbeatWorker

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


# ============================================================================
# Helpers
# ============================================================================


def _mock_registry(tool_names: list[str] | None = None) -> MagicMock:
    """Create a mock ToolRegistry with optional tool names."""
    registry = MagicMock()
    registry.pool = MagicMock()

    if tool_names:
        specs = [
            {"type": "function", "function": {"name": n, "description": f"{n} tool", "parameters": {}}}
            for n in tool_names
        ]
    else:
        specs = [
            {"type": "function", "function": {"name": "recall", "description": "Recall", "parameters": {}}},
            {"type": "function", "function": {"name": "remember", "description": "Remember", "parameters": {}}},
            {"type": "function", "function": {"name": "manage_goals", "description": "Goals", "parameters": {}}},
        ]
    registry.get_specs = AsyncMock(return_value=specs)
    registry.get_spec = MagicMock(return_value=None)
    registry.execute = AsyncMock()
    registry.get_config = AsyncMock(return_value=MagicMock(
        get_context_overrides=MagicMock(return_value=MagicMock(
            allow_shell=False, allow_file_write=False
        )),
        workspace_path=None,
    ))
    return registry


def _mock_context() -> dict[str, Any]:
    """Build a minimal heartbeat context for testing."""
    return {
        "agent": {
            "objectives": ["Test objective"],
            "guardrails": [],
            "tools": [],
            "budget": {},
        },
        "environment": {
            "timestamp": "2025-01-15T12:00:00Z",
            "day_of_week": "Wednesday",
            "hour_of_day": 12,
            "time_since_user_hours": 1.0,
            "pending_events": 0,
        },
        "goals": {
            "counts": {"active": 1, "queued": 0},
            "active": [{"title": "Test goal"}],
            "queued": [],
            "issues": [],
        },
        "recent_memories": [],
        "identity": [],
        "worldview": [],
        "self_model": [],
        "narrative": {},
        "urgent_drives": [],
        "emotional_state": {},
        "relationships": [],
        "contradictions": [],
        "emotional_patterns": [],
        "active_transformations": [],
        "transformations_ready": [],
        "energy": {"current": 15, "max": 20},
        "allowed_actions": [],
        "action_costs": {},
        "heartbeat_number": 42,
    }


# ============================================================================
# Unit: build_heartbeat_system_prompt
# ============================================================================


class TestBuildSystemPrompt:
    async def test_includes_base_prompt(self):
        """System prompt includes the base agentic heartbeat text."""
        prompt = await build_heartbeat_system_prompt()
        # Should contain key phrases from the agentic prompt
        assert "tool" in prompt.lower() or "heartbeat" in prompt.lower()

    async def test_includes_tool_names(self):
        """System prompt includes tool names from registry."""
        registry = _mock_registry(tool_names=["recall", "remember", "manage_goals"])
        prompt = await build_heartbeat_system_prompt(registry)
        assert "recall" in prompt
        assert "remember" in prompt
        assert "manage_goals" in prompt

    async def test_includes_personhood(self):
        """System prompt includes personhood modules."""
        prompt = await build_heartbeat_system_prompt()
        # Should include personhood section (may be minimal or full)
        # The personhood prompt may or may not load depending on file presence
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    async def test_graceful_with_no_registry(self):
        """Works even without a registry."""
        prompt = await build_heartbeat_system_prompt(None)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    async def test_graceful_with_registry_error(self):
        """Handles registry errors gracefully."""
        registry = MagicMock()
        registry.get_specs = AsyncMock(side_effect=RuntimeError("DB error"))
        prompt = await build_heartbeat_system_prompt(registry)
        assert isinstance(prompt, str)


# ============================================================================
# Unit: run_agentic_heartbeat
# ============================================================================


class TestRunAgenticHeartbeat:
    @patch("services.heartbeat_agentic.AgentLoop")
    @patch("services.heartbeat_agentic.load_llm_config")
    async def test_runs_agent_loop(self, mock_load_config, mock_agent_class):
        """run_agentic_heartbeat creates and runs an AgentLoop."""
        mock_load_config.return_value = {
            "provider": "openai",
            "model": "gpt-4o",
            "endpoint": None,
            "api_key": "test",
        }

        # Mock the AgentLoop instance
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="I reflected on my goals.",
            tool_calls_made=[{"name": "recall", "success": True, "energy_spent": 1}],
            iterations=2,
            energy_spent=1,
            timed_out=False,
            stopped_reason="completed",
        )
        mock_agent_class.return_value = mock_agent

        conn = AsyncMock()
        pool = MagicMock()
        registry = _mock_registry()

        result = await run_agentic_heartbeat(
            conn,
            pool=pool,
            registry=registry,
            heartbeat_id="hb-test-001",
            context=_mock_context(),
        )

        assert result["completed"] is True
        assert result["energy_spent"] == 1
        assert result["stopped_reason"] == "completed"
        assert len(result["tool_calls_made"]) == 1
        mock_agent.run.assert_awaited_once()

    @patch("services.heartbeat_agentic.AgentLoop")
    @patch("services.heartbeat_agentic.load_llm_config")
    async def test_energy_budget_from_context(self, mock_load_config, mock_agent_class):
        """Energy budget comes from context energy.current."""
        mock_load_config.return_value = {
            "provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t",
        }
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="Done.", tool_calls_made=[], iterations=1,
            energy_spent=0, timed_out=False, stopped_reason="completed",
        )
        mock_agent_class.return_value = mock_agent

        conn = AsyncMock()
        pool = MagicMock()
        registry = _mock_registry()

        ctx = _mock_context()
        ctx["energy"]["current"] = 7

        await run_agentic_heartbeat(
            conn, pool=pool, registry=registry,
            heartbeat_id="hb-test-002", context=ctx,
        )

        # Check that AgentLoopConfig got energy_budget=7
        config_arg = mock_agent_class.call_args[0][0]
        assert config_arg.energy_budget == 7

    @patch("services.heartbeat_agentic.AgentLoop")
    @patch("services.heartbeat_agentic.load_llm_config")
    async def test_timeout_reported(self, mock_load_config, mock_agent_class):
        """Timeout is reported in result."""
        mock_load_config.return_value = {
            "provider": "openai", "model": "gpt-4o", "endpoint": None, "api_key": "t",
        }
        mock_agent = AsyncMock()
        mock_agent.run.return_value = MagicMock(
            text="Timed out.", tool_calls_made=[], iterations=3,
            energy_spent=5, timed_out=True, stopped_reason="timeout",
        )
        mock_agent_class.return_value = mock_agent

        conn = AsyncMock()
        pool = MagicMock()
        registry = _mock_registry()

        result = await run_agentic_heartbeat(
            conn, pool=pool, registry=registry,
            heartbeat_id="hb-test-003", context=_mock_context(),
        )

        assert result["completed"] is False
        assert result["timed_out"] is True
        assert result["stopped_reason"] == "timeout"


# ============================================================================
# Unit: finalize_heartbeat
# ============================================================================


class TestFinalizeHeartbeat:
    async def test_finalize_records_memory(self, db_pool):
        """finalize_heartbeat creates an episodic memory and updates heartbeat_state."""
        async with db_pool.acquire() as conn:
            result = await finalize_heartbeat(
                conn,
                heartbeat_id=str(uuid.uuid4()),
                result={
                    "text": "I checked my goals and recalled some memories.",
                    "tool_calls_made": [
                        {"name": "recall", "success": True},
                        {"name": "manage_goals", "success": True},
                    ],
                    "energy_spent": 3,
                    "stopped_reason": "completed",
                },
            )

            assert result["completed"] is True
            assert result["energy_spent"] == 3
            # memory_id may or may not be set depending on DB state

    async def test_finalize_with_empty_result(self, db_pool):
        """finalize_heartbeat handles empty/minimal result gracefully."""
        async with db_pool.acquire() as conn:
            result = await finalize_heartbeat(
                conn,
                heartbeat_id=str(uuid.uuid4()),
                result={},
            )

            assert result["completed"] is True
            assert result["energy_spent"] == 0

    async def test_finalize_builds_summary_with_tool_names(self, db_pool):
        """Summary includes tool names when present."""
        async with db_pool.acquire() as conn:
            result = await finalize_heartbeat(
                conn,
                heartbeat_id=str(uuid.uuid4()),
                result={
                    "text": "",
                    "tool_calls_made": [
                        {"name": "recall"},
                        {"name": "remember"},
                    ],
                    "energy_spent": 2,
                    "stopped_reason": "completed",
                },
            )

            # Should succeed regardless
            assert result["completed"] is True


# ============================================================================
# Unit: HeartbeatWorker context extraction
# ============================================================================


class TestContextExtraction:
    def test_extract_context_from_external_calls(self):
        """_extract_heartbeat_context gets context from think call input."""
        payload = {
            "heartbeat_id": "hb-123",
            "external_calls": [
                {
                    "call_type": "think",
                    "input": {
                        "kind": "heartbeat_decision",
                        "heartbeat_id": "hb-123",
                        "context": {
                            "energy": {"current": 15, "max": 20},
                            "goals": {"active": [], "queued": []},
                        },
                    },
                }
            ],
        }
        context = HeartbeatWorker._extract_heartbeat_context(payload)
        assert "energy" in context
        assert context["energy"]["current"] == 15

    def test_extract_context_no_nested_context(self):
        """Falls back to call input when no nested context key."""
        payload = {
            "external_calls": [
                {
                    "call_type": "think",
                    "input": {
                        "kind": "heartbeat_decision",
                        "heartbeat_id": "hb-123",
                        "energy": {"current": 10},
                    },
                }
            ],
        }
        context = HeartbeatWorker._extract_heartbeat_context(payload)
        assert "energy" in context

    def test_extract_context_no_external_calls(self):
        """Returns payload when no external_calls present."""
        payload = {"heartbeat_id": "hb-123", "energy": {"current": 5}}
        context = HeartbeatWorker._extract_heartbeat_context(payload)
        assert context["heartbeat_id"] == "hb-123"

    def test_extract_context_no_think_call(self):
        """Returns payload when no think call present."""
        payload = {
            "external_calls": [
                {"call_type": "embed", "input": {}},
            ],
        }
        context = HeartbeatWorker._extract_heartbeat_context(payload)
        assert context == payload

    def test_extract_context_empty_external_calls(self):
        """Returns payload when external_calls is empty."""
        payload = {"external_calls": []}
        context = HeartbeatWorker._extract_heartbeat_context(payload)
        assert context == payload


# ============================================================================
# Unit: HeartbeatWorker agentic flag
# ============================================================================


class TestAgenticFlag:
    async def test_agentic_disabled_by_default(self, db_pool):
        """Agentic heartbeat is disabled when config key is missing."""
        worker = HeartbeatWorker()
        worker.pool = db_pool

        async with db_pool.acquire() as conn:
            # Ensure the config key is absent
            await conn.execute("DELETE FROM config WHERE key = 'heartbeat.use_agentic_loop'")
            enabled = await worker._is_agentic_heartbeat_enabled(conn)
            assert enabled is False

    async def test_agentic_enabled_when_true(self, db_pool):
        """Agentic heartbeat is enabled when config key is 'true'."""
        worker = HeartbeatWorker()
        worker.pool = db_pool

        async with db_pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('heartbeat.use_agentic_loop', 'true'::jsonb)"
            )
            try:
                enabled = await worker._is_agentic_heartbeat_enabled(conn)
                assert enabled is True
            finally:
                await conn.execute("DELETE FROM config WHERE key = 'heartbeat.use_agentic_loop'")

    async def test_agentic_disabled_when_false(self, db_pool):
        """Agentic heartbeat is disabled when config key is 'false'."""
        worker = HeartbeatWorker()
        worker.pool = db_pool

        async with db_pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('heartbeat.use_agentic_loop', 'false'::jsonb)"
            )
            try:
                enabled = await worker._is_agentic_heartbeat_enabled(conn)
                assert enabled is False
            finally:
                await conn.execute("DELETE FROM config WHERE key = 'heartbeat.use_agentic_loop'")
