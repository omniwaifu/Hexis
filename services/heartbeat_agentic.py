"""
Agentic Heartbeat Runner

Runs a heartbeat cycle using the unified AgentLoop. Replaces the legacy
JSON-decision path with direct tool_use. The LLM uses real tools (recall,
remember, reflect, manage_goals, etc.) within its energy budget.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from core.tools.config import ContextOverrides
from services.agent import run_agent
from services.heartbeat_prompt import build_heartbeat_decision_prompt

if TYPE_CHECKING:
    import asyncpg
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _has_backlog_tasks(context: dict[str, Any]) -> bool:
    """Check if backlog has actionable items requiring elevated resources/permissions."""
    backlog = context.get("backlog", {})
    if not isinstance(backlog, dict):
        return False
    actionable = backlog.get("actionable", [])
    if isinstance(actionable, list) and len(actionable) > 0:
        return True
    counts = backlog.get("counts", {})
    if isinstance(counts, dict):
        todo = counts.get("todo", 0) or 0
        in_progress = counts.get("in_progress", 0) or 0
        if todo + in_progress > 0:
            return True
    return False


def _get_checkpoint_context(context: dict[str, Any]) -> str:
    """Extract checkpoint info from in-progress backlog items for prompt inclusion."""
    backlog = context.get("backlog", {})
    if not isinstance(backlog, dict):
        return ""
    actionable = backlog.get("actionable", [])
    if not isinstance(actionable, list):
        return ""

    checkpoint_parts: list[str] = []
    for item in actionable:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "in_progress":
            continue
        checkpoint = item.get("checkpoint")
        if not isinstance(checkpoint, dict) or not checkpoint:
            continue
        title = item.get("title", "Untitled")
        step = checkpoint.get("step", "unknown")
        progress = checkpoint.get("progress", "")
        next_action = checkpoint.get("next_action", "")
        checkpoint_parts.append(
            f"### Resuming: {title}\n"
            f"- Last step: {step}\n"
            f"- Progress: {progress}\n"
            f"- Next action: {next_action}"
        )
    if not checkpoint_parts:
        return ""
    return "\n\n## Checkpoint Resume\n\n" + "\n\n".join(checkpoint_parts)


async def build_heartbeat_system_prompt(
    registry: "ToolRegistry | None" = None,
    *,
    has_backlog_tasks: bool = False,
) -> str:
    """Build the system prompt for an agentic heartbeat.

    Compatibility wrapper — delegates to services.agent.build_system_prompt().
    """
    from services.agent import build_system_prompt as _build
    from core.tools.registry import ToolRegistry as _TR

    if registry is None:
        # Create a minimal mock for the prompt builder
        class _NoopRegistry:
            async def get_specs(self, ctx):
                return []
        registry = _NoopRegistry()  # type: ignore[assignment]
    return await _build(
        "heartbeat",
        registry,  # type: ignore[arg-type]
        has_backlog_tasks=has_backlog_tasks,
    )


async def run_agentic_heartbeat(
    conn: "asyncpg.Connection",
    *,
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    heartbeat_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Run a single heartbeat cycle using the unified agent runner.

    Returns a dict with:
    - completed: bool
    - text: str (final agent text)
    - tool_calls_made: list
    - energy_spent: int
    - stopped_reason: str
    - has_backlog_tasks: bool
    """
    # Check if backlog has actionable tasks (gates resources + permissions)
    has_tasks = _has_backlog_tasks(context)
    if has_tasks:
        logger.info("Backlog has actionable items — scaling resources + granting permissions")

    # Build the user message (heartbeat context snapshot)
    user_message = build_heartbeat_decision_prompt(context)

    # Append checkpoint resume context if there are in-progress items with checkpoints
    if has_tasks:
        checkpoint_ctx = _get_checkpoint_context(context)
        if checkpoint_ctx:
            user_message += "\n" + checkpoint_ctx

    # Extract energy budget from context
    energy = context.get("energy", {})
    energy_budget = energy.get("current", 20)

    # Scale resources when backlog has work
    if has_tasks:
        energy_budget = energy_budget * 2
        logger.info("Backlog energy boost: %d → %d", energy_budget // 2, energy_budget)

    result = await run_agent(
        pool,
        registry,
        user_message=user_message,
        mode="heartbeat",
        energy_budget=energy_budget,
        heartbeat_id=heartbeat_id,
        heartbeat_context=context,
        has_backlog_tasks=has_tasks,
        timeout_seconds=300.0 if has_tasks else 120.0,
        max_tokens=4096 if has_tasks else 2048,
        context_overrides=ContextOverrides(
            allow_shell=True,
            allow_file_write=True,
        ) if has_tasks else None,
    )

    return {
        "completed": result.stopped_reason == "completed",
        "text": result.text,
        "tool_calls_made": result.tool_calls_made,
        "energy_spent": result.energy_spent,
        "iterations": result.iterations,
        "stopped_reason": result.stopped_reason,
        "timed_out": result.timed_out,
        "has_backlog_tasks": has_tasks,
    }


async def finalize_heartbeat(
    conn: "asyncpg.Connection",
    *,
    heartbeat_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Finalize a heartbeat after the agentic loop completes.

    Records the heartbeat as an episodic memory and updates state.
    If backlog had tasks, auto-checkpoints in-progress items that were
    not explicitly completed on timeout/energy exhaustion.
    """
    text = result.get("text", "")
    tool_calls = result.get("tool_calls_made", [])
    energy_spent = result.get("energy_spent", 0)
    stopped_reason = result.get("stopped_reason", "completed")
    has_tasks = result.get("has_backlog_tasks", False)

    # Build a summary of what happened
    tool_names = [tc.get("name", "?") for tc in tool_calls]
    summary = text or f"Heartbeat completed: {len(tool_calls)} tool calls, {energy_spent} energy spent."
    if tool_names:
        summary += f" Tools used: {', '.join(tool_names)}."
    if has_tasks:
        summary += " [backlog active]"

    # Record heartbeat as episodic memory
    try:
        memory_id = await conn.fetchval(
            """
            SELECT create_episodic_memory(
                p_content := $1,
                p_action := 'heartbeat',
                p_context := $2::jsonb,
                p_result := $3,
                p_importance := 0.5,
                p_trust_level := 1.0
            )
            """,
            summary[:2000],
            json.dumps({
                "heartbeat_id": heartbeat_id,
                "energy_spent": energy_spent,
                "tool_calls": len(tool_calls),
                "stopped_reason": stopped_reason,
                "has_backlog_tasks": has_tasks,
            }),
            "completed" if stopped_reason == "completed" else stopped_reason,
        )
    except Exception:
        memory_id = None
        logger.error("Failed to record heartbeat memory", exc_info=True)

    # Update heartbeat state (mark completion, deduct energy)
    try:
        await conn.execute(
            """
            UPDATE heartbeat_state
            SET last_heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """
        )
    except Exception:
        logger.error("Failed to update heartbeat state", exc_info=True)

    # Auto-checkpoint: if backlog had tasks and heartbeat was interrupted,
    # checkpoint any still-in-progress items so next heartbeat can resume
    if has_tasks and stopped_reason in ("timeout", "energy_exhausted"):
        try:
            in_progress_items = await conn.fetch(
                """
                SELECT id, title, checkpoint
                FROM public.backlog
                WHERE status = 'in_progress'
                ORDER BY updated_at DESC
                LIMIT 5
                """
            )
            for item in in_progress_items:
                existing_cp = item["checkpoint"]
                if existing_cp is None:
                    # Auto-create a minimal checkpoint
                    auto_checkpoint = json.dumps({
                        "step": "interrupted",
                        "progress": f"Heartbeat ended ({stopped_reason}). {len(tool_calls)} tool calls made.",
                        "next_action": "Continue from where left off",
                    })
                    await conn.execute(
                        """
                        UPDATE public.backlog
                        SET checkpoint = $1::jsonb, updated_at = CURRENT_TIMESTAMP
                        WHERE id = $2
                        """,
                        auto_checkpoint,
                        item["id"],
                    )
                    logger.info(
                        "Auto-checkpointed in-progress item %s: %s",
                        item["id"],
                        item["title"],
                    )
        except Exception:
            logger.debug("Failed to auto-checkpoint backlog items", exc_info=True)

    return {
        "completed": True,
        "memory_id": str(memory_id) if memory_id else None,
        "energy_spent": energy_spent,
        "outbox_messages": [],
        "has_backlog_tasks": has_tasks,
    }
