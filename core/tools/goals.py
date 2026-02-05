"""
Hexis Tools System - Goal Management

Allows the agent to create, update, complete, and manage goals
through the standard tool_use interface.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_VALID_PRIORITIES = {"active", "queued", "backburner", "completed", "abandoned"}
_VALID_SOURCES = {"curiosity", "user_request", "identity", "derived", "external"}
_VALID_ACTIONS = {"create", "update_priority", "add_progress", "list"}


class ManageGoalsHandler(ToolHandler):
    """Manage the agent's goals: create, reprioritize, add progress notes, or list."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="manage_goals",
            description=(
                "Manage your goals. Actions: "
                "'create' (new goal), "
                "'update_priority' (change priority), "
                "'add_progress' (record progress on a goal), "
                "'list' (view current goals)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_VALID_ACTIONS),
                        "description": "The goal management action to perform.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for a new goal (required for 'create').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description for a new goal (optional for 'create').",
                    },
                    "source": {
                        "type": "string",
                        "enum": list(_VALID_SOURCES),
                        "description": "Source of the goal (for 'create'). Default: 'curiosity'.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(_VALID_PRIORITIES),
                        "description": "Priority level. Used for 'create' (default 'queued') and 'update_priority'.",
                    },
                    "goal_id": {
                        "type": "string",
                        "description": "Goal ID (required for 'update_priority' and 'add_progress').",
                    },
                    "note": {
                        "type": "string",
                        "description": "Progress note (required for 'add_progress').",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the priority change (optional for 'update_priority').",
                    },
                },
                "required": ["action"],
            },
            category=ToolCategory.MEMORY,
            energy_cost=1,
            is_read_only=False,
            requires_approval=False,
            allowed_contexts={ToolContext.HEARTBEAT, ToolContext.CHAT, ToolContext.MCP},
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        action = arguments.get("action", "")
        if action not in _VALID_ACTIONS:
            return ToolResult.error_result(
                f"Invalid action '{action}'. Must be one of: {', '.join(sorted(_VALID_ACTIONS))}",
                ToolErrorType.INVALID_PARAMS,
            )

        pool = context.registry.pool if context.registry else None
        if not pool:
            return ToolResult.error_result(
                "Database pool not available",
                ToolErrorType.MISSING_CONFIG,
            )

        if action == "create":
            return await self._create_goal(pool, arguments)
        if action == "update_priority":
            return await self._update_priority(pool, arguments)
        if action == "add_progress":
            return await self._add_progress(pool, arguments)
        if action == "list":
            return await self._list_goals(pool, arguments)

        return ToolResult.error_result(f"Unhandled action: {action}")

    async def _create_goal(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        title = (args.get("title") or "").strip()
        if not title:
            return ToolResult.error_result("Title is required for create", ToolErrorType.INVALID_PARAMS)

        description = args.get("description")
        source = args.get("source", "curiosity")
        priority = args.get("priority", "queued")

        if source not in _VALID_SOURCES:
            source = "curiosity"
        if priority not in _VALID_PRIORITIES:
            priority = "queued"

        try:
            async with pool.acquire() as conn:
                goal_id = await conn.fetchval(
                    "SELECT create_goal($1, $2, $3::goal_source, $4::goal_priority)",
                    title,
                    description,
                    source,
                    priority,
                )
            return ToolResult.success_result(
                {"goal_id": str(goal_id), "title": title, "priority": priority},
                display_output=f"Created goal: {title} ({priority})",
            )
        except Exception as e:
            logger.error("Failed to create goal: %s", e)
            return ToolResult.error_result(f"Failed to create goal: {e}")

    async def _update_priority(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        goal_id = (args.get("goal_id") or "").strip()
        if not goal_id:
            return ToolResult.error_result("goal_id is required for update_priority", ToolErrorType.INVALID_PARAMS)

        priority = args.get("priority", "")
        if priority not in _VALID_PRIORITIES:
            return ToolResult.error_result(
                f"Invalid priority '{priority}'. Must be one of: {', '.join(sorted(_VALID_PRIORITIES))}",
                ToolErrorType.INVALID_PARAMS,
            )

        reason = args.get("reason", "")

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT change_goal_priority($1::uuid, $2::goal_priority, $3)",
                    goal_id,
                    priority,
                    reason,
                )
            return ToolResult.success_result(
                {"goal_id": goal_id, "new_priority": priority, "reason": reason},
                display_output=f"Updated goal {goal_id[:8]}... to {priority}",
            )
        except Exception as e:
            logger.error("Failed to update goal priority: %s", e)
            return ToolResult.error_result(f"Failed to update goal priority: {e}")

    async def _add_progress(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        goal_id = (args.get("goal_id") or "").strip()
        if not goal_id:
            return ToolResult.error_result("goal_id is required for add_progress", ToolErrorType.INVALID_PARAMS)

        note = (args.get("note") or "").strip()
        if not note:
            return ToolResult.error_result("note is required for add_progress", ToolErrorType.INVALID_PARAMS)

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT add_goal_progress($1::uuid, $2)",
                    goal_id,
                    note,
                )
            return ToolResult.success_result(
                {"goal_id": goal_id, "note": note},
                display_output=f"Added progress to goal {goal_id[:8]}...",
            )
        except Exception as e:
            logger.error("Failed to add goal progress: %s", e)
            return ToolResult.error_result(f"Failed to add goal progress: {e}")

    async def _list_goals(self, pool: "asyncpg.Pool", args: dict[str, Any]) -> ToolResult:
        priority_filter = args.get("priority")

        try:
            async with pool.acquire() as conn:
                if priority_filter and priority_filter in _VALID_PRIORITIES:
                    rows = await conn.fetch(
                        "SELECT * FROM get_goals_by_priority($1::goal_priority)",
                        priority_filter,
                    )
                else:
                    snapshot = await conn.fetchval("SELECT get_goals_snapshot()")
                    if snapshot:
                        parsed = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                        return ToolResult.success_result(parsed)
                    rows = []

            goals = []
            for row in rows:
                goals.append({
                    "id": str(row["id"]),
                    "title": row["title"],
                    "description": row.get("description"),
                    "priority": row["priority"],
                    "source": row.get("source"),
                })
            return ToolResult.success_result({"goals": goals, "count": len(goals)})
        except Exception as e:
            logger.error("Failed to list goals: %s", e)
            return ToolResult.error_result(f"Failed to list goals: {e}")


def create_goal_tools() -> list[ToolHandler]:
    """Create goal management tools."""
    return [ManageGoalsHandler()]
