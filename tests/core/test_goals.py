"""
Tests for core/tools/goals.py — ManageGoalsHandler.

Covers: create, update_priority, add_progress, list actions,
validation, missing params, and DB integration.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tools.base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolResult,
)
from core.tools.goals import ManageGoalsHandler, create_goal_tools

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


# ============================================================================
# Helpers
# ============================================================================


def _make_exec_context(*, pool=None, registry=None) -> ToolExecutionContext:
    """Build a ToolExecutionContext with optional mocked registry."""
    reg = registry or MagicMock()
    if pool:
        reg.pool = pool
    ctx = ToolExecutionContext(
        tool_context=ToolContext.HEARTBEAT,
        call_id=str(uuid.uuid4()),
    )
    ctx.registry = reg
    return ctx


# ============================================================================
# Unit: spec
# ============================================================================


class TestGoalsSpec:
    def test_spec_name(self):
        handler = ManageGoalsHandler()
        assert handler.spec.name == "manage_goals"

    def test_spec_category(self):
        handler = ManageGoalsHandler()
        assert handler.spec.category == ToolCategory.MEMORY

    def test_spec_allowed_contexts(self):
        handler = ManageGoalsHandler()
        assert ToolContext.HEARTBEAT in handler.spec.allowed_contexts
        assert ToolContext.CHAT in handler.spec.allowed_contexts
        assert ToolContext.MCP in handler.spec.allowed_contexts

    def test_spec_energy_cost(self):
        handler = ManageGoalsHandler()
        assert handler.spec.energy_cost == 1

    def test_create_goal_tools_returns_list(self):
        tools = create_goal_tools()
        assert len(tools) == 1
        assert isinstance(tools[0], ManageGoalsHandler)


# ============================================================================
# Unit: validation
# ============================================================================


class TestGoalsValidation:
    async def test_invalid_action_rejected(self):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = MagicMock()
        ctx = _make_exec_context(registry=registry)

        result = await handler.execute({"action": "invalid_action"}, ctx)
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_missing_action_rejected(self):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = MagicMock()
        ctx = _make_exec_context(registry=registry)

        result = await handler.execute({}, ctx)
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_create_requires_title(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute({"action": "create"}, ctx)
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_create_blank_title_rejected(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute({"action": "create", "title": "   "}, ctx)
        assert not result.success

    async def test_update_priority_requires_goal_id(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute(
            {"action": "update_priority", "priority": "active"}, ctx
        )
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_update_priority_requires_valid_priority(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute(
            {"action": "update_priority", "goal_id": "abc", "priority": "mega_urgent"},
            ctx,
        )
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_add_progress_requires_goal_id(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute(
            {"action": "add_progress", "note": "some progress"}, ctx
        )
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_add_progress_requires_note(self):
        handler = ManageGoalsHandler()
        mock_pool = MagicMock()
        registry = MagicMock()
        registry.pool = mock_pool
        ctx = _make_exec_context(pool=mock_pool, registry=registry)

        result = await handler.execute(
            {"action": "add_progress", "goal_id": "abc"}, ctx
        )
        assert not result.success
        assert result.error_type == ToolErrorType.INVALID_PARAMS

    async def test_no_pool_returns_error(self):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = None
        ctx = _make_exec_context(registry=registry)

        result = await handler.execute({"action": "list"}, ctx)
        assert not result.success
        assert result.error_type == ToolErrorType.MISSING_CONFIG


# ============================================================================
# Integration: DB operations
# ============================================================================


class TestGoalsIntegration:
    async def test_create_goal(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        result = await handler.execute(
            {
                "action": "create",
                "title": "Test Goal from pytest",
                "description": "Integration test goal",
                "source": "curiosity",
                "priority": "queued",
            },
            ctx,
        )
        assert result.success
        output = result.output
        assert "goal_id" in output
        assert output["title"] == "Test Goal from pytest"
        assert output["priority"] == "queued"

        # Cleanup: goals are stored in the 'memories' table with type='goal'
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1::uuid", output["goal_id"])

    async def test_create_goal_invalid_source_defaults(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        result = await handler.execute(
            {
                "action": "create",
                "title": "Goal with bad source",
                "source": "nonexistent_source",
            },
            ctx,
        )
        assert result.success
        # Invalid source defaults to "curiosity"

        # Cleanup
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1::uuid", result.output["goal_id"])

    async def test_list_goals(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        # Create a goal first
        create_result = await handler.execute(
            {"action": "create", "title": "Listable Goal", "priority": "active"},
            ctx,
        )
        assert create_result.success
        goal_id = create_result.output["goal_id"]

        try:
            # List goals
            list_result = await handler.execute({"action": "list"}, ctx)
            assert list_result.success
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal_id)

    async def test_list_goals_by_priority(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        # Create a queued goal
        create_result = await handler.execute(
            {"action": "create", "title": "Queued Goal", "priority": "queued"},
            ctx,
        )
        assert create_result.success
        goal_id = create_result.output["goal_id"]

        try:
            list_result = await handler.execute(
                {"action": "list", "priority": "queued"}, ctx
            )
            assert list_result.success
            output = list_result.output
            assert "goals" in output
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal_id)

    async def test_update_priority(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        # Create a goal
        create_result = await handler.execute(
            {"action": "create", "title": "Priority Test Goal", "priority": "queued"},
            ctx,
        )
        assert create_result.success
        goal_id = create_result.output["goal_id"]

        try:
            # Update priority
            result = await handler.execute(
                {
                    "action": "update_priority",
                    "goal_id": goal_id,
                    "priority": "active",
                    "reason": "Promoting to active",
                },
                ctx,
            )
            assert result.success
            assert result.output["new_priority"] == "active"
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal_id)

    async def test_add_progress(self, db_pool):
        handler = ManageGoalsHandler()
        registry = MagicMock()
        registry.pool = db_pool
        ctx = _make_exec_context(pool=db_pool, registry=registry)

        # Create a goal
        create_result = await handler.execute(
            {"action": "create", "title": "Progress Test Goal", "priority": "active"},
            ctx,
        )
        assert create_result.success
        goal_id = create_result.output["goal_id"]

        try:
            result = await handler.execute(
                {
                    "action": "add_progress",
                    "goal_id": goal_id,
                    "note": "Made some progress on this goal",
                },
                ctx,
            )
            assert result.success
            assert result.output["goal_id"] == goal_id
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute("DELETE FROM memories WHERE id = $1::uuid", goal_id)
