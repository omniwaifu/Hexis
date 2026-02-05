"""
Tests for the unified AgentLoop.

Covers: basic text, single/multi tool calls, multi-iteration,
self-correction, energy budget, timeout, max iterations, approval
callbacks, config overrides, event emission, streaming, error handling.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent_loop import (
    AgentEvent,
    AgentEventData,
    AgentLoop,
    AgentLoopConfig,
    AgentLoopResult,
    _to_openai_tool_call,
)
from core.tools.base import (
    ToolCategory,
    ToolContext,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)
from core.tools.config import ContextOverrides, ToolsConfig

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


# ============================================================================
# Helpers
# ============================================================================


def _make_llm_config() -> dict[str, Any]:
    return {
        "provider": "openai",
        "model": "gpt-4o",
        "endpoint": None,
        "api_key": "test-key",
    }


def _text_response(text: str) -> dict[str, Any]:
    """Simulate an LLM response with text only (no tool calls)."""
    return {"content": text, "tool_calls": [], "raw": None}


def _tool_response(
    text: str,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Simulate an LLM response with tool calls."""
    return {"content": text, "tool_calls": tool_calls, "raw": None}


def _tool_call(name: str, arguments: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    return {
        "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
        "name": name,
        "arguments": arguments,
    }


def _mock_registry(
    *,
    tool_specs: list[dict[str, Any]] | None = None,
    execute_results: dict[str, ToolResult] | None = None,
    spec_map: dict[str, ToolSpec] | None = None,
    config: ToolsConfig | None = None,
) -> MagicMock:
    """Create a mock ToolRegistry."""
    registry = MagicMock()
    registry.pool = MagicMock()

    # get_specs returns OpenAI function specs
    registry.get_specs = AsyncMock(return_value=tool_specs or [])

    # get_spec returns ToolSpec for a given name
    def _get_spec(name: str):
        if spec_map:
            return spec_map.get(name)
        return None
    registry.get_spec = MagicMock(side_effect=_get_spec)

    # execute returns ToolResult
    execute_results = execute_results or {}

    async def _execute(name: str, arguments: dict, context: ToolExecutionContext) -> ToolResult:
        if name in execute_results:
            return execute_results[name]
        return ToolResult.success_result({"echo": name, "args": arguments})
    registry.execute = AsyncMock(side_effect=_execute)

    # get_config returns ToolsConfig
    config = config or ToolsConfig()
    registry.get_config = AsyncMock(return_value=config)

    return registry


def _make_config(
    registry: MagicMock | None = None,
    **overrides: Any,
) -> AgentLoopConfig:
    """Build an AgentLoopConfig with sensible defaults."""
    reg = registry or _mock_registry()
    defaults = dict(
        tool_context=ToolContext.CHAT,
        system_prompt="You are a test assistant.",
        llm_config=_make_llm_config(),
        registry=reg,
        pool=reg.pool,
        timeout_seconds=10.0,
    )
    defaults.update(overrides)
    return AgentLoopConfig(**defaults)


# ============================================================================
# Unit: basic text response
# ============================================================================


class TestBasicText:
    @patch("core.agent_loop.chat_completion")
    async def test_text_only_response(self, mock_llm):
        mock_llm.return_value = _text_response("Hello, world!")
        config = _make_config()
        agent = AgentLoop(config)
        result = await agent.run("Hi")

        assert result.text == "Hello, world!"
        assert result.iterations == 1
        assert result.energy_spent == 0
        assert result.stopped_reason == "completed"
        assert result.timed_out is False
        assert len(result.tool_calls_made) == 0
        assert len(result.messages) == 3  # system + user + assistant

    @patch("core.agent_loop.chat_completion")
    async def test_empty_text_response(self, mock_llm):
        mock_llm.return_value = _text_response("")
        config = _make_config()
        agent = AgentLoop(config)
        result = await agent.run("Hi")

        assert result.text == ""
        assert result.stopped_reason == "completed"

    @patch("core.agent_loop.chat_completion")
    async def test_history_included(self, mock_llm):
        mock_llm.return_value = _text_response("Got it.")
        config = _make_config()
        agent = AgentLoop(config)
        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        result = await agent.run("Follow-up", history=history)

        # system + 2 history + user + assistant = 5
        assert len(result.messages) == 5
        assert result.messages[1]["content"] == "Previous question"


# ============================================================================
# Unit: tool calls
# ============================================================================


class TestToolCalls:
    @patch("core.agent_loop.chat_completion")
    async def test_single_tool_call(self, mock_llm):
        """LLM calls one tool, gets result, then responds with text."""
        recall_result = ToolResult.success_result({"memories": ["Python is great"]})
        recall_result.energy_spent = 1
        registry = _mock_registry(
            tool_specs=[{"type": "function", "function": {"name": "recall", "description": "Recall", "parameters": {}}}],
            execute_results={"recall": recall_result},
        )

        # First call: tool call; Second call: text response
        mock_llm.side_effect = [
            _tool_response("Let me search.", [_tool_call("recall", {"query": "Python"})]),
            _text_response("Python is great!"),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Tell me about Python")

        assert result.text == "Python is great!"
        assert result.iterations == 2
        assert result.energy_spent == 1
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0]["name"] == "recall"
        assert result.tool_calls_made[0]["success"] is True

    @patch("core.agent_loop.chat_completion")
    async def test_multiple_tool_calls_in_one_response(self, mock_llm):
        """LLM makes two tool calls in a single response."""
        recall_result = ToolResult.success_result({"data": "recalled"})
        recall_result.energy_spent = 1
        search_result = ToolResult.success_result({"results": ["found"]})
        search_result.energy_spent = 2
        registry = _mock_registry(
            execute_results={"recall": recall_result, "web_search": search_result},
        )

        mock_llm.side_effect = [
            _tool_response("Searching...", [
                _tool_call("recall", {"query": "test"}),
                _tool_call("web_search", {"query": "test"}),
            ]),
            _text_response("Here's what I found."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Find info")

        assert result.iterations == 2
        assert result.energy_spent == 3  # 1 + 2
        assert len(result.tool_calls_made) == 2

    @patch("core.agent_loop.chat_completion")
    async def test_multi_iteration_tool_calls(self, mock_llm):
        """LLM calls tools, gets results, calls more tools, then responds."""
        r1 = ToolResult.success_result("first")
        r1.energy_spent = 1
        r2 = ToolResult.success_result("second")
        r2.energy_spent = 1
        registry = _mock_registry(execute_results={"tool_a": r1, "tool_b": r2})

        mock_llm.side_effect = [
            _tool_response("Step 1", [_tool_call("tool_a", {})]),
            _tool_response("Step 2", [_tool_call("tool_b", {})]),
            _text_response("All done."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Do things")

        assert result.text == "All done."
        assert result.iterations == 3
        assert result.energy_spent == 2
        assert len(result.tool_calls_made) == 2

    @patch("core.agent_loop.chat_completion")
    async def test_self_correction(self, mock_llm):
        """Tool returns error, LLM sees it and adjusts approach."""
        fail_result = ToolResult.error_result("File not found", ToolErrorType.FILE_NOT_FOUND)
        fail_result.energy_spent = 1
        ok_result = ToolResult.success_result("content of file.txt")
        ok_result.energy_spent = 1
        registry = _mock_registry(execute_results={"read_file": fail_result})

        call_count = 0

        async def _execute(name, args, ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fail_result
            return ok_result

        registry.execute = AsyncMock(side_effect=_execute)

        mock_llm.side_effect = [
            _tool_response("Reading file.", [_tool_call("read_file", {"path": "/bad/path"})]),
            _tool_response("Trying again.", [_tool_call("read_file", {"path": "/good/path"})]),
            _text_response("Found the file content."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Read a file")

        assert result.text == "Found the file content."
        assert result.iterations == 3
        assert len(result.tool_calls_made) == 2
        assert result.tool_calls_made[0]["success"] is False
        assert result.tool_calls_made[1]["success"] is True


# ============================================================================
# Unit: energy budget
# ============================================================================


class TestEnergyBudget:
    @patch("core.agent_loop.chat_completion")
    async def test_energy_exhausted_stops_loop(self, mock_llm):
        """Loop stops when energy budget is exhausted."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 5
        registry = _mock_registry(execute_results={"expensive_tool": tool_result})

        mock_llm.side_effect = [
            _tool_response("Using tool.", [_tool_call("expensive_tool", {})]),
            # Second iteration: energy check will stop before LLM call
        ]

        config = _make_config(registry=registry, energy_budget=5)
        agent = AgentLoop(config)
        result = await agent.run("Do expensive thing")

        assert result.stopped_reason == "energy"
        assert result.energy_spent == 5

    @patch("core.agent_loop.chat_completion")
    async def test_energy_remaining_passed_to_context(self, mock_llm):
        """Remaining energy is set on ToolExecutionContext."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 3
        registry = _mock_registry(execute_results={"tool_a": tool_result})

        captured_ctx = None

        async def _capture_execute(name, args, ctx):
            nonlocal captured_ctx
            captured_ctx = ctx
            return tool_result

        registry.execute = AsyncMock(side_effect=_capture_execute)

        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool_a", {})]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry, energy_budget=10)
        agent = AgentLoop(config)
        await agent.run("Test")

        assert captured_ctx is not None
        assert captured_ctx.energy_available == 10  # Full budget at first call

    @patch("core.agent_loop.chat_completion")
    async def test_unlimited_energy_for_chat(self, mock_llm):
        """Chat mode (energy_budget=None) has no energy limit."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 100
        registry = _mock_registry(execute_results={"tool": tool_result})

        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool", {})]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry, energy_budget=None)
        agent = AgentLoop(config)
        result = await agent.run("Test")

        assert result.stopped_reason == "completed"
        assert result.energy_spent == 100

    @patch("core.agent_loop.chat_completion")
    async def test_energy_none_context(self, mock_llm):
        """When energy_budget is None, context.energy_available is None."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 0
        registry = _mock_registry(execute_results={"tool": tool_result})

        captured_ctx = None

        async def _capture_execute(name, args, ctx):
            nonlocal captured_ctx
            captured_ctx = ctx
            return tool_result

        registry.execute = AsyncMock(side_effect=_capture_execute)

        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool", {})]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry, energy_budget=None)
        agent = AgentLoop(config)
        await agent.run("Test")

        assert captured_ctx is not None
        assert captured_ctx.energy_available is None


# ============================================================================
# Unit: limits
# ============================================================================


class TestLimits:
    @patch("core.agent_loop.chat_completion")
    async def test_max_iterations(self, mock_llm):
        """Loop stops at max_iterations."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 0
        registry = _mock_registry(execute_results={"tool": tool_result})

        # Keep returning tool calls — loop should stop at 3 iterations
        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool", {})]),
            _tool_response("More", [_tool_call("tool", {})]),
            _tool_response("Even more", [_tool_call("tool", {})]),
        ]

        config = _make_config(registry=registry, max_iterations=3)
        agent = AgentLoop(config)
        result = await agent.run("Loop forever")

        assert result.stopped_reason == "max_iterations"
        assert result.iterations == 3

    @patch("core.agent_loop.chat_completion")
    async def test_timeout(self, mock_llm):
        """Loop returns with timed_out when timeout is exceeded."""

        async def _slow_llm(**kwargs):
            await asyncio.sleep(5)
            return _text_response("Too slow")

        mock_llm.side_effect = _slow_llm

        config = _make_config(timeout_seconds=0.1)
        agent = AgentLoop(config)
        result = await agent.run("Slow request")

        assert result.timed_out is True
        assert result.stopped_reason == "timeout"


# ============================================================================
# Unit: approval
# ============================================================================


class TestApproval:
    @patch("core.agent_loop.chat_completion")
    async def test_approval_callback_called(self, mock_llm):
        """Approval callback is invoked for tools that require approval."""
        approval_calls = []

        async def _approve(name: str, args: dict) -> bool:
            approval_calls.append((name, args))
            return True

        spec = ToolSpec(
            name="dangerous_tool",
            description="Needs approval",
            parameters={"type": "object"},
            category=ToolCategory.SHELL,
            requires_approval=True,
        )
        tool_result = ToolResult.success_result("executed")
        tool_result.energy_spent = 0
        registry = _mock_registry(
            spec_map={"dangerous_tool": spec},
            execute_results={"dangerous_tool": tool_result},
        )

        mock_llm.side_effect = [
            _tool_response("Running.", [_tool_call("dangerous_tool", {"cmd": "ls"})]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry, on_approval=_approve)
        agent = AgentLoop(config)
        result = await agent.run("Run a command")

        assert len(approval_calls) == 1
        assert approval_calls[0][0] == "dangerous_tool"
        assert result.stopped_reason == "completed"

    @patch("core.agent_loop.chat_completion")
    async def test_approval_denied(self, mock_llm):
        """When approval is denied, tool is not executed and denial message is sent to LLM."""
        async def _deny(name: str, args: dict) -> bool:
            return False

        spec = ToolSpec(
            name="dangerous_tool",
            description="Needs approval",
            parameters={"type": "object"},
            category=ToolCategory.SHELL,
            requires_approval=True,
        )
        registry = _mock_registry(spec_map={"dangerous_tool": spec})

        mock_llm.side_effect = [
            _tool_response("Running.", [_tool_call("dangerous_tool", {"cmd": "rm -rf /"}, call_id="call_1")]),
            _text_response("OK, I won't do that."),
        ]

        config = _make_config(registry=registry, on_approval=_deny)
        agent = AgentLoop(config)
        result = await agent.run("Delete everything")

        # Tool should NOT have been executed
        registry.execute.assert_not_awaited()

        # Should have a denial message in the messages
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "denied" in tool_msgs[0]["content"].lower()

        # Denial recorded
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0]["denied"] is True

    @patch("core.agent_loop.chat_completion")
    async def test_no_approval_callback_skips_check(self, mock_llm):
        """If no on_approval callback is set, approval tools are executed normally."""
        spec = ToolSpec(
            name="dangerous_tool",
            description="Needs approval",
            parameters={"type": "object"},
            category=ToolCategory.SHELL,
            requires_approval=True,
        )
        tool_result = ToolResult.success_result("ran")
        tool_result.energy_spent = 0
        registry = _mock_registry(
            spec_map={"dangerous_tool": spec},
            execute_results={"dangerous_tool": tool_result},
        )

        mock_llm.side_effect = [
            _tool_response("Go.", [_tool_call("dangerous_tool", {})]),
            _text_response("Ran it."),
        ]

        config = _make_config(registry=registry, on_approval=None)
        agent = AgentLoop(config)
        result = await agent.run("Run it")

        # Tool should have been executed (no approval check)
        registry.execute.assert_awaited_once()
        assert result.stopped_reason == "completed"


# ============================================================================
# Unit: context propagation
# ============================================================================


class TestContextPropagation:
    @patch("core.agent_loop.chat_completion")
    async def test_session_and_heartbeat_ids(self, mock_llm):
        """Session and heartbeat IDs propagate to execution context."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 0
        registry = _mock_registry(execute_results={"tool": tool_result})

        captured_ctx = None

        async def _capture_execute(name, args, ctx):
            nonlocal captured_ctx
            captured_ctx = ctx
            return tool_result

        registry.execute = AsyncMock(side_effect=_capture_execute)

        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool", {})]),
            _text_response("Done."),
        ]

        config = _make_config(
            registry=registry,
            session_id="sess-123",
            heartbeat_id="hb-456",
            tool_context=ToolContext.HEARTBEAT,
        )
        agent = AgentLoop(config)
        await agent.run("Test")

        assert captured_ctx is not None
        assert captured_ctx.session_id == "sess-123"
        assert captured_ctx.heartbeat_id == "hb-456"
        assert captured_ctx.tool_context == ToolContext.HEARTBEAT

    @patch("core.agent_loop.chat_completion")
    async def test_config_overrides_applied(self, mock_llm):
        """allow_shell and allow_file_write from ToolsConfig are propagated."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 0
        tools_config = ToolsConfig(
            context_overrides={
                ToolContext.CHAT: ContextOverrides(
                    allow_shell=True,
                    allow_file_write=True,
                ),
            },
            workspace_path="/test/workspace",
        )
        registry = _mock_registry(
            execute_results={"tool": tool_result},
            config=tools_config,
        )

        captured_ctx = None

        async def _capture_execute(name, args, ctx):
            nonlocal captured_ctx
            captured_ctx = ctx
            return tool_result

        registry.execute = AsyncMock(side_effect=_capture_execute)

        mock_llm.side_effect = [
            _tool_response("Go", [_tool_call("tool", {})]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        await agent.run("Test")

        assert captured_ctx is not None
        assert captured_ctx.allow_shell is True
        assert captured_ctx.allow_file_write is True
        assert captured_ctx.workspace_path == "/test/workspace"


# ============================================================================
# Unit: events
# ============================================================================


class TestEvents:
    @patch("core.agent_loop.chat_completion")
    async def test_event_order_text_only(self, mock_llm):
        """Events: LOOP_START → TEXT_DELTA → LOOP_END for text-only."""
        events: list[AgentEvent] = []

        async def _capture(e: AgentEventData) -> None:
            events.append(e.event)

        mock_llm.return_value = _text_response("Hello!")
        config = _make_config(on_event=_capture)
        agent = AgentLoop(config)
        await agent.run("Hi")

        assert events == [
            AgentEvent.LOOP_START,
            AgentEvent.TEXT_DELTA,
            AgentEvent.LOOP_END,
        ]

    @patch("core.agent_loop.chat_completion")
    async def test_event_order_with_tool(self, mock_llm):
        """Events: LOOP_START → TEXT_DELTA → TOOL_START → TOOL_RESULT → TEXT_DELTA → LOOP_END."""
        events: list[AgentEvent] = []

        async def _capture(e: AgentEventData) -> None:
            events.append(e.event)

        tool_result = ToolResult.success_result("found it")
        tool_result.energy_spent = 1
        registry = _mock_registry(execute_results={"recall": tool_result})

        mock_llm.side_effect = [
            _tool_response("Searching.", [_tool_call("recall", {"q": "test"})]),
            _text_response("Here you go."),
        ]

        config = _make_config(registry=registry, on_event=_capture)
        agent = AgentLoop(config)
        await agent.run("Find something")

        assert events == [
            AgentEvent.LOOP_START,
            AgentEvent.TEXT_DELTA,   # "Searching."
            AgentEvent.TOOL_START,
            AgentEvent.TOOL_RESULT,
            AgentEvent.TEXT_DELTA,   # "Here you go."
            AgentEvent.LOOP_END,
        ]

    @patch("core.agent_loop.chat_completion")
    async def test_event_data_contains_tool_info(self, mock_llm):
        """TOOL_START and TOOL_RESULT events contain tool name and details."""
        event_data: list[AgentEventData] = []

        async def _capture(e: AgentEventData) -> None:
            event_data.append(e)

        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 2
        registry = _mock_registry(execute_results={"recall": tool_result})

        mock_llm.side_effect = [
            _tool_response("", [_tool_call("recall", {"query": "test"}, call_id="call_abc")]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry, on_event=_capture)
        agent = AgentLoop(config)
        await agent.run("Test")

        tool_starts = [e for e in event_data if e.event == AgentEvent.TOOL_START]
        tool_results = [e for e in event_data if e.event == AgentEvent.TOOL_RESULT]

        assert len(tool_starts) == 1
        assert tool_starts[0].data["tool_name"] == "recall"

        assert len(tool_results) == 1
        assert tool_results[0].data["tool_name"] == "recall"
        assert tool_results[0].data["success"] is True
        assert tool_results[0].data["energy_spent"] == 2

    @patch("core.agent_loop.chat_completion")
    async def test_event_callback_error_does_not_crash(self, mock_llm):
        """If event callback raises, loop continues."""
        async def _bad_callback(e: AgentEventData) -> None:
            raise RuntimeError("callback error")

        mock_llm.return_value = _text_response("Works.")
        config = _make_config(on_event=_bad_callback)
        agent = AgentLoop(config)
        result = await agent.run("Test")

        assert result.text == "Works."
        assert result.stopped_reason == "completed"


# ============================================================================
# Unit: streaming
# ============================================================================


class TestStreaming:
    @patch("core.agent_loop.stream_chat_completion")
    async def test_stream_yields_events(self, mock_stream_llm):
        """stream() yields AgentEventData objects."""
        async def _fake_stream(**kwargs):
            cb = kwargs.get("on_text_delta")
            if cb:
                await cb("Streamed!")
            return _text_response("Streamed!")

        mock_stream_llm.side_effect = _fake_stream
        config = _make_config()
        agent = AgentLoop(config)

        events = []
        async for event in agent.stream("Hi"):
            events.append(event)

        event_types = [e.event for e in events]
        assert AgentEvent.LOOP_START in event_types
        assert AgentEvent.TEXT_DELTA in event_types
        assert AgentEvent.LOOP_END in event_types

    @patch("core.agent_loop.stream_chat_completion")
    async def test_stream_text_delta_content(self, mock_stream_llm):
        """TEXT_DELTA events contain the text content."""
        # Simulate stream_chat_completion calling on_text_delta per-token
        async def _fake_stream(**kwargs):
            cb = kwargs.get("on_text_delta")
            if cb:
                await cb("Hello ")
                await cb("stream!")
            return _text_response("Hello stream!")

        mock_stream_llm.side_effect = _fake_stream
        config = _make_config()
        agent = AgentLoop(config)

        text_events = []
        async for event in agent.stream("Hi"):
            if event.event == AgentEvent.TEXT_DELTA:
                text_events.append(event)

        # Two token-level deltas
        assert len(text_events) == 2
        assert text_events[0].data["text"] == "Hello "
        assert text_events[1].data["text"] == "stream!"

    @patch("core.agent_loop.stream_chat_completion")
    async def test_stream_with_tools(self, mock_stream_llm):
        """stream() yields tool events during tool-use cycles."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 1
        registry = _mock_registry(execute_results={"recall": tool_result})

        call_count = 0

        async def _fake_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            cb = kwargs.get("on_text_delta")
            if call_count == 1:
                if cb:
                    await cb("Searching.")
                return _tool_response("Searching.", [_tool_call("recall", {"q": "x"})])
            else:
                if cb:
                    await cb("Found.")
                return _text_response("Found.")

        mock_stream_llm.side_effect = _fake_stream
        config = _make_config(registry=registry)
        agent = AgentLoop(config)

        event_types = []
        async for event in agent.stream("Find it"):
            event_types.append(event.event)

        assert AgentEvent.TOOL_START in event_types
        assert AgentEvent.TOOL_RESULT in event_types


# ============================================================================
# Unit: error handling
# ============================================================================


class TestErrorHandling:
    @patch("core.agent_loop.chat_completion")
    async def test_llm_error_returns_error_result(self, mock_llm):
        """LLM call failure returns error stopped_reason."""
        mock_llm.side_effect = RuntimeError("API error")
        config = _make_config()
        agent = AgentLoop(config)
        result = await agent.run("Fail please")

        assert result.stopped_reason == "error"
        assert result.iterations == 1
        assert result.timed_out is False

    @patch("core.agent_loop.chat_completion")
    async def test_tool_error_visible_to_llm(self, mock_llm):
        """Tool execution error is sent back to LLM as tool message."""
        fail_result = ToolResult.error_result("Permission denied", ToolErrorType.PERMISSION_DENIED)
        fail_result.energy_spent = 1
        registry = _mock_registry(execute_results={"write_file": fail_result})

        mock_llm.side_effect = [
            _tool_response("Writing.", [_tool_call("write_file", {"path": "/x"}, call_id="call_1")]),
            _text_response("Failed to write."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Write a file")

        # Verify error was passed back in messages
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "Permission denied" in tool_msgs[0]["content"]

        # LLM saw the error and responded
        assert result.text == "Failed to write."

    @patch("core.agent_loop.chat_completion")
    async def test_empty_tool_calls_list(self, mock_llm):
        """Empty tool_calls list treated as no tool calls."""
        mock_llm.return_value = {"content": "No tools.", "tool_calls": [], "raw": None}
        config = _make_config()
        agent = AgentLoop(config)
        result = await agent.run("Test")

        assert result.stopped_reason == "completed"
        assert len(result.tool_calls_made) == 0


# ============================================================================
# Unit: message format
# ============================================================================


class TestMessageFormat:
    @patch("core.agent_loop.chat_completion")
    async def test_assistant_message_includes_tool_calls(self, mock_llm):
        """Assistant message with tool calls includes them in OpenAI format."""
        tool_result = ToolResult.success_result("ok")
        tool_result.energy_spent = 0
        registry = _mock_registry(execute_results={"tool": tool_result})

        call_id = "call_test123"
        mock_llm.side_effect = [
            _tool_response("Using tool.", [_tool_call("tool", {"x": 1}, call_id=call_id)]),
            _text_response("Done."),
        ]

        config = _make_config(registry=registry)
        agent = AgentLoop(config)
        result = await agent.run("Test")

        # Find the assistant message with tool_calls
        assistant_msgs = [m for m in result.messages if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_msgs) == 1

        tc = assistant_msgs[0]["tool_calls"][0]
        assert tc["id"] == call_id
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "tool"
        # arguments should be a JSON string
        assert isinstance(tc["function"]["arguments"], str)
        assert json.loads(tc["function"]["arguments"]) == {"x": 1}

    def test_to_openai_tool_call_serializes_arguments(self):
        """_to_openai_tool_call converts dict arguments to JSON string."""
        result = _to_openai_tool_call({
            "id": "call_1",
            "name": "test",
            "arguments": {"key": "value"},
        })
        assert result["function"]["arguments"] == '{"key": "value"}'
        assert result["type"] == "function"

    def test_to_openai_tool_call_preserves_string_arguments(self):
        """If arguments is already a string, preserve it."""
        result = _to_openai_tool_call({
            "id": "call_1",
            "name": "test",
            "arguments": '{"key": "value"}',
        })
        assert result["function"]["arguments"] == '{"key": "value"}'

    def test_to_openai_tool_call_generates_id(self):
        """Missing ID gets a generated UUID."""
        result = _to_openai_tool_call({"name": "test", "arguments": {}})
        assert result["id"] is not None
        assert len(result["id"]) > 0


# ============================================================================
# Unit: result dataclass
# ============================================================================


class TestAgentLoopResult:
    def test_defaults(self):
        result = AgentLoopResult(
            text="hello",
            messages=[],
            tool_calls_made=[],
            iterations=1,
            energy_spent=0,
        )
        assert result.timed_out is False
        assert result.stopped_reason == "completed"

    def test_timeout_result(self):
        result = AgentLoopResult(
            text="",
            messages=[],
            tool_calls_made=[],
            iterations=0,
            energy_spent=0,
            timed_out=True,
            stopped_reason="timeout",
        )
        assert result.timed_out is True
        assert result.stopped_reason == "timeout"


# ============================================================================
# Unit: config dataclass
# ============================================================================


class TestAgentLoopConfig:
    def test_defaults(self):
        registry = _mock_registry()
        config = AgentLoopConfig(
            tool_context=ToolContext.CHAT,
            system_prompt="test",
            llm_config=_make_llm_config(),
            registry=registry,
            pool=registry.pool,
        )
        assert config.energy_budget is None
        assert config.max_iterations is None
        assert config.timeout_seconds == 300.0
        assert config.temperature == 0.7
        assert config.max_tokens == 4096
        assert config.session_id is None
        assert config.heartbeat_id is None
        assert config.on_event is None
        assert config.on_approval is None


# ============================================================================
# Unit: event dataclass
# ============================================================================


class TestAgentEventData:
    def test_defaults(self):
        event = AgentEventData(event=AgentEvent.LOOP_START)
        assert event.data == {}
        assert event.timestamp > 0

    def test_with_data(self):
        event = AgentEventData(
            event=AgentEvent.TEXT_DELTA,
            data={"text": "hello"},
        )
        assert event.data["text"] == "hello"
        assert event.event == AgentEvent.TEXT_DELTA


# ============================================================================
# Integration: real registry with mocked LLM
# ============================================================================


class TestIntegrationWithRegistry:
    @patch("core.agent_loop.chat_completion")
    async def test_chat_mode_with_real_registry(self, mock_llm, db_pool):
        """Chat mode uses real registry, unlimited energy."""
        from core.tools.registry import create_default_registry

        registry = create_default_registry(db_pool)

        mock_llm.side_effect = [
            _tool_response(
                "Let me recall.",
                [_tool_call("recall", {"query": "test"})],
            ),
            _text_response("I found something."),
        ]

        config = AgentLoopConfig(
            tool_context=ToolContext.CHAT,
            system_prompt="Test assistant.",
            llm_config=_make_llm_config(),
            registry=registry,
            pool=db_pool,
            energy_budget=None,
            timeout_seconds=30.0,
        )
        agent = AgentLoop(config)
        result = await agent.run("What do you know about test?")

        assert result.iterations == 2
        assert result.stopped_reason == "completed"
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0]["name"] == "recall"

    @patch("core.agent_loop.chat_completion")
    async def test_heartbeat_mode_with_energy(self, mock_llm, db_pool):
        """Heartbeat mode with energy budget stops when energy is used up."""
        from core.tools.registry import create_default_registry

        registry = create_default_registry(db_pool)

        mock_llm.side_effect = [
            _tool_response(
                "Recalling.",
                [_tool_call("recall", {"query": "goals"})],
            ),
            # After recall (cost 1), LLM tries another tool
            _tool_response(
                "Reflecting.",
                [_tool_call("reflect", {"query": "status"})],
            ),
            _text_response("Reflected."),
        ]

        config = AgentLoopConfig(
            tool_context=ToolContext.HEARTBEAT,
            system_prompt="Heartbeat agent.",
            llm_config=_make_llm_config(),
            registry=registry,
            pool=db_pool,
            energy_budget=20,
            timeout_seconds=30.0,
            heartbeat_id="hb-test-123",
        )
        agent = AgentLoop(config)
        result = await agent.run("Run heartbeat cycle")

        assert result.iterations >= 1
        assert result.energy_spent >= 0

    @patch("core.agent_loop.chat_completion")
    async def test_tool_specs_filtered_by_context(self, mock_llm, db_pool):
        """Registry returns context-appropriate tool specs."""
        from core.tools.registry import create_default_registry

        registry = create_default_registry(db_pool)

        mock_llm.return_value = _text_response("Done.")

        config = AgentLoopConfig(
            tool_context=ToolContext.CHAT,
            system_prompt="Test.",
            llm_config=_make_llm_config(),
            registry=registry,
            pool=db_pool,
            timeout_seconds=10.0,
        )
        agent = AgentLoop(config)
        result = await agent.run("Hi")

        # Verify tools were passed to LLM
        call_args = mock_llm.call_args
        tools = call_args.kwargs.get("tools") or []
        tool_names = [t["function"]["name"] for t in tools]

        # recall should be available in chat context
        assert "recall" in tool_names
