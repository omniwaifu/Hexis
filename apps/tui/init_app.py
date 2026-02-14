"""Hexis Init TUI — Textual app for the init wizard."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from textual.app import App

from apps.tui.theme import hexis_theme


@dataclass
class InitState:
    """Accumulated state across wizard screens."""

    # LLM config
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    api_key_env: str = ""
    sub_provider: str = ""
    sub_model: str = ""
    sub_endpoint: str = ""
    sub_key_env: str = ""

    # Path choice
    tier: str = ""  # "express" | "character" | "custom"

    # Express
    user_name: str = "User"

    # Character
    character_cards: list[dict[str, Any]] = field(default_factory=list)
    chosen_card: dict[str, Any] | None = None
    tweaked_ext: dict[str, Any] | None = None

    # Custom
    agent_name: str = "Hexis"
    pronouns: str = "they/them"
    voice: str = "thoughtful and curious"
    description: str = ""
    purpose: str = "To be helpful, to learn, and to grow as an individual."
    personality_traits: dict[str, float] | None = None
    personality_desc: str = "reflective and exploratory"
    values: list[str] = field(default_factory=lambda: ["honesty", "growth", "kindness", "wisdom", "humility"])
    worldview: dict[str, str] = field(default_factory=lambda: {
        "metaphysics": "agnostic",
        "human_nature": "mixed",
        "epistemology": "empiricist",
        "ethics": "virtue ethics",
    })
    boundaries: list[str] = field(default_factory=lambda: [
        "I will not deceive people or falsify evidence.",
        "I will avoid causing harm.",
        "I will protect privacy and sensitive information.",
        "I will be honest about uncertainty.",
    ])
    interests: list[str] = field(default_factory=lambda: ["broad curiosity across domains"])
    goals: list[str] = field(default_factory=lambda: ["Support the user and grow as an individual"])
    relationship_type: str = "partner"

    # Consent result
    consent_decision: str = ""
    consent_reasoning: str = ""
    consent_signature: str = ""
    consent_memories: list[dict[str, Any]] = field(default_factory=list)

    # Final
    final_agent_name: str = ""

    # DB
    dsn: str = ""
    wait_seconds: int = 30


class HexisInitApp(App):
    """Textual TUI for `hexis init`."""

    TITLE = "Hexis Init"
    CSS_PATH = "hexis.tcss"

    def __init__(self, argv: list[str] | None = None) -> None:
        super().__init__()
        self.register_theme(hexis_theme)
        self.theme = "hexis"
        self.state = InitState()
        self._argv = argv or []
        self._conn: Any = None

    async def on_mount(self) -> None:
        load_dotenv()
        from core.agent_api import db_dsn_from_env, ensure_schema_has_config, _connect_with_retry

        # Parse DSN / wait-seconds from forwarded argv
        dsn = None
        wait_seconds = int(os.getenv("POSTGRES_WAIT_SECONDS", "30"))
        i = 0
        while i < len(self._argv):
            if self._argv[i] == "--dsn" and i + 1 < len(self._argv):
                dsn = self._argv[i + 1]
                i += 2
            elif self._argv[i] == "--wait-seconds" and i + 1 < len(self._argv):
                wait_seconds = int(self._argv[i + 1])
                i += 2
            else:
                i += 1

        self.state.dsn = dsn or db_dsn_from_env()
        self.state.wait_seconds = wait_seconds

        try:
            await ensure_schema_has_config(self.state.dsn, wait_seconds=wait_seconds)
            self._conn = await _connect_with_retry(self.state.dsn, wait_seconds=wait_seconds)
        except Exception as e:
            from apps.tui.dialogs import ErrorDialog
            await self.push_screen(ErrorDialog("Connection Error", str(e)))
            return

        from apps.tui.init_screens import LLMConfigScreen
        await self.push_screen(LLMConfigScreen())

    @property
    def conn(self) -> Any:
        return self._conn

    async def action_quit_app(self) -> None:
        if self._conn:
            await self._conn.close()
        self.exit(1)

    async def on_unmount(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
