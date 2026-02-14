"""Hexis Chat TUI — Textual app for streaming conversations."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static
from textual.worker import Worker, WorkerState

from apps.tui.chat_widgets import (
    ChatInput,
    ChatLog,
    ContextSidebar,
    StreamingMessage,
)
from apps.tui.theme import hexis_theme


class ChatScreen(Screen):
    """Main chat screen with log, input, and optional sidebar."""

    BINDINGS = [
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+l", "clear_chat", "Clear"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._history: list[dict[str, Any]] = []
        self._verbose = False
        self._debug = False
        self._agent_name = "Hexis"
        self._streaming = False
        self._streaming_msg: StreamingMessage | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="chat-header", classes="chat-header")
        with Horizontal():
            with Vertical(id="chat-column"):
                yield ChatLog(id="chat-log")
                yield ChatInput(id="chat-input")
            yield ContextSidebar(id="context-sidebar")
        yield Static(
            " /quit  /clear  /recall <q>  /status  /verbose  /tools  /history ",
            classes="chat-footer",
        )

    async def on_mount(self) -> None:
        app: HexisChatApp = self.app  # type: ignore[assignment]
        self._agent_name = app.agent_name
        header = self.query_one("#chat-header", Static)
        header.update(
            f"[bold #d8774f]{self._agent_name}[/bold #d8774f]"
            f" [#4e463d]— streaming chat[/#4e463d]"
        )

        log = self.query_one("#chat-log", ChatLog)
        log.write_info(f"Connected to {self._agent_name}. Type /quit to exit, /help for commands.")

        self.query_one("#chat-input", ChatInput).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return

        text = event.value.strip()
        if not text:
            return

        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.push_history(text)
        chat_input.value = ""

        if text.startswith("/"):
            await self._handle_slash(text)
            return

        if self._streaming:
            return

        await self._send_message(text)

    async def _handle_slash(self, text: str) -> None:
        log = self.query_one("#chat-log", ChatLog)
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd in ("/quit", "/exit"):
            self.app.exit(0)
            return

        if cmd == "/clear":
            self._history.clear()
            # Remove all children from the ChatLog
            await log.remove_children()
            log.write_info("Conversation cleared.")
            return

        if cmd == "/help":
            log.write_info("Commands:")
            log.write_info("  /quit, /exit   — Exit chat")
            log.write_info("  /clear         — Clear conversation")
            log.write_info("  /recall <q>    — Search memories")
            log.write_info("  /status        — Show agent status")
            log.write_info("  /verbose       — Toggle verbose mode")
            log.write_info("  /debug         — Toggle debug mode")
            log.write_info("  /tools         — List available tools")
            log.write_info("  /history       — Show conversation history")
            return

        if cmd == "/recall":
            query = parts[1] if len(parts) > 1 else ""
            if not query:
                log.write_error("Usage: /recall <query>")
                return
            app: HexisChatApp = self.app  # type: ignore[assignment]
            try:
                from core.cognitive_memory_api import CognitiveMemory
                async with CognitiveMemory.connect(app.dsn) as mem:
                    result = await mem.recall(query, limit=5)
                if not result.memories:
                    log.write_info("No memories found.")
                else:
                    log.write_recall(result.memories)
            except Exception as e:
                log.write_error(str(e))
            return

        if cmd == "/status":
            app: HexisChatApp = self.app  # type: ignore[assignment]
            try:
                from core.cli_api import status_payload_rich
                payload = await status_payload_rich(app.dsn)
                agent = payload.get("agent", {})
                log.write_info(f"Agent: {agent.get('name', '?')}")
                log.write_info(f"Mood: {agent.get('mood', '?')}")
                energy = payload.get("energy", {})
                log.write_info(f"Energy: {energy.get('current', '?')}/{energy.get('max', '?')}")
                consent = payload.get("consent", {})
                log.write_info(f"Consent: {consent.get('status', '?')}")
            except Exception as e:
                log.write_error(str(e))
            return

        if cmd == "/verbose":
            self._verbose = not self._verbose
            sidebar = self.query_one("#context-sidebar", ContextSidebar)
            if self._verbose:
                sidebar.add_class("visible")
            else:
                sidebar.remove_class("visible")
            log.write_info(f"Verbose mode: {'on' if self._verbose else 'off'}")
            return

        if cmd == "/debug":
            self._debug = not self._debug
            self._verbose = self._verbose or self._debug
            log.write_info(f"Debug mode: {'on' if self._debug else 'off'} (verbose: {'on' if self._verbose else 'off'})")
            return

        if cmd == "/tools":
            app: HexisChatApp = self.app  # type: ignore[assignment]
            try:
                from core.tools import ToolContext
                specs = await app.registry.get_specs(ToolContext.CHAT)
                log.write_info("Available Tools:")
                for spec in specs:
                    func = spec.get("function", {})
                    name = func.get("name", "?")
                    desc = func.get("description", "")[:60]
                    log.write_info(f"  [#3c6f64]{name}[/#3c6f64] — {desc}")
            except Exception as e:
                log.write_error(str(e))
            return

        if cmd == "/history":
            if not self._history:
                log.write_info("No conversation history yet.")
            else:
                for i, msg in enumerate(self._history):
                    role = msg["role"]
                    content = msg["content"][:100]
                    log.write_info(f"  {i} [{role}]: {content}")
            return

        log.write_error(f"Unknown command: {cmd}")
        log.write_info("Type /help for available commands.")

    async def _send_message(self, text: str) -> None:
        log = self.query_one("#chat-log", ChatLog)
        log.write_user(text)

        self._streaming = True
        self._streaming_msg = log.start_assistant(self._agent_name)
        self.run_worker(
            self._stream_response(text),
            name="chat-stream",
            exclusive=True,
        )

    async def _stream_response(self, user_input: str) -> str:
        from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
        from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt
        from core.tools import ToolContext
        from services.chat import _remember_conversation

        app: HexisChatApp = self.app  # type: ignore[assignment]
        log = self.query_one("#chat-log", ChatLog)
        streaming_msg = self._streaming_msg

        async with CognitiveMemory.connect(app.dsn) as mem_client:
            # Hydrate memory context
            context = await mem_client.hydrate(
                user_input,
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
            if memory_context:
                enriched = f"{memory_context}\n\n[USER MESSAGE]\n{user_input}"
            else:
                enriched = user_input

            # Verbose: show context in sidebar
            if self._verbose:
                sidebar = self.query_one("#context-sidebar", ContextSidebar)
                sidebar.update_context(memory_context or "")
                if not sidebar.has_class("visible"):
                    sidebar.add_class("visible")

            if context.memories:
                log.write_info(f"Recalled {len(context.memories)} memories")

            # Configure agent loop
            session_id = str(uuid.uuid4())
            loop_config = AgentLoopConfig(
                tool_context=ToolContext.CHAT,
                system_prompt=app.system_prompt,
                llm_config=app.llm_config,
                registry=app.registry,
                pool=app.pool,
                energy_budget=None,
                max_iterations=6,
                timeout_seconds=120.0,
                temperature=0.7,
                max_tokens=1200,
                session_id=session_id,
            )

            agent = AgentLoop(loop_config)
            full_text = ""

            async for event in agent.stream(enriched, history=self._history):
                if event.event == AgentEvent.TEXT_DELTA:
                    text = event.data.get("text", "")
                    if text and streaming_msg:
                        full_text += text
                        streaming_msg.append_text(text)

                elif event.event == AgentEvent.TOOL_START:
                    tool_name = event.data.get("tool_name", "tool")
                    log.write_tool_start(tool_name)

                elif event.event == AgentEvent.TOOL_RESULT:
                    tool_name = event.data.get("tool_name", "tool")
                    success = event.data.get("success", False)
                    duration = event.data.get("duration")
                    error = event.data.get("error", "")
                    log.write_tool_result(tool_name, success, duration, error)

                elif event.event == AgentEvent.ERROR:
                    error_msg = event.data.get("error", "Unknown error")
                    log.write_error(error_msg)

            # Finalize streaming message
            if streaming_msg:
                log.finish_assistant(streaming_msg, self._agent_name)

            # Update history
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": full_text})

            # Memory formation
            if full_text:
                try:
                    await _remember_conversation(
                        mem_client,
                        user_message=user_input,
                        assistant_message=full_text,
                    )
                except Exception:
                    pass

        return full_text

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "chat-stream":
            return

        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            self._streaming = False
            self._streaming_msg = None
            self.query_one("#chat-input", ChatInput).focus()

            if event.state == WorkerState.ERROR:
                log = self.query_one("#chat-log", ChatLog)
                log.write_error(str(event.worker.error))

    def action_quit_app(self) -> None:
        self.app.exit(0)

    def action_clear_chat(self) -> None:
        self._history.clear()
        log = self.query_one("#chat-log", ChatLog)
        log.remove_children()
        log.write_info("Conversation cleared.")


class HexisChatApp(App):
    """Textual TUI for `hexis chat`."""

    TITLE = "Hexis Chat"
    CSS_PATH = "hexis.tcss"

    def __init__(self, argv: list[str] | None = None) -> None:
        super().__init__()
        self.register_theme(hexis_theme)
        self.theme = "hexis"
        self._argv = argv or []
        self.pool: Any = None
        self.registry: Any = None
        self.system_prompt: str = ""
        self.llm_config: dict[str, Any] = {}
        self.agent_name: str = "Hexis"
        self.dsn: str = ""

    async def on_mount(self) -> None:
        load_dotenv()
        import asyncpg
        from core.agent_api import db_dsn_from_env, get_agent_profile_context
        from core.llm_config import load_llm_config
        from core.tools import ToolContext, create_default_registry
        from services.chat import _build_system_prompt

        # Parse args
        dsn = None
        verbose = False
        debug = False
        i = 0
        while i < len(self._argv):
            if self._argv[i] == "--dsn" and i + 1 < len(self._argv):
                dsn = self._argv[i + 1]
                i += 2
            elif self._argv[i] in ("-v", "--verbose"):
                verbose = True
                i += 1
            elif self._argv[i] in ("-d", "--debug"):
                debug = True
                verbose = True
                i += 1
            else:
                i += 1

        self.dsn = dsn or db_dsn_from_env()

        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=5)

            async with self.pool.acquire() as conn:
                self.llm_config = await load_llm_config(conn, "llm.chat", fallback_key="llm")

            self.registry = create_default_registry(self.pool)
            agent_profile = await get_agent_profile_context(self.dsn)
            self.system_prompt = await _build_system_prompt(agent_profile, self.registry)

            if isinstance(agent_profile, dict):
                self.agent_name = agent_profile.get("name", "Hexis")

        except Exception as e:
            from apps.tui.dialogs import ErrorDialog
            await self.push_screen(ErrorDialog("Connection Error", str(e)))
            return

        screen = ChatScreen()
        if verbose:
            screen._verbose = True
        if debug:
            screen._debug = True
        await self.push_screen(screen)

    async def on_unmount(self) -> None:
        if self.pool:
            try:
                await self.pool.close()
            except Exception:
                pass
