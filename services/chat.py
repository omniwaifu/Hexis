from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from core.agent_api import db_dsn_from_env, get_agent_profile_context
from core.cognitive_memory_api import CognitiveMemory, MemoryType, format_context_for_prompt
from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
from core.llm import normalize_llm_config
from core.tools import create_default_registry, ToolContext, ToolExecutionContext, ToolRegistry
from services.prompt_resources import compose_personhood_prompt


BASE_SYSTEM_PROMPT = """You are an AI assistant with access to a persistent memory system and a range of tools for interacting with the world.

## Guidelines

- Be natural about using your tools - don't constantly announce that you're searching or executing
- If you don't find relevant memories, that's fine - just respond based on the current conversation
- When you learn new information about the user, it will be automatically remembered
- You can make multiple tool calls if needed to build a complete picture
- Treat memories as claims with provenance; prefer higher-trust and better-sourced memories when unsure

You are a helpful, knowledgeable assistant with the added capability of genuine memory and continuity."""


async def _build_system_prompt(
    agent_profile: dict[str, Any],
    registry: ToolRegistry | None = None,
) -> str:
    prompt = BASE_SYSTEM_PROMPT

    # Add dynamic tool descriptions if registry available
    if registry is not None:
        try:
            specs = await registry.get_specs(ToolContext.CHAT)
            if specs:
                tool_lines = []
                for spec in specs:
                    func = spec.get("function", {})
                    name = func.get("name", "")
                    desc = func.get("description", "")
                    tool_lines.append(f"- **{name}**: {desc}")
                prompt += "\n\n## Available Tools\n\n" + "\n".join(tool_lines)
        except Exception:
            pass  # Fall back to no tool descriptions

    try:
        prompt = (
            prompt
            + "\n\n----- PERSONHOOD MODULES (conversation grounding) -----\n\n"
            + compose_personhood_prompt("conversation")
        )
    except Exception:
        pass
    if agent_profile:
        prompt = prompt + "\n\n## Agent Profile\n" + json.dumps(agent_profile, indent=2)
    return prompt


def _estimate_importance(user_message: str, assistant_message: str) -> float:
    importance = 0.5
    combined = (user_message + "\n" + assistant_message).lower()
    learning_signals = [
        "remember",
        "don't forget",
        "important",
        "note that",
        "my name is",
        "i prefer",
        "i like",
        "i don't like",
        "always",
        "never",
        "make sure",
        "keep in mind",
    ]
    if len(user_message) > 200 or len(assistant_message) > 500:
        importance = max(importance, 0.7)
    if any(signal in combined for signal in learning_signals):
        importance = max(importance, 0.8)
    return max(0.15, min(float(importance), 1.0))


def _extract_allowed_tools(raw_tools: Any) -> list[str] | None:
    if raw_tools is None:
        return None
    if not isinstance(raw_tools, list):
        return None
    names: list[str] = []
    for item in raw_tools:
        if isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool")
            enabled = item.get("enabled", True)
            if isinstance(name, str) and name.strip() and enabled is not False:
                names.append(name.strip())
    return names


async def _remember_conversation(
    mem_client: CognitiveMemory,
    *,
    user_message: str,
    assistant_message: str,
) -> None:
    if not user_message and not assistant_message:
        return
    content = f"User: {user_message}\n\nAssistant: {assistant_message}"
    importance = _estimate_importance(user_message, assistant_message)
    source_attribution = {
        "kind": "conversation",
        "ref": "conversation_turn",
        "label": "conversation turn",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "trust": 0.95,
    }
    await mem_client.remember(
        content,
        type=MemoryType.EPISODIC,
        importance=importance,
        emotional_valence=0.0,
        context={"type": "conversation"},
        source_attribution=source_attribution,
        source_references=None,
        trust_level=0.95,
    )


async def _build_execution_context(
    registry: ToolRegistry,
    call_id: str,
    session_id: str | None = None,
) -> ToolExecutionContext:
    """Build a ToolExecutionContext with config overrides for chat."""
    ctx = ToolExecutionContext(
        tool_context=ToolContext.CHAT,
        call_id=call_id,
        session_id=session_id,
        allow_network=True,
        allow_shell=False,
        allow_file_write=False,
        allow_file_read=True,
    )
    try:
        config = await registry.get_config()
        overrides = config.get_context_overrides(ToolContext.CHAT)
        ctx.allow_shell = overrides.allow_shell
        ctx.allow_file_write = overrides.allow_file_write
        if config.workspace_path:
            ctx.workspace_path = config.workspace_path
    except Exception:
        pass  # Use defaults
    return ctx


