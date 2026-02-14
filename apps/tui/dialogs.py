"""Reusable modal dialogs for Hexis TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ConfirmDialog(ModalScreen[bool]):
    """Yes/No confirmation dialog."""

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-box"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Yes", variant="success", id="yes")
                yield Button("No", variant="error", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ErrorDialog(ModalScreen[None]):
    """Error display dialog with a single OK button."""

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog-box"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
