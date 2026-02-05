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

from core.agent_loop import AgentEvent, AgentEventData, AgentLoop, AgentLoopConfig
from core.llm_config import load_llm_config
from core.tools.base import ToolContext
from services.heartbeat_prompt import build_heartbeat_decision_prompt
from services.prompt_resources import compose_personhood_prompt, load_heartbeat_agentic_prompt

if TYPE_CHECKING:
    import asyncpg
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def build_heartbeat_system_prompt(registry: "ToolRegistry | None" = None) -> str:
    """Build the system prompt for an agentic heartbeat."""
    base_prompt = load_heartbeat_agentic_prompt().strip()

    personhood = ""
    try:
        personhood = compose_personhood_prompt("heartbeat")
    except Exception:
        logger.debug("Failed to compose personhood prompt", exc_info=True)

    # Add tool descriptions from registry
    tool_section = ""
    if registry:
        try:
            specs = await registry.get_specs(ToolContext.HEARTBEAT)
            tool_names = sorted(s["function"]["name"] for s in specs)
            tool_section = (
                "\n\n## Available Tools\n"
                + ", ".join(tool_names)
                + "\n\nUse these tools via tool_use to take actions. "
                "Each tool has its own parameters — the LLM API will show you the schemas."
            )
        except Exception:
            logger.debug("Failed to get tool specs for heartbeat prompt", exc_info=True)

    parts = [base_prompt]
    if tool_section:
        parts.append(tool_section)
    if personhood:
        parts.append(
            "\n\n----- PERSONHOOD MODULES (for grounding) -----\n\n"
            + personhood
        )
    return "\n".join(parts)


async def run_agentic_heartbeat(
    conn: "asyncpg.Connection",
    *,
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    heartbeat_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Run a single heartbeat cycle using the AgentLoop.

    Returns a dict with:
    - completed: bool
    - text: str (final agent text)
    - tool_calls_made: list
    - energy_spent: int
    - stopped_reason: str
    """
    # Build system prompt
    system_prompt = await build_heartbeat_system_prompt(registry)

    # Build the user message (heartbeat context snapshot)
    user_message = build_heartbeat_decision_prompt(context)

    # Load LLM config
    llm_config = await load_llm_config(conn, "llm.heartbeat")

    # Extract energy budget from context
    energy = context.get("energy", {})
    energy_budget = energy.get("current", 20)

    # Build agent loop config
    loop_config = AgentLoopConfig(
        tool_context=ToolContext.HEARTBEAT,
        system_prompt=system_prompt,
        llm_config=llm_config,
        registry=registry,
        pool=pool,
        energy_budget=energy_budget,
        max_iterations=None,  # Timeout-based
        timeout_seconds=120.0,
        temperature=0.7,
        max_tokens=2048,
        heartbeat_id=heartbeat_id,
    )

    agent = AgentLoop(loop_config)
    result = await agent.run(user_message)

    return {
        "completed": result.stopped_reason == "completed",
        "text": result.text,
        "tool_calls_made": result.tool_calls_made,
        "energy_spent": result.energy_spent,
        "iterations": result.iterations,
        "stopped_reason": result.stopped_reason,
        "timed_out": result.timed_out,
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
    """
    text = result.get("text", "")
    tool_calls = result.get("tool_calls_made", [])
    energy_spent = result.get("energy_spent", 0)
    stopped_reason = result.get("stopped_reason", "completed")

    # Build a summary of what happened
    tool_names = [tc.get("name", "?") for tc in tool_calls]
    summary = text or f"Heartbeat completed: {len(tool_calls)} tool calls, {energy_spent} energy spent."
    if tool_names:
        summary += f" Tools used: {', '.join(tool_names)}."

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
            }),
            "completed" if stopped_reason == "completed" else stopped_reason,
        )
    except Exception:
        memory_id = None
        logger.debug("Failed to record heartbeat memory", exc_info=True)

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
        logger.debug("Failed to update heartbeat state", exc_info=True)

    return {
        "completed": True,
        "memory_id": str(memory_id) if memory_id else None,
        "energy_spent": energy_spent,
        "outbox_messages": [],
    }
