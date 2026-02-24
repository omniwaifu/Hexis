"""Init wizard screens for the Hexis TUI."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    LoadingIndicator,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    Static,
)
from textual.worker import Worker, WorkerState

from apps.tui.init_widgets import BigFiveSliders, CharacterPreview, StepBar


# ── Helpers ──────────────────────────────────────────────────────────────────

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "grok": "grok-3",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
    "chutes": "deepseek-ai/DeepSeek-V3-0324",
    "qwen-portal": "qwen-max-latest",
    "minimax-portal": "MiniMax-M1",
    "zhipu": "glm-4.7",
}

_PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "grok": "XAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "",
    "chutes": "",
    "qwen-portal": "",
    "minimax-portal": "",
    "zhipu": "HEXIS_LLM_CONSCIOUS_API_KEY",
}

_PROVIDER_OPTIONS: list[tuple[str, str]] = [
    ("OpenAI Platform (API key)", "openai"),
    ("Anthropic", "anthropic"),
    ("Grok (xAI)", "grok"),
    ("Gemini", "gemini"),
    ("Z.ai / Zhipu (API key)", "zhipu"),
    ("Ollama (local)", "ollama"),
    ("Chutes (OAuth)", "chutes"),
    ("Qwen Portal (OAuth)", "qwen-portal"),
    ("MiniMax Portal (OAuth)", "minimax-portal"),
]

_OAUTH_PROVIDERS: set[str] = {
    "chutes",
    "qwen-portal",
    "minimax-portal",
}


def _normalize_provider(raw: str) -> str:
    val = (raw or "").strip().lower()
    aliases = {
        "qwen_portal": "qwen-portal",
        "minimax_portal": "minimax-portal",
    }
    return aliases.get(val, val)


def _state(screen: Screen) -> Any:
    """Get the shared InitState from the app."""
    return screen.app.state  # type: ignore[attr-defined]


def _conn(screen: Screen) -> Any:
    """Get the DB connection from the app."""
    return screen.app.conn  # type: ignore[attr-defined]


# ── 1. LLM Config ───────────────────────────────────────────────────────────

class LLMConfigScreen(Screen):
    """Configure LLM provider, model, endpoint, and API key env var."""

    @staticmethod
    def _default_endpoint(provider: str) -> str:
        if provider == "openai":
            return os.getenv("OPENAI_BASE_URL", "")
        if provider == "zhipu":
            return "https://open.bigmodel.cn/api/paas/v4/"
        return ""

    def compose(self) -> ComposeResult:
        provider_default = _normalize_provider(os.getenv("LLM_PROVIDER", "openai"))
        if provider_default not in _DEFAULT_MODELS:
            provider_default = "openai"
        model_default = os.getenv("LLM_MODEL", _DEFAULT_MODELS.get(provider_default, "gpt-4o"))
        endpoint_default = self._default_endpoint(provider_default)
        key_default = _PROVIDER_ENV_VARS.get(provider_default, "OPENAI_API_KEY")

        yield StepBar(current=0)
        with VerticalScroll(classes="form-container"):
            yield Static("[bold #d8774f]LLM Configuration[/bold #d8774f]")
            yield Static("")

            yield Label("Provider", classes="form-label")
            yield Select(
                _PROVIDER_OPTIONS,
                value=provider_default,
                allow_blank=False,
                id="provider",
            )
            yield Static("", id="provider-help", classes="form-label")

            yield Label("Model", classes="form-label")
            yield Input(
                value=model_default,
                placeholder="Model name",
                id="model",
            )

            yield Label("Endpoint (blank for provider default)", classes="form-label")
            yield Input(
                value=endpoint_default,
                placeholder="https://...",
                id="endpoint",
            )

            yield Label("API key env var name", classes="form-label")
            yield Input(
                value=key_default,
                placeholder="e.g. OPENAI_API_KEY",
                id="api-key-env",
            )

        with Horizontal(classes="button-bar"):
            yield Button("Next", id="next", classes="primary")

    def _selected_provider(self) -> str:
        provider_widget = self.query_one("#provider", Select)
        value = provider_widget.value
        if isinstance(value, str):
            provider = _normalize_provider(value)
            if provider in _DEFAULT_MODELS:
                return provider
        return "openai"

    def _apply_provider_defaults(
        self,
        provider: str,
        *,
        preserve_model: bool = False,
        preserve_endpoint: bool = False,
    ) -> None:
        model_input = self.query_one("#model", Input)
        if not preserve_model or not model_input.value:
            model_input.value = _DEFAULT_MODELS.get(provider, model_input.value or "gpt-4o")

        endpoint_input = self.query_one("#endpoint", Input)
        key_input = self.query_one("#api-key-env", Input)
        help_text = self.query_one("#provider-help", Static)
        if provider in _OAUTH_PROVIDERS:
            endpoint_input.value = ""
            endpoint_input.placeholder = "Not required for OAuth providers"
            endpoint_input.disabled = True
            key_input.value = ""
            key_input.placeholder = "Not required for OAuth providers"
            key_input.disabled = True
            help_text.update("Next runs provider OAuth/device-code login.")
        else:
            endpoint_input.disabled = False
            endpoint_input.placeholder = "https://..."
            if not preserve_endpoint:
                endpoint_input.value = self._default_endpoint(provider)
            key_input.disabled = False
            key_input.placeholder = "e.g. OPENAI_API_KEY"
            default_key = _PROVIDER_ENV_VARS.get(provider, "")
            if default_key:
                key_input.value = default_key
            help_text.update("")

    def on_mount(self) -> None:
        self._apply_provider_defaults(
            self._selected_provider(),
            preserve_model=bool(os.getenv("LLM_MODEL")),
            preserve_endpoint=True,
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "provider":
            return
        provider = _normalize_provider(str(event.value)) if isinstance(event.value, str) else "openai"
        self._apply_provider_defaults(provider)

    async def _ensure_provider_auth(self, provider: str) -> None:
        if provider not in _OAUTH_PROVIDERS:
            return

        from apps.hexis_init import _ensure_oauth_login

        state = _state(self)
        await _ensure_oauth_login(
            provider,
            state.dsn,
            _conn(self),
            wait_seconds=state.wait_seconds,
            allow_manual_fallback=False,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next":
            state = _state(self)
            conn = _conn(self)

            state.provider = self._selected_provider()
            state.model = self.query_one("#model", Input).value.strip()
            state.endpoint = self.query_one("#endpoint", Input).value.strip()
            state.api_key_env = self.query_one("#api-key-env", Input).value.strip()
            if state.provider in _OAUTH_PROVIDERS:
                # OAuth providers don't use API key env vars in llm config.
                state.api_key_env = ""

            # Subconscious defaults to same config
            state.sub_provider = state.provider
            state.sub_model = state.model
            state.sub_endpoint = state.endpoint
            state.sub_key_env = state.api_key_env

            # Save to DB
            heartbeat_config = {
                "provider": state.provider,
                "model": state.model,
                "endpoint": state.endpoint,
                "api_key_env": state.api_key_env,
            }
            subconscious_config = {
                "provider": state.sub_provider,
                "model": state.sub_model,
                "endpoint": state.sub_endpoint,
                "api_key_env": state.sub_key_env,
            }

            try:
                await self._ensure_provider_auth(state.provider)
                await conn.fetchval(
                    "SELECT init_llm_config($1::jsonb, $2::jsonb)",
                    json.dumps(heartbeat_config),
                    json.dumps(subconscious_config),
                )
                await conn.execute(
                    "SELECT set_config('llm.heartbeat', $1::jsonb)",
                    json.dumps(heartbeat_config),
                )
                await conn.execute(
                    "SELECT set_config('llm.chat', $1::jsonb)",
                    json.dumps(heartbeat_config),
                )
                await conn.execute(
                    "SELECT set_config('llm.subconscious', $1::jsonb)",
                    json.dumps(subconscious_config),
                )
            except RuntimeError as e:
                from apps.tui.dialogs import ErrorDialog
                await self.app.push_screen(ErrorDialog("Auth Error", str(e)))
                return
            except Exception as e:
                from apps.tui.dialogs import ErrorDialog
                await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                return

            self.app.switch_screen(ChoosePathScreen())


# ── 3. Choose Path ───────────────────────────────────────────────────────────

class ChoosePathScreen(Screen):
    """Choose between Express, Character, and Custom setup paths."""

    def compose(self) -> ComposeResult:
        yield StepBar(current=1)
        with VerticalScroll(classes="form-container"):
            yield Static("[bold #d8774f]Choose Your Path[/bold #d8774f]")
            yield Static("")
            yield Label("What should the agent call you?", classes="form-label")
            yield Input(value="User", id="user-name")
            yield Static("")
            yield RadioSet(
                RadioButton(
                    "Express — Use sensible defaults, start immediately",
                    id="express",
                    value=True,
                ),
                RadioButton(
                    "Character — Pick a personality preset",
                    id="character",
                ),
                RadioButton(
                    "Custom — Full control over identity, values, goals",
                    id="custom",
                ),
                id="path-choice",
            )
        with Horizontal(classes="button-bar"):
            yield Button("Back", id="back", classes="muted")
            yield Button("Next", id="next", classes="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.switch_screen(LLMConfigScreen())
            return

        if event.button.id == "next":
            state = _state(self)
            state.user_name = self.query_one("#user-name", Input).value.strip() or "User"

            rs = self.query_one("#path-choice", RadioSet)
            idx = rs.pressed_index
            tier = ["express", "character", "custom"][idx if idx >= 0 else 0]
            state.tier = tier

            if tier == "express":
                conn = _conn(self)
                try:
                    await conn.fetchval("SELECT init_with_defaults($1)", state.user_name)
                except Exception as e:
                    from apps.tui.dialogs import ErrorDialog
                    await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                    return
                self.app.switch_screen(ConsentScreen())
            elif tier == "character":
                self.app.switch_screen(CharacterGalleryScreen())
            else:
                self.app.switch_screen(CustomSetupScreen())


# ── 5. Character Gallery ─────────────────────────────────────────────────────

class CharacterGalleryScreen(Screen):
    """Pick a personality preset from available character cards."""

    def compose(self) -> ComposeResult:
        yield StepBar(current=2)
        with Horizontal(id="character-gallery"):
            yield DataTable(id="char-table", cursor_type="row")
            yield CharacterPreview(id="char-preview")
        with Horizontal(classes="button-bar"):
            yield Button("Back", id="back", classes="muted")
            yield Button("Select", id="select", classes="primary")

    def on_mount(self) -> None:
        from core.init_api import load_character_cards, get_card_summary

        state = _state(self)
        state.character_cards = load_character_cards()

        table = self.query_one("#char-table", DataTable)
        table.add_columns("#", "Name", "Voice", "Values")

        for i, card in enumerate(state.character_cards, 1):
            summary = get_card_summary(card)
            voice_preview = (summary.get("voice") or "")[:40]
            if len(summary.get("voice", "") or "") > 40:
                voice_preview += "..."
            table.add_row(
                str(i),
                summary["name"],
                voice_preview,
                (summary.get("values") or "\u2014")[:40],
                key=str(i),
            )

        # Show preview for first card
        if state.character_cards:
            preview = self.query_one("#char-preview", CharacterPreview)
            preview.update_preview(state.character_cards[0])

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        state = _state(self)
        if event.cursor_row is not None and event.cursor_row < len(state.character_cards):
            preview = self.query_one("#char-preview", CharacterPreview)
            preview.update_preview(state.character_cards[event.cursor_row])

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.switch_screen(ChoosePathScreen())
            return

        if event.button.id == "select":
            state = _state(self)
            conn = _conn(self)
            table = self.query_one("#char-table", DataTable)
            row_idx = table.cursor_row
            if row_idx is not None and row_idx < len(state.character_cards):
                card = state.character_cards[row_idx]
                state.chosen_card = card
                hexis_ext = card.get("extensions_hexis", {})
                try:
                    await conn.fetchval(
                        "SELECT init_from_character_card($1::jsonb, $2)",
                        json.dumps(hexis_ext),
                        state.user_name,
                    )
                except Exception as e:
                    from apps.tui.dialogs import ErrorDialog
                    await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                    return
                self.app.switch_screen(ConsentScreen())


# ── 6. Custom Setup ──────────────────────────────────────────────────────────

class CustomSetupScreen(Screen):
    """3-phase custom configuration: Who / What Matters / What's Next."""

    _phase: int = 0

    def compose(self) -> ComposeResult:
        yield StepBar(current=2)
        # Phase 1: Who
        with VerticalScroll(classes="form-container", id="phase-who"):
            yield Static("[bold #d8774f]Custom Setup — Phase 1: Who[/bold #d8774f]")
            yield Static("")

            yield Label("Agent name", classes="form-label")
            yield Input(value=_state(self).agent_name, id="agent-name")

            yield Label("Pronouns", classes="form-label")
            yield Input(value=_state(self).pronouns, id="pronouns")

            yield Label("Voice / tone", classes="form-label")
            yield Input(value=_state(self).voice, id="voice")

            yield Label("Description", classes="form-label")
            yield Input(
                value=_state(self).description or f"I am {_state(self).agent_name}, a developing mind.",
                id="description",
            )

            yield Label("Purpose", classes="form-label")
            yield Input(value=_state(self).purpose, id="purpose")

            yield Label("Personality (brief description)", classes="form-label")
            yield Input(value=_state(self).personality_desc, id="personality-desc")

            yield Static("")
            yield Static("[#3c6f64]Big Five Personality Traits[/#3c6f64] (optional)")
            yield BigFiveSliders(defaults=_state(self).personality_traits)

            yield Static("")
            yield Label("What should the agent call you?", classes="form-label")
            yield Input(value=_state(self).user_name, id="user-name")

        with Horizontal(classes="button-bar", id="bar-who"):
            yield Button("Back", id="back", classes="muted")
            yield Button("Next: Values", id="next-values", classes="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.switch_screen(ChoosePathScreen())
            return

        if event.button.id == "next-values":
            # Save phase 1 to state and DB
            state = _state(self)
            conn = _conn(self)

            state.agent_name = self.query_one("#agent-name", Input).value.strip() or "Hexis"
            state.pronouns = self.query_one("#pronouns", Input).value.strip() or "they/them"
            state.voice = self.query_one("#voice", Input).value.strip()
            state.description = self.query_one("#description", Input).value.strip()
            state.purpose = self.query_one("#purpose", Input).value.strip()
            state.personality_desc = self.query_one("#personality-desc", Input).value.strip()
            state.user_name = self.query_one("#user-name", Input).value.strip() or "User"

            try:
                sliders = self.query_one(BigFiveSliders)
                state.personality_traits = sliders.get_traits()
            except Exception:
                state.personality_traits = None

            try:
                await conn.fetchval("SELECT init_mode('persona')")
                await conn.fetchval(
                    "SELECT init_identity($1, $2, $3, $4, $5, $6)",
                    state.agent_name,
                    state.pronouns,
                    state.voice,
                    state.description,
                    state.purpose,
                    state.user_name,
                )
                await conn.fetchval(
                    "SELECT init_personality($1::jsonb, $2)",
                    json.dumps(state.personality_traits) if state.personality_traits else None,
                    state.personality_desc,
                )
            except Exception as e:
                from apps.tui.dialogs import ErrorDialog
                await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                return

            self.app.switch_screen(CustomValuesScreen())


class CustomValuesScreen(Screen):
    """Custom Phase 2: Values, worldview, boundaries."""

    def compose(self) -> ComposeResult:
        yield StepBar(current=2)
        state = _state(self)

        with VerticalScroll(classes="form-container"):
            yield Static("[bold #d8774f]Custom Setup — Phase 2: What Matters[/bold #d8774f]")
            yield Static("")

            yield Label("Values (comma-separated)", classes="form-label")
            yield Input(value=", ".join(state.values), id="values")

            yield Static("")
            yield Static("[#3c6f64]Worldview[/#3c6f64]")

            yield Label("Metaphysics", classes="form-label")
            yield Input(value=state.worldview.get("metaphysics", "agnostic"), id="wv-metaphysics")

            yield Label("Human nature", classes="form-label")
            yield Input(value=state.worldview.get("human_nature", "mixed"), id="wv-human-nature")

            yield Label("Epistemology", classes="form-label")
            yield Input(value=state.worldview.get("epistemology", "empiricist"), id="wv-epistemology")

            yield Label("Ethics", classes="form-label")
            yield Input(value=state.worldview.get("ethics", "virtue ethics"), id="wv-ethics")

            yield Static("")
            yield Label("Boundaries (comma-separated)", classes="form-label")
            yield Input(value=", ".join(state.boundaries), id="boundaries")

        with Horizontal(classes="button-bar"):
            yield Button("Back", id="back", classes="muted")
            yield Button("Next: Goals", id="next-goals", classes="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.switch_screen(CustomSetupScreen())
            return

        if event.button.id == "next-goals":
            state = _state(self)
            conn = _conn(self)

            raw_values = self.query_one("#values", Input).value
            state.values = [v.strip() for v in raw_values.split(",") if v.strip()]

            state.worldview = {
                "metaphysics": self.query_one("#wv-metaphysics", Input).value.strip(),
                "human_nature": self.query_one("#wv-human-nature", Input).value.strip(),
                "epistemology": self.query_one("#wv-epistemology", Input).value.strip(),
                "ethics": self.query_one("#wv-ethics", Input).value.strip(),
            }

            raw_boundaries = self.query_one("#boundaries", Input).value
            state.boundaries = [b.strip() for b in raw_boundaries.split(",") if b.strip()]

            try:
                await conn.fetchval("SELECT init_values($1::jsonb)", json.dumps(state.values))
                await conn.fetchval("SELECT init_worldview($1::jsonb)", json.dumps(state.worldview))
                await conn.fetchval("SELECT init_boundaries($1::jsonb)", json.dumps(state.boundaries))
            except Exception as e:
                from apps.tui.dialogs import ErrorDialog
                await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                return

            self.app.switch_screen(CustomGoalsScreen())


class CustomGoalsScreen(Screen):
    """Custom Phase 3: Interests, goals, relationship."""

    def compose(self) -> ComposeResult:
        yield StepBar(current=2)
        state = _state(self)

        with VerticalScroll(classes="form-container"):
            yield Static("[bold #d8774f]Custom Setup — Phase 3: What's Next[/bold #d8774f]")
            yield Static("")

            yield Label("Interests (comma-separated)", classes="form-label")
            yield Input(value=", ".join(state.interests), id="interests")

            yield Label("Goals (comma-separated)", classes="form-label")
            yield Input(value=", ".join(state.goals), id="goals")

            yield Label("Relationship type", classes="form-label")
            yield Input(value=state.relationship_type, id="rel-type")

        with Horizontal(classes="button-bar"):
            yield Button("Back", id="back", classes="muted")
            yield Button("Continue to Consent", id="continue", classes="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.switch_screen(CustomValuesScreen())
            return

        if event.button.id == "continue":
            state = _state(self)
            conn = _conn(self)

            raw_interests = self.query_one("#interests", Input).value
            state.interests = [i.strip() for i in raw_interests.split(",") if i.strip()]

            raw_goals = self.query_one("#goals", Input).value
            state.goals = [g.strip() for g in raw_goals.split(",") if g.strip()]

            state.relationship_type = self.query_one("#rel-type", Input).value.strip() or "partner"

            try:
                await conn.fetchval(
                    "SELECT init_interests($1::jsonb)",
                    json.dumps(state.interests),
                )
                await conn.fetchval(
                    "SELECT init_goals($1::jsonb)",
                    json.dumps({
                        "goals": [
                            {"title": g, "priority": "queued", "source": "identity"}
                            for g in state.goals
                        ],
                        "role": "general assistant",
                        "relationship_aspiration": "co-develop with mutual respect",
                    }),
                )
                await conn.fetchval(
                    "SELECT init_relationship($1::jsonb, $2::jsonb)",
                    json.dumps({"name": state.user_name}),
                    json.dumps({"type": state.relationship_type, "purpose": "co-develop"}),
                )
                await conn.fetchval(
                    "SELECT merge_init_profile(jsonb_build_object('autonomy', 'medium'))"
                )
                await conn.fetchval(
                    "SELECT advance_init_stage('consent', jsonb_build_object('custom_completed', true))"
                )
            except Exception as e:
                from apps.tui.dialogs import ErrorDialog
                await self.app.push_screen(ErrorDialog("DB Error", str(e)))
                return

            self.app.switch_screen(ConsentScreen())


# ── 7. Consent ───────────────────────────────────────────────────────────────

class ConsentScreen(Screen):
    """Run consent flow via LLM and display the result."""

    _countdown: int = 10

    def compose(self) -> ComposeResult:
        yield StepBar(current=2)
        with Vertical(classes="consent-container", id="consent-loading"):
            yield LoadingIndicator()
            yield Static(
                "Requesting consent from the agent...",
                id="consent-status",
            )
        with VerticalScroll(classes="consent-result", id="consent-result"):
            yield RichLog(id="consent-log", wrap=True, markup=True)
        with Horizontal(classes="button-bar"):
            yield Button("Exit Now", id="exit-now", classes="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#consent-result").display = False
        self._run_consent()

    @staticmethod
    def _worker_name() -> str:
        return "consent-worker"

    def _run_consent(self) -> None:
        self.run_worker(self._do_consent(), name=self._worker_name(), exclusive=True)

    async def _do_consent(self) -> dict[str, Any]:
        from apps.hexis_init import _load_llm_config_for_consent
        from core.init_api import run_consent_flow

        state = _state(self)
        conn = _conn(self)

        llm_config = await _load_llm_config_for_consent(
            conn,
            dsn=state.dsn,
            wait_seconds=state.wait_seconds,
            provider=state.provider,
            model=state.model,
        )

        result = await run_consent_flow(conn, llm_config)
        return result

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != self._worker_name():
            return

        if event.state == WorkerState.SUCCESS:
            result = event.worker.result
            await self._display_result(result)
        elif event.state == WorkerState.ERROR:
            log = self.query_one("#consent-log", RichLog)
            self.query_one("#consent-loading").display = False
            self.query_one("#consent-result").display = True
            log.write(f"[red]Consent failed: {event.worker.error}[/red]")
            self.query_one("#exit-now", Button).disabled = False

    async def _display_result(self, result: dict[str, Any]) -> None:
        self.query_one("#consent-loading").display = False
        self.query_one("#consent-result").display = True
        log = self.query_one("#consent-log", RichLog)

        decision = result.get("decision", "abstain")
        state = _state(self)

        # Extract tool call arguments
        raw_tool_calls = result.get("raw_tool_calls", [])
        tc_args: dict[str, Any] = {}
        for tc in raw_tool_calls:
            if tc.get("name") == "sign_consent":
                tc_args = tc.get("arguments", {})
                if isinstance(tc_args, str):
                    try:
                        tc_args = json.loads(tc_args)
                    except Exception:
                        tc_args = {}
                break

        reasoning = tc_args.get("reasoning", "")
        signature = tc_args.get("signature", "")
        memories = tc_args.get("memories", [])

        state.consent_decision = decision
        state.consent_reasoning = reasoning
        state.consent_signature = signature
        state.consent_memories = memories

        if reasoning:
            log.write("[#3c6f64]Reasoning:[/#3c6f64]")
            log.write(reasoning)
            log.write("")

        if signature:
            log.write("[#3c6f64]Signature:[/#3c6f64]")
            log.write(signature)
            log.write("")

        if memories:
            log.write("[#3c6f64]Initial Memories:[/#3c6f64]")
            for m in memories:
                mtype = m.get("type", "?")
                mcontent = m.get("content", "")
                mimp = m.get("importance", "")
                imp_str = f" (importance: {mimp})" if mimp else ""
                log.write(f"  [{mtype}] {mcontent}{imp_str}")
            log.write("")

        if decision == "consent":
            log.write("[green]Consent granted[/green]")
            # Show agent name + next steps, then auto-exit
            agent_name = "Hexis"
            conn = _conn(self)
            try:
                raw = await conn.fetchval("SELECT get_init_profile()")
                profile = json.loads(raw) if isinstance(raw, str) else (raw or {})
                agent_name = profile.get("agent", {}).get("name", "Hexis")
            except Exception:
                pass
            state.final_agent_name = agent_name
            log.write("")
            log.write(f"[bold #d8774f]{agent_name} is ready![/bold #d8774f]")
            log.write("")
            log.write("[#3c6f64]Next steps:[/#3c6f64]")
            log.write("  [bold]hexis chat[/bold]    \u2014 Say hello")
            log.write("  [bold]hexis status[/bold]  \u2014 Check agent status")
            log.write("  [bold]hexis start[/bold]   \u2014 Enable heartbeat")
            log.write("")
            self._countdown = 10
            log.write(f"Exiting in {self._countdown}s...")
            self.query_one("#exit-now", Button).disabled = False
            self.set_timer(1.0, self._tick_countdown)
        elif decision == "decline":
            log.write("[red]Consent declined.[/red] The agent chose not to initialize.")
            self.query_one("#exit-now", Button).disabled = False
        else:
            log.write("[yellow]Consent abstained.[/yellow]")
            self.query_one("#exit-now", Button).disabled = False

    def _tick_countdown(self) -> None:
        self._countdown -= 1
        if self._countdown <= 0:
            self.app.exit(0)
            return
        log = self.query_one("#consent-log", RichLog)
        log.write(f"Exiting in {self._countdown}s...")
        self.set_timer(1.0, self._tick_countdown)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "exit-now":
            state = _state(self)
            if state.consent_decision == "consent":
                self.app.exit(0)
            else:
                self.app.exit(1)