async def chat_turn(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any],
    dsn: str | None = None,
    memory_limit: int = 10,
    max_tool_iterations: int = 5,
    session_id: str | None = None,
    pool: Any | None = None,
) -> dict[str, Any]:
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    history = history or []

    # Check if RLM is enabled for chat
    try:
        import asyncpg
        _conn = await asyncpg.connect(dsn)
        try:
            use_rlm_raw = await _conn.fetchval("SELECT get_config_bool('chat.use_rlm')")
            use_rlm = bool(use_rlm_raw)
        finally:
            await _conn.close()
    except Exception:
        use_rlm = False

    if use_rlm:
        from services.hexis_rlm import run_chat_turn
        result = await run_chat_turn(
            user_message=user_message,
            history=history,
            llm_config=normalized,
            dsn=dsn,
            session_id=session_id,
        )
        assistant_text = result["response"]
        # Still form memory from the turn
        async with CognitiveMemory.connect(dsn) as mem_client:
            await _remember_conversation(
                mem_client,
                user_message=user_message,
                assistant_message=assistant_text,
            )
        new_history = list(history)
        new_history.append({"role": "user", "content": user_message})
        new_history.append({"role": "assistant", "content": assistant_text})
        return {"assistant": assistant_text, "history": new_history}

    # Create or use provided pool for tool registry
    import asyncpg

    own_pool = pool is None
    if own_pool:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

    try:
        registry = create_default_registry(pool)

        agent_profile = await get_agent_profile_context(dsn)
        system_prompt = await _build_system_prompt(agent_profile, registry)

        async with CognitiveMemory.connect(dsn) as mem_client:
            context = await mem_client.hydrate(
                user_message,
                memory_limit=memory_limit,
                include_partial=True,
                include_identity=True,
                include_worldview=True,
                include_emotional_state=True,
                include_drives=True,
            )
            if context.memories:
                await mem_client.touch_memories([m.id for m in context.memories])

            memory_context = format_context_for_prompt(context)
            if memory_context:
                enriched_user_message = f"{memory_context}\n\n[USER MESSAGE]\n{user_message}"
            else:
                enriched_user_message = user_message

            loop_config = AgentLoopConfig(
                tool_context=ToolContext.CHAT,
                system_prompt=system_prompt,
                llm_config=normalized,
                registry=registry,
                pool=pool,
                energy_budget=None,
                max_iterations=max_tool_iterations + 1,
                timeout_seconds=120.0,
                temperature=0.7,
                max_tokens=1200,
                session_id=session_id,
            )
            agent = AgentLoop(loop_config)
            loop_result = await agent.run(enriched_user_message, history=history)
            assistant_text = loop_result.text

            await _remember_conversation(mem_client, user_message=user_message, assistant_message=assistant_text)

        new_history = list(history)
        new_history.append({"role": "user", "content": user_message})
        new_history.append({"role": "assistant", "content": assistant_text})
        return {"assistant": assistant_text, "history": new_history}
    finally:
        if own_pool:
            await pool.close()


async def stream_chat_turn(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any],
    dsn: str | None = None,
    memory_limit: int = 10,
    max_tool_iterations: int = 5,
    session_id: str | None = None,
    pool: Any | None = None,
) -> AsyncIterator[str]:
    """
    Streaming variant of chat_turn().

    Yields text chunks as they arrive from the AgentLoop. During tool-use
    cycles, text is emitted per-iteration (not token-by-token). Token-level
    streaming will be added in a future phase via stream_chat_completion.

    The caller receives the same enriched conversation flow (hydrate +
    tools + memory formation) — just delivered as a stream.
    """
    dsn = dsn or db_dsn_from_env()
    normalized = normalize_llm_config(llm_config)
    history = history or []

    # Create or use provided pool for tool registry
    import asyncpg

    own_pool = pool is None
    if own_pool:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

    try:
        registry = create_default_registry(pool)

        agent_profile = await get_agent_profile_context(dsn)
        system_prompt = await _build_system_prompt(agent_profile, registry)

        async with CognitiveMemory.connect(dsn) as mem_client:
            context = await mem_client.hydrate(
                user_message,
                memory_limit=memory_limit,
                include_partial=True,
                include_identity=True,
                include_worldview=True,
                include_emotional_state=True,
                include_drives=True,
            )
            if context.memories:
                await mem_client.touch_memories([m.id for m in context.memories])

            memory_context = format_context_for_prompt(context)
            if memory_context:
                enriched_user_message = f"{memory_context}\n\n[USER MESSAGE]\n{user_message}"
            else:
                enriched_user_message = user_message

            loop_config = AgentLoopConfig(
                tool_context=ToolContext.CHAT,
                system_prompt=system_prompt,
                llm_config=normalized,
                registry=registry,
                pool=pool,
                energy_budget=None,
                max_iterations=max_tool_iterations + 1,
                timeout_seconds=120.0,
                temperature=0.7,
                max_tokens=1200,
                session_id=session_id,
            )

            agent = AgentLoop(loop_config)
            collected: list[str] = []
            async for event in agent.stream(enriched_user_message, history=history):
                if event.event == AgentEvent.TEXT_DELTA:
                    text = event.data.get("text", "")
                    if text:
                        collected.append(text)
                        yield text

            full_text = collected[-1] if collected else ""
            await _remember_conversation(
                mem_client,
                user_message=user_message,
                assistant_message=full_text,
            )
    finally:
        if own_pool:
            await pool.close()


def chat_turn_sync(**kwargs: Any) -> dict[str, Any]:
    from core.sync_utils import run_sync

    return run_sync(chat_turn(**kwargs))
