"""
Hexis Unified Agent Loop

A single agentic loop shared by both chat and heartbeat contexts.
The LLM calls tools via the standard tool_use API, with results fed
back into the conversation for self-correction.

Differences between contexts are confined to:
- System prompt (chat vs heartbeat)
- Energy budget (None = unlimited for chat; int for heartbeat)
- Approval mechanism (callback for interactive; DB-based for autonomous)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from core.llm import chat_completion, stream_chat_completion
from core.tools.base import ToolContext, ToolExecutionContext

if TYPE_CHECKING:
    import asyncpg
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class AgentEvent(str, Enum):
    """Events emitted during the agent loop."""

    LOOP_START = "loop_start"
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    ENERGY_EXHAUSTED = "energy_exhausted"
    LOOP_END = "loop_end"
    ERROR = "error"


@dataclass
class AgentEventData:
    """Payload for an agent loop event."""

    event: AgentEvent
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopConfig:
    """Configuration for an agent loop run."""

    tool_context: ToolContext
    system_prompt: str
    llm_config: dict[str, Any]  # {provider, model, endpoint, api_key}
    registry: "ToolRegistry"
    pool: "asyncpg.Pool"

    # Energy budget — None means unlimited (chat mode)
    energy_budget: int | None = None

    # Limits
    max_iterations: int | None = None  # None = timeout-based only
    timeout_seconds: float = 300.0

    # LLM params
    temperature: float = 0.7
    max_tokens: int = 4096

    # Session
    session_id: str | None = None
    heartbeat_id: str | None = None

    # Callbacks
    on_event: Callable[[AgentEventData], Awaitable[None]] | None = None
    on_approval: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopResult:
    """Result of a completed agent loop run."""

    text: str
    messages: list[dict[str, Any]]
    tool_calls_made: list[dict[str, Any]]
    iterations: int
    energy_spent: int
    timed_out: bool = False
    stopped_reason: str = "completed"


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------


class AgentLoop:
    """
    Unified agentic loop for Hexis.

    Chat and heartbeat share the same loop. The only differences are the
    system prompt and energy budget, configured via AgentLoopConfig.

    Usage::

        config = AgentLoopConfig(
            tool_context=ToolContext.CHAT,
            system_prompt="...",
            llm_config=normalized,
            registry=registry,
            pool=pool,
        )
        agent = AgentLoop(config)
        result = await agent.run("Hello!")
    """

    def __init__(self, config: AgentLoopConfig) -> None:
        self.config = config
        self._energy_spent: int = 0
        self._iteration_count: int = 0
        self._tool_calls_made: list[dict[str, Any]] = []
        self._last_text: str = ""
        self._streaming: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AgentLoopResult:
        """Run the agent loop to completion."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.config.system_prompt},
        ]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_message})

        tools = await self.config.registry.get_specs(self.config.tool_context)

        await self._emit(AgentEvent.LOOP_START, {
            "tool_context": self.config.tool_context.value,
            "energy_budget": self.config.energy_budget,
            "tool_count": len(tools),
        })

        try:
            result = await asyncio.wait_for(
                self._loop(messages, tools),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = AgentLoopResult(
                text=self._last_text,
                messages=messages,
                tool_calls_made=self._tool_calls_made,
                iterations=self._iteration_count,
                energy_spent=self._energy_spent,
                timed_out=True,
                stopped_reason="timeout",
            )

        await self._emit(AgentEvent.LOOP_END, {
            "stopped_reason": result.stopped_reason,
            "iterations": result.iterations,
            "energy_spent": result.energy_spent,
            "timed_out": result.timed_out,
        })

        return result

    async def stream(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AgentEventData]:
        """
        Streaming variant of run().

        Yields AgentEventData as they happen. Callers can filter by
        event type (e.g. TEXT_DELTA for text streaming).
        """
        queue: asyncio.Queue[AgentEventData | None] = asyncio.Queue()
        original_on_event = self.config.on_event

        async def _enqueue(event: AgentEventData) -> None:
            await queue.put(event)
            if original_on_event:
                await original_on_event(event)

        self.config.on_event = _enqueue
        self._streaming = True

        # Run loop in background task
        task = asyncio.create_task(self.run(user_message, history))

        # Signal completion via sentinel
        def _on_done(_: asyncio.Task) -> None:  # type: ignore[type-arg]
            queue.put_nowait(None)

        task.add_done_callback(_on_done)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            # Restore original callback
            self.config.on_event = original_on_event
            # Ensure task exceptions propagate
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            elif task.exception():
                raise task.exception()  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentLoopResult:
        """Core agentic loop: LLM -> tool calls -> results -> LLM."""
        cfg = self.config
        llm = cfg.llm_config

        while True:
            # Check iteration limit
            if cfg.max_iterations is not None and self._iteration_count >= cfg.max_iterations:
                return self._make_result(messages, "max_iterations")

            # Check energy budget
            if cfg.energy_budget is not None and self._energy_spent >= cfg.energy_budget:
                await self._emit(AgentEvent.ENERGY_EXHAUSTED, {
                    "budget": cfg.energy_budget,
                    "spent": self._energy_spent,
                })
                return self._make_result(messages, "energy")

            self._iteration_count += 1

            # LLM call
            try:
                if self._streaming:
                    # Token-level streaming: emit TEXT_DELTA per token
                    async def _on_text_delta(token: str) -> None:
                        await self._emit(AgentEvent.TEXT_DELTA, {
                            "text": token,
                            "iteration": self._iteration_count,
                        })

                    response = await stream_chat_completion(
                        provider=llm["provider"],
                        model=llm["model"],
                        endpoint=llm.get("endpoint"),
                        api_key=llm.get("api_key"),
                        messages=messages,
                        tools=tools if tools else None,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                        on_text_delta=_on_text_delta,
                    )
                else:
                    response = await chat_completion(
                        provider=llm["provider"],
                        model=llm["model"],
                        endpoint=llm.get("endpoint"),
                        api_key=llm.get("api_key"),
                        messages=messages,
                        tools=tools if tools else None,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                    )
            except Exception as e:
                logger.error("LLM call failed at iteration %d: %s", self._iteration_count, e)
                await self._emit(AgentEvent.ERROR, {"error": str(e), "iteration": self._iteration_count})
                return self._make_result(messages, "error")

            text = response.get("content", "") or ""
            tool_calls = response.get("tool_calls") or []

            if text:
                self._last_text = text
                # Only emit per-iteration TEXT_DELTA in non-streaming mode
                # (streaming mode emits per-token via the callback)
                if not self._streaming:
                    await self._emit(AgentEvent.TEXT_DELTA, {"text": text, "iteration": self._iteration_count})

            # Build assistant message with tool_calls in OpenAI format
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    _to_openai_tool_call(tc) for tc in tool_calls
                ]
            messages.append(assistant_msg)

            if not tool_calls:
                return self._make_result(messages, "completed")

            # Process tool calls
            for call in tool_calls:
                tool_name = call.get("name", "")
                arguments = call.get("arguments", {})
                call_id = call.get("id") or str(uuid.uuid4())

                # Check approval via callback
                spec = cfg.registry.get_spec(tool_name)
                if spec and spec.requires_approval and cfg.on_approval:
                    await self._emit(AgentEvent.APPROVAL_REQUEST, {
                        "tool_name": tool_name,
                        "arguments": arguments,
                    })
                    try:
                        approved = await cfg.on_approval(tool_name, arguments)
                    except Exception:
                        approved = False

                    if not approved:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"Tool call '{tool_name}' was denied by the user.",
                        })
                        self._tool_calls_made.append({
                            "name": tool_name,
                            "arguments": arguments,
                            "success": False,
                            "denied": True,
                            "energy_spent": 0,
                        })
                        continue

                # Build execution context
                exec_ctx = await self._build_exec_context(call_id)

                await self._emit(AgentEvent.TOOL_START, {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "iteration": self._iteration_count,
                })

                # Execute tool via registry (policy + hooks + audit)
                result = await cfg.registry.execute(tool_name, arguments, exec_ctx)
                self._energy_spent += result.energy_spent

                await self._emit(AgentEvent.TOOL_RESULT, {
                    "tool_name": tool_name,
                    "success": result.success,
                    "energy_spent": result.energy_spent,
                    "total_energy_spent": self._energy_spent,
                    "duration": result.duration_seconds,
                    "error": result.error,
                })

                self._tool_calls_made.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "success": result.success,
                    "energy_spent": result.energy_spent,
                    "error": result.error,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.to_model_output(),
                })

        # Should not reach here, but safety net
        return self._make_result(messages, "completed")  # pragma: no cover

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_exec_context(self, call_id: str) -> ToolExecutionContext:
        """Build ToolExecutionContext with config overrides and remaining energy."""
        cfg = self.config
        remaining_energy: int | None = None
        if cfg.energy_budget is not None:
            remaining_energy = max(0, cfg.energy_budget - self._energy_spent)

        ctx = ToolExecutionContext(
            tool_context=cfg.tool_context,
            call_id=call_id,
            session_id=cfg.session_id,
            heartbeat_id=cfg.heartbeat_id,
            energy_available=remaining_energy,
            allow_network=True,
            allow_shell=False,
            allow_file_read=True,
            allow_file_write=False,
        )

        # Apply overrides from ToolsConfig
        try:
            tc = await cfg.registry.get_config()
            overrides = tc.get_context_overrides(cfg.tool_context)
            ctx.allow_shell = overrides.allow_shell
            ctx.allow_file_write = overrides.allow_file_write
            if tc.workspace_path:
                ctx.workspace_path = tc.workspace_path
        except Exception:
            pass

        return ctx

    async def _emit(self, event: AgentEvent, data: dict[str, Any] | None = None) -> None:
        """Emit an event via the configured callback."""
        if self.config.on_event:
            try:
                await self.config.on_event(AgentEventData(
                    event=event,
                    data=data or {},
                ))
            except Exception:
                logger.debug("Event callback failed for %s", event, exc_info=True)

    def _make_result(self, messages: list[dict[str, Any]], stopped_reason: str) -> AgentLoopResult:
        """Build an AgentLoopResult from current state."""
        return AgentLoopResult(
            text=self._last_text,
            messages=messages,
            tool_calls_made=self._tool_calls_made,
            iterations=self._iteration_count,
            energy_spent=self._energy_spent,
            timed_out=False,
            stopped_reason=stopped_reason,
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _to_openai_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Convert simplified tool call dict to OpenAI assistant message format."""
    arguments = call.get("arguments", {})
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    return {
        "id": call.get("id") or str(uuid.uuid4()),
        "type": "function",
        "function": {
            "name": call.get("name", ""),
            "arguments": arguments,
        },
    }
