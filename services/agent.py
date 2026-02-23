"""
Unified Agent Runner

Single entry point for both chat and heartbeat modes. Runs a subconscious
pre-phase before the conscious agent loop, injecting observations into the
system prompt. Mode differences are expressed entirely through configuration
(energy budget, planning, continuation, permissions).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TYPE_CHECKING

from core.agent_loop import AgentEvent, AgentEventData, AgentLoop, AgentLoopConfig
from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core.tools.base import ToolContext
from core.tools.config import ContextOverrides
from services.prompt_resources import (
    compose_personhood_prompt,
    load_conversation_prompt,
    load_heartbeat_agentic_prompt,
    load_heartbeat_task_mode_prompt,
    load_subconscious_prompt,
)

if TYPE_CHECKING:
    import asyncpg
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subconscious output
# ---------------------------------------------------------------------------


@dataclass
class SubconsciousOutput:
    """Structured output from the subconscious pre-phase."""

    salient_memories: list[dict[str, Any]] = field(default_factory=list)
    memory_expansions: list[dict[str, Any]] = field(default_factory=list)
    instincts: list[dict[str, Any]] = field(default_factory=list)
    emotional_state: dict[str, Any] = field(default_factory=dict)
    subconscious_response: str = ""

    # Observational patterns (passed through for completeness)
    narrative_observations: list[dict[str, Any]] = field(default_factory=list)
    relationship_observations: list[dict[str, Any]] = field(default_factory=list)
    contradiction_observations: list[dict[str, Any]] = field(default_factory=list)
    emotional_observations: list[dict[str, Any]] = field(default_factory=list)
    consolidation_observations: list[dict[str, Any]] = field(default_factory=list)


def _parse_subconscious_output(doc: dict[str, Any]) -> SubconsciousOutput:
    """Parse raw JSON from the subconscious LLM call into a structured output."""

    def _as_list(val: Any) -> list[dict[str, Any]]:
        if isinstance(val, list):
            return [v for v in val if isinstance(v, dict)]
        return []

    emotional = doc.get("emotional_observations")
    if emotional is None:
        emotional = doc.get("emotional_patterns")
    consolidation = doc.get("consolidation_observations")
    if consolidation is None:
        consolidation = doc.get("consolidation_suggestions")

    return SubconsciousOutput(
        salient_memories=_as_list(doc.get("salient_memories")),
        memory_expansions=_as_list(doc.get("memory_expansions")),
        instincts=_as_list(doc.get("instincts")),
        emotional_state=doc.get("emotional_state") if isinstance(doc.get("emotional_state"), dict) else {},
        subconscious_response=str(doc.get("subconscious_response") or ""),
        narrative_observations=_as_list(doc.get("narrative_observations")),
        relationship_observations=_as_list(doc.get("relationship_observations")),
        contradiction_observations=_as_list(doc.get("contradiction_observations")),
        emotional_observations=_as_list(emotional),
        consolidation_observations=_as_list(consolidation),
    )


def format_subconscious_signals(output: SubconsciousOutput) -> str:
    """Format subconscious output for injection into the user message context."""
    parts: list[str] = ["## Subconscious Signals"]

    if output.instincts:
        for inst in output.instincts[:3]:
            impulse = inst.get("impulse", "unknown")
            intensity = inst.get("intensity", 0)
            reason = inst.get("reason", "")
            parts.append(f"- Instinct: {impulse} ({intensity:.1f}) — {reason}")

    if output.emotional_state:
        es = output.emotional_state
        emotion = es.get("primary_emotion", "neutral")
        valence = es.get("valence", 0)
        arousal = es.get("arousal", 0)
        parts.append(f"- Emotional state: {emotion} (valence: {valence}, arousal: {arousal})")

    if output.memory_expansions:
        queries = [me.get("query", "") for me in output.memory_expansions[:3] if me.get("query")]
        if queries:
            parts.append(f"- Suggested memory searches: {', '.join(repr(q) for q in queries)}")

    if output.salient_memories:
        for sm in output.salient_memories[:3]:
            mid = sm.get("memory_id", "?")
            reason = sm.get("reason", "")
            parts.append(f"- Salient memory: [{mid}] ({reason})")

    if output.subconscious_response:
        parts.append(f"- Gut reaction: {output.subconscious_response[:200]}")

    # Only return content if we have actual signals beyond the header
    if len(parts) <= 1:
        return ""
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Subconscious pre-phase
# ---------------------------------------------------------------------------


async def run_subconscious_appraisal(
    conn: "asyncpg.Connection",
    user_message: str,
    memory_context: str,
    *,
    llm_config: dict[str, Any] | None = None,
) -> SubconsciousOutput:
    """
    Run the subconscious appraisal as a fast inline LLM call.

    This is a lightweight JSON-mode call that surfaces instincts, emotional
    reactions, salient memories, and memory expansion cues. It does NOT
    apply observations to the DB (that stays in the maintenance worker).
    """
    if llm_config is None:
        llm_config = await load_llm_config(conn, "llm.subconscious", fallback_key="llm.heartbeat")

    # Build context for the subconscious
    context_parts = []
    if user_message:
        context_parts.append(f"User message: {user_message}")
    if memory_context:
        context_parts.append(f"Relevant memories:\n{memory_context}")

    # Get emotional state from DB
    try:
        affect_raw = await conn.fetchval("SELECT get_current_affective_state()")
        if isinstance(affect_raw, str):
            affect = json.loads(affect_raw)
        elif isinstance(affect_raw, dict):
            affect = affect_raw
        else:
            affect = {}
        if affect:
            context_parts.append(f"Current emotional state: {json.dumps(affect)}")
    except Exception as e:
        logger.debug("Failed to fetch current affective state: %s", e)

    # Get active goals
    try:
        goals_raw = await conn.fetchval("SELECT get_active_goals()")
        if isinstance(goals_raw, str):
            goals = json.loads(goals_raw)
        elif goals_raw is not None:
            goals = goals_raw
        else:
            goals = []
        if goals:
            context_parts.append(f"Active goals: {json.dumps(goals)[:2000]}")
    except Exception as e:
        logger.debug("Failed to fetch active goals: %s", e)

    # Get dopamine/reward state — modulates instinct intensity and emotional response
    try:
        da_raw = await conn.fetchval("SELECT get_dopamine_state()")
        if isinstance(da_raw, str):
            da_state = json.loads(da_raw)
        elif isinstance(da_raw, dict):
            da_state = da_raw
        else:
            da_state = {}
        if da_state:
            tonic = da_state.get("tonic", 0.5)
            effective = da_state.get("effective", tonic)
            spike_trigger = da_state.get("spike_trigger")
            da_summary = f"Dopamine state: tonic={tonic:.2f}, effective={effective:.2f}"
            if spike_trigger:
                age = da_state.get("spike_age_seconds")
                age_str = f" ({int(age)}s ago)" if age is not None else ""
                da_summary += f", last spike: {spike_trigger}{age_str}"
            context_parts.append(da_summary)
    except Exception as e:
        logger.debug("Failed to fetch dopamine state: %s", e)

    user_prompt = f"Context (JSON):\n{json.dumps({'input': chr(10).join(context_parts)})[:12000]}"

    try:
        doc, _raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": load_subconscious_prompt().strip()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800,
            response_format={"type": "json_object"},
            fallback={},
        )
    except Exception as exc:
        logger.warning("Subconscious appraisal failed: %s", exc)
        return SubconsciousOutput()

    if not isinstance(doc, dict):
        return SubconsciousOutput()

    return _parse_subconscious_output(doc)


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


async def build_system_prompt(
    mode: Literal["chat", "heartbeat"],
    registry: "ToolRegistry | None",
    agent_profile: dict[str, Any] | None = None,
    *,
    subconscious_output: SubconsciousOutput | None = None,
    has_backlog_tasks: bool = False,
    is_group: bool = False,
    persona: dict[str, Any] | None = None,
) -> str:
    """Build the system prompt for either chat or heartbeat mode."""

    # Base prompt
    if mode == "chat":
        prompt = load_conversation_prompt().strip()
        if is_group:
            from services.prompt_resources import load_channel_context_prompt
            prompt += "\n\n" + load_channel_context_prompt().strip()
    else:
        prompt = load_heartbeat_agentic_prompt().strip()

    # Persona preamble — voice, description, personality from init_profile
    if persona:
        lines = ["## Persona"]
        for key in ("description", "voice", "personality", "purpose", "pronouns"):
            val = persona.get(key)
            if val:
                lines.append(f"- **{key}**: {val}")
        if len(lines) > 1:
            prompt += "\n\n" + "\n".join(lines)

    # Add dynamic tool descriptions
    tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT
    try:
        specs = await registry.get_specs(tool_context) if registry else []
        if specs:
            if mode == "chat":
                tool_lines = []
                for spec in specs:
                    func = spec.get("function", {})
                    name = func.get("name", "")
                    desc = func.get("description", "")
                    tool_lines.append(f"- **{name}**: {desc}")
                prompt += "\n\n## Available Tools\n\n" + "\n".join(tool_lines)
            else:
                tool_names = sorted(s["function"]["name"] for s in specs)
                prompt += (
                    "\n\n## Available Tools\n"
                    + ", ".join(tool_names)
                    + "\n\nUse these tools via tool_use to take actions. "
                    "Each tool has its own parameters — the LLM API will show you the schemas."
                )
    except Exception:
        logger.debug("Failed to get tool specs for prompt", exc_info=True)

    # Heartbeat-specific: task mode guidance
    if mode == "heartbeat" and has_backlog_tasks:
        task_mode_prompt = load_heartbeat_task_mode_prompt().strip()
        prompt += "\n\n" + task_mode_prompt

    # Personhood modules
    personhood_kind = "group" if (mode == "chat" and is_group) else ("conversation" if mode == "chat" else "heartbeat")
    try:
        personhood = compose_personhood_prompt(personhood_kind)
        if personhood:
            prompt += (
                "\n\n----- PERSONHOOD MODULES (for grounding) -----\n\n"
                + personhood
            )
    except Exception:
        logger.debug("Failed to compose personhood prompt", exc_info=True)

    # Agent profile
    if agent_profile:
        prompt += "\n\n## Agent Profile\n" + json.dumps(agent_profile, separators=(", ", ": "))

    return prompt


# ---------------------------------------------------------------------------
# Helper: read persona from config
# ---------------------------------------------------------------------------


async def _read_persona(conn: "asyncpg.Connection") -> dict[str, Any] | None:
    """
    Load agent persona from config.

    Reads agent.init_profile, extracts agent name (sets it globally),
    and returns persona dict with keys: description, voice, personality, purpose, pronouns.
    """
    _persona: dict[str, Any] | None = None
    try:
        _raw = await conn.fetchval("SELECT value FROM config WHERE key = 'agent.init_profile'")
        if _raw:
            _ip = json.loads(_raw) if isinstance(_raw, str) else _raw
            _agent = _ip.get("agent") or {}
            _aname = _agent.get("name") or _ip.get("name")
            if _aname:
                from services.prompt_resources import set_agent_name
                set_agent_name(_aname)
            _persona = {k: _agent[k] for k in ("description", "voice", "personality", "purpose", "pronouns") if _agent.get(k)}
    except Exception:
        pass
    return _persona


# ---------------------------------------------------------------------------
# Unified run_agent
# ---------------------------------------------------------------------------


async def run_agent(
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    *,
    user_message: str,
    mode: Literal["chat", "heartbeat"],
    energy_budget: int | None = None,
    tool_context: ToolContext | None = None,
    history: list[dict[str, Any]] | None = None,
    heartbeat_id: str | None = None,
    session_id: str | None = None,
    heartbeat_context: dict[str, Any] | None = None,
    on_event: Callable[[AgentEventData], Awaitable[None]] | None = None,
    streaming: bool = False,
    context_overrides: ContextOverrides | None = None,
    agent_profile: dict[str, Any] | None = None,
    is_group: bool = False,
    dsn: str | None = None,
    has_backlog_tasks: bool = False,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
    max_iterations: int | None = None,
) -> "AgentLoopResult":
    """
    Unified entry point for both chat and heartbeat agent invocations.

    Mode differences are expressed through configuration only:
    - Chat: unlimited budget, timeout-based, no planning
    - Heartbeat: finite budget, planning enabled, continuation nudges
    """
    from core.agent_api import db_dsn_from_env
    from core.agent_loop import AgentLoopResult
    from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt

    dsn = dsn or db_dsn_from_env()
    history = history or []

    if tool_context is None:
        tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT

    # 1. Load LLM configs
    async with pool.acquire() as conn:
        llm_key = "llm.chat" if mode == "chat" else "llm.heartbeat"
        llm_fallback = "llm" if mode == "chat" else None
        llm_config = await load_llm_config(conn, llm_key, fallback_key=llm_fallback)

        # Inject agent name into prompts + capture persona for system prompt
        _persona = await _read_persona(conn)

        # 2. Hydrate memory context (chat mode - heartbeat builds its own context)
        memory_context = ""
        if mode == "chat":
            try:
                mem_client = CognitiveMemory(pool)
                context = await mem_client.hydrate(
                    user_message,
                    memory_limit=10,
                    include_partial=True,
                    include_identity=True,
                    include_worldview=True,
                    include_emotional_state=True,
                    include_goals=True,
                    include_drives=True,
                )
                if context.memories:
                    await mem_client.touch_memories([m.id for m in context.memories])
                memory_context = format_context_for_prompt(context, max_memories=10)

                # Emit memory recall event
                if on_event and context.memories:
                    await on_event(AgentEventData(
                        event=AgentEvent.PHASE_CHANGE,
                        data={"phase": "memory_recall", "count": len(context.memories)},
                    ))
            except Exception as exc:
                logger.warning("Memory hydration failed: %s", exc)

        # 3. Run subconscious pre-phase
        subconscious_output = SubconsciousOutput()
        try:
            if on_event:
                await on_event(AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={"phase": "subconscious", "status": "start"},
                ))

            # For heartbeat, use the heartbeat context as memory context
            sub_memory_ctx = memory_context
            if mode == "heartbeat" and heartbeat_context:
                from services.heartbeat_prompt import build_heartbeat_decision_prompt
                sub_memory_ctx = build_heartbeat_decision_prompt(heartbeat_context)

            subconscious_output = await run_subconscious_appraisal(
                conn, user_message, sub_memory_ctx,
            )

            if on_event:
                await on_event(AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={"phase": "subconscious", "status": "end"},
                ))
        except Exception as exc:
            logger.warning("Subconscious pre-phase failed: %s", exc)

    # 4. Build system prompt
    system_prompt = await build_system_prompt(
        mode,
        registry,
        agent_profile,
        subconscious_output=subconscious_output,
        has_backlog_tasks=has_backlog_tasks,
        is_group=is_group,
        persona=_persona,
    )

    # 5. Build enriched user message
    enriched_parts: list[str] = []

    # Add subconscious signals
    sub_signals = format_subconscious_signals(subconscious_output)
    if sub_signals:
        enriched_parts.append(sub_signals)

    # Add memory context (chat mode)
    if memory_context:
        enriched_parts.append(memory_context)

    # Add the actual user message
    if mode == "chat":
        enriched_parts.append(f"[USER MESSAGE]\n{user_message}")
    else:
        enriched_parts.append(user_message)

    enriched_user_message = "\n\n".join(enriched_parts) if enriched_parts else user_message

    # 6. Configure AgentLoop with mode-specific defaults
    if mode == "chat":
        effective_timeout = timeout_seconds or 120.0
        effective_max_tokens = max_tokens or 4096
        loop_config = AgentLoopConfig(
            tool_context=tool_context,
            system_prompt=system_prompt,
            llm_config=llm_config,
            registry=registry,
            pool=pool,
            energy_budget=energy_budget,  # None = unlimited for chat
            max_iterations=max_iterations,  # None = timeout-based only
            timeout_seconds=effective_timeout,
            temperature=0.7,
            max_tokens=effective_max_tokens,
            session_id=session_id,
        )
    else:
        effective_timeout = timeout_seconds or (300.0 if has_backlog_tasks else 120.0)
        effective_max_tokens = max_tokens or (4096 if has_backlog_tasks else 2048)
        loop_config = AgentLoopConfig(
            tool_context=tool_context,
            system_prompt=system_prompt,
            llm_config=llm_config,
            registry=registry,
            pool=pool,
            energy_budget=energy_budget or 20,
            max_iterations=None,  # timeout-based
            timeout_seconds=effective_timeout,
            temperature=0.7,
            max_tokens=effective_max_tokens,
            heartbeat_id=heartbeat_id,
            enable_planning=True,
            continuation_prompt=(
                "You finished without taking further action. "
                "Review your output — is there anything you should recall, "
                "record, or act on before finishing?"
            ),
            max_continuations=2 if has_backlog_tasks else 1,
            context_overrides=context_overrides,
        )

    # 7. Run agent loop
    agent = AgentLoop(loop_config)
    if on_event:
        loop_config.on_event = on_event

    if streaming:
        # For streaming, we need to collect results while yielding events.
        # The caller should use stream_agent() instead.
        # Fall back to non-streaming here.
        pass

    result = await agent.run(enriched_user_message, history=history)
    return result


async def stream_agent(
    pool: "asyncpg.Pool",
    registry: "ToolRegistry",
    *,
    user_message: str,
    mode: Literal["chat", "heartbeat"] = "chat",
    energy_budget: int | None = None,
    tool_context: ToolContext | None = None,
    history: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    agent_profile: dict[str, Any] | None = None,
    is_group: bool = False,
    dsn: str | None = None,
    has_backlog_tasks: bool = False,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[AgentEventData]:
    """
    Streaming variant of run_agent(). Yields AgentEventData as they happen.

    Used by the SSE chat endpoint to stream tokens to the frontend.
    """
    from core.agent_api import db_dsn_from_env
    from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt

    dsn = dsn or db_dsn_from_env()
    history = history or []

    if tool_context is None:
        tool_context = ToolContext.CHAT if mode == "chat" else ToolContext.HEARTBEAT

    # 1. Load LLM config and run pre-phases
    async with pool.acquire() as conn:
        llm_key = "llm.chat" if mode == "chat" else "llm.heartbeat"
        llm_fallback = "llm" if mode == "chat" else None
        llm_config = await load_llm_config(conn, llm_key, fallback_key=llm_fallback)

        # Inject agent name into prompts + capture persona for system prompt
        _persona = await _read_persona(conn)

        # Hydrate memory
        memory_context = ""
        if mode == "chat":
            try:
                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={"phase": "memory_recall", "status": "start"},
                )
                mem_client = CognitiveMemory(pool)
                context = await mem_client.hydrate(
                    user_message,
                    memory_limit=10,
                    include_partial=True,
                    include_identity=True,
                    include_worldview=True,
                    include_emotional_state=True,
                    include_goals=True,
                    include_drives=True,
                )
                if context.memories:
                    await mem_client.touch_memories([m.id for m in context.memories])
                memory_context = format_context_for_prompt(context, max_memories=10)

                yield AgentEventData(
                    event=AgentEvent.PHASE_CHANGE,
                    data={
                        "phase": "memory_recall",
                        "status": "end",
                        "count": len(context.memories),
                    },
                )
            except Exception as exc:
                logger.warning("Memory hydration failed: %s", exc)

        # Run subconscious
        subconscious_output = SubconsciousOutput()
        try:
            yield AgentEventData(
                event=AgentEvent.PHASE_CHANGE,
                data={"phase": "subconscious", "status": "start"},
            )
            subconscious_output = await run_subconscious_appraisal(
                conn, user_message, memory_context,
            )
            yield AgentEventData(
                event=AgentEvent.PHASE_CHANGE,
                data={"phase": "subconscious", "status": "end"},
            )
        except Exception as exc:
            logger.warning("Subconscious pre-phase failed: %s", exc)

    # Build system prompt
    system_prompt = await build_system_prompt(
        mode,
        registry,
        agent_profile,
        subconscious_output=subconscious_output,
        has_backlog_tasks=has_backlog_tasks,
        is_group=is_group,
        persona=_persona,
    )

    # Build enriched user message
    enriched_parts: list[str] = []
    sub_signals = format_subconscious_signals(subconscious_output)
    if sub_signals:
        enriched_parts.append(sub_signals)
    if memory_context:
        enriched_parts.append(memory_context)
    enriched_parts.append(f"[USER MESSAGE]\n{user_message}")
    enriched_user_message = "\n\n".join(enriched_parts)

    # Configure loop
    effective_timeout = timeout_seconds or 120.0
    effective_max_tokens = max_tokens or 4096
    loop_config = AgentLoopConfig(
        tool_context=tool_context,
        system_prompt=system_prompt,
        llm_config=llm_config,
        registry=registry,
        pool=pool,
        energy_budget=energy_budget,
        max_iterations=None,
        timeout_seconds=effective_timeout,
        temperature=0.7,
        max_tokens=effective_max_tokens,
        session_id=session_id,
    )

    agent = AgentLoop(loop_config)
    async for event in agent.stream(enriched_user_message, history=history):
        yield event
