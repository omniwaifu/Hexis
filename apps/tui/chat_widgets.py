"""Chat widgets for the Hexis TUI."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static


# ── Messages ─────────────────────────────────────────────────────────────────

class StreamChunk(Message):
    """A chunk of streamed text from the agent."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class StreamDone(Message):
    """Signal that streaming is complete for the current turn."""

    def __init__(self, full_text: str) -> None:
        super().__init__()
        self.full_text = full_text


class ToolStarted(Message):
    """A tool call has started."""

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments


class ToolCompleted(Message):
    """A tool call has completed."""

    def __init__(
        self,
        tool_name: str,
        success: bool,
        duration: float | None = None,
        output: str = "",
        error: str = "",
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.success = success
        self.duration = duration
        self.output = output
        self.error = error


class ChatError(Message):
    """An error occurred during streaming."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


# ── StreamingMessage ─────────────────────────────────────────────────────────

class StreamingMessage(Static):
    """A Static widget that updates in-place as streaming text arrives."""

    text: reactive[str] = reactive("")
    done: reactive[bool] = reactive(False)

    def __init__(self, agent_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent_name = agent_name

    def render(self) -> str:
        cursor = "" if self.done else "\u2588"
        if self.text:
            return f"[bold #3c6f64]{self._agent_name}:[/bold #3c6f64] {self.text}{cursor}"
        return f"[bold #3c6f64]{self._agent_name}:[/bold #3c6f64] {cursor}"

    def append_text(self, chunk: str) -> None:
        self.text += chunk

    def set_text(self, text: str) -> None:
        self.text = text

    def finalize(self, final_text: str | None = None) -> str:
        """Mark streaming complete and optionally replace content."""
        if final_text is not None:
            self.text = final_text
        self.done = True
        return self.text


# ── ChatLog ──────────────────────────────────────────────────────────────────

class ChatLog(VerticalScroll):
    """Scrollable chat log that composes Static widgets for each message."""

    def write_user(self, text: str) -> None:
        self.mount(Static(f"[bold #d8774f]you:[/bold #d8774f] {text}", classes="msg-user"))
        self.scroll_end(animate=False)

    def start_assistant(self, agent_name: str) -> StreamingMessage:
        msg = StreamingMessage(agent_name, classes="msg-assistant")
        self.mount(msg)
        self.scroll_end(animate=False)
        return msg

    def finish_assistant(
        self,
        streaming_msg: StreamingMessage,
        final_text: str | None = None,
    ) -> None:
        streaming_msg.finalize(final_text)
        self.scroll_end(animate=False)

    def write_tool_start(self, tool_name: str) -> None:
        self.mount(Static(
            f"  [italic #4e463d]{tool_name}...[/italic #4e463d]",
            classes="msg-tool",
        ))
        self.scroll_end(animate=False)

    def write_tool_result(
        self,
        tool_name: str,
        success: bool,
        duration: float | None = None,
        error: str = "",
    ) -> None:
        dur = f" [{duration:.1f}s]" if isinstance(duration, (int, float)) else ""
        if success:
            self.mount(Static(
                f"  [italic #4e463d]{tool_name}[/italic #4e463d] [green]done[/green][dim]{dur}[/dim]",
                classes="msg-tool",
            ))
        else:
            err_msg = f" {error[:80]}" if error else ""
            self.mount(Static(
                f"  [italic #4e463d]{tool_name}[/italic #4e463d] [red]failed[/red][dim]{dur}[/dim] [#4e463d]{err_msg}[/#4e463d]",
                classes="msg-tool",
            ))
        self.scroll_end(animate=False)

    def write_error(self, error: str) -> None:
        self.mount(Static(f"[bold red]Error: {error}[/bold red]", classes="msg-error"))
        self.scroll_end(animate=False)

    def write_info(self, text: str) -> None:
        self.mount(Static(f"[#4e463d]{text}[/#4e463d]"))
        self.scroll_end(animate=False)

    def write_recall(self, memories: list[Any]) -> None:
        for m in memories:
            content = m.content[:100] + "..." if len(m.content) > 100 else m.content
            self.mount(Static(
                f"  [#3c6f64]{m.type}[/#3c6f64] {content} [#4e463d](sim: {m.similarity:.2f})[/#4e463d]"
            ))
        self.scroll_end(animate=False)


# ── ChatInput ────────────────────────────────────────────────────────────────

class ChatInput(Input):
    """Chat input with command history via Up/Down arrows."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(placeholder="Type a message... (/help for commands)", **kwargs)
        self._history: list[str] = []
        self._history_idx: int = -1
        self._saved_input: str = ""

    def on_key(self, event: Any) -> None:
        if event.key == "up":
            if self._history:
                if self._history_idx == -1:
                    self._saved_input = self.value
                    self._history_idx = len(self._history) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
                self.value = self._history[self._history_idx]
                self.cursor_position = len(self.value)
            event.prevent_default()
        elif event.key == "down":
            if self._history_idx >= 0:
                if self._history_idx < len(self._history) - 1:
                    self._history_idx += 1
                    self.value = self._history[self._history_idx]
                else:
                    self._history_idx = -1
                    self.value = self._saved_input
                self.cursor_position = len(self.value)
            event.prevent_default()

    def push_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_idx = -1
        self._saved_input = ""


# ── ContextSidebar ───────────────────────────────────────────────────────────

class ContextSidebar(Widget):
    """Optional sidebar showing hydrated context."""

    def compose(self) -> ComposeResult:
        yield RichLog(id="context-log", wrap=True, markup=True)

    def update_context(self, context_text: str) -> None:
        log = self.query_one("#context-log", RichLog)
        log.clear()
        if context_text:
            log.write("[bold #3c6f64]Hydrated Context[/bold #3c6f64]")
            log.write("")
            log.write(context_text)
        else:
            log.write("[#4e463d]No memory context[/#4e463d]")

    def toggle(self) -> None:
        if self.has_class("visible"):
            self.remove_class("visible")
        else:
            self.add_class("visible")
