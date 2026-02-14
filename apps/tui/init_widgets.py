"""Custom widgets for the Hexis init wizard TUI."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static, RichLog


# ── Step bar ─────────────────────────────────────────────────────────────────

STEPS = ["Models", "Path", "Setup"]


class StepBar(Widget):
    """Progress indicator showing wizard steps: Models > Path > Setup > Consent."""

    current: reactive[int] = reactive(0)

    def __init__(self, current: int = 0) -> None:
        super().__init__()
        self.current = current

    def render(self) -> str:
        parts: list[str] = []
        for i, label in enumerate(STEPS):
            if i < self.current:
                parts.append(f"[green]{label}[/green]")
            elif i == self.current:
                parts.append(f"[bold #d8774f]\\[{label}][/bold #d8774f]")
            else:
                parts.append(f"[#4e463d]{label}[/#4e463d]")
        return " [#4e463d]>[/#4e463d] ".join(parts)


# ── Big Five inputs ──────────────────────────────────────────────────────────

_TRAIT_NAMES = [
    "Openness",
    "Conscientiousness",
    "Extraversion",
    "Agreeableness",
    "Neuroticism",
]

_TRAIT_KEYS = [t.lower() for t in _TRAIT_NAMES]


class BigFiveSliders(Widget):
    """Five personality trait inputs (0.0-1.0)."""

    def __init__(self, defaults: dict[str, float] | None = None) -> None:
        super().__init__()
        self._defaults = defaults or {}

    def compose(self) -> ComposeResult:
        for name, key in zip(_TRAIT_NAMES, _TRAIT_KEYS):
            default = self._defaults.get(key, 0.5)
            with Horizontal(classes="big-five-row"):
                yield Label(f"{name}:", classes="big-five-label")
                yield Input(
                    value=f"{default:.2f}",
                    placeholder="0.0 - 1.0",
                    id=f"trait-{key}",
                    classes="big-five-slider",
                    type="number",
                )

    def get_traits(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for key in _TRAIT_KEYS:
            try:
                inp = self.query_one(f"#trait-{key}", Input)
                val = float(inp.value)
                result[key] = max(0.0, min(1.0, val))
            except (ValueError, Exception):
                result[key] = 0.5
        return result


# ── Character preview ────────────────────────────────────────────────────────

class CharacterPreview(Widget):
    """Right-side panel showing details of a selected character card."""

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield RichLog(id="preview-log", wrap=True, markup=True, highlight=True)

    def update_preview(self, card: dict[str, Any] | None) -> None:
        log = self.query_one("#preview-log", RichLog)
        log.clear()
        if card is None:
            log.write("[#4e463d]Select a character to preview[/#4e463d]")
            return

        from core.init_api import get_card_summary

        summary = get_card_summary(card)
        log.write(f"[bold #d8774f]{summary['name']}[/bold #d8774f]")
        log.write("")
        if summary.get("voice"):
            log.write(f"[#3c6f64]Voice:[/#3c6f64] {summary['voice']}")
        if summary.get("values"):
            log.write(f"[#3c6f64]Values:[/#3c6f64] {summary['values']}")
        if summary.get("personality"):
            log.write(f"[#3c6f64]Personality:[/#3c6f64] {summary['personality']}")
        if summary.get("description"):
            log.write("")
            log.write(summary["description"])

        # Show Big Five bars if available
        ext = card.get("extensions_hexis", {})
        traits = ext.get("personality_traits", {})
        if traits:
            log.write("")
            log.write("[#3c6f64]Big Five:[/#3c6f64]")
            for trait_name in _TRAIT_NAMES:
                key = trait_name.lower()
                val = traits.get(key, 0.5)
                bar_width = 20
                filled = int(val * bar_width)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                log.write(f"  {trait_name:20s} [{bar}] {val:.2f}")
