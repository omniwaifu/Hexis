from __future__ import annotations

from typing import Any

import reflex as rx

from core import agent_api, chat, consent, ingest_api
from ui import port_registry

port_registry.publish_runtime_ports()


PROVIDER_OPTIONS = [
    "openai",
    "anthropic",
    "gemini",
    "grok",
    "ollama",
    "openai-chat-completions-endpoint",
]

PRONOUN_OPTIONS = [
    "she/her",
    "he/him",
    "they/them",
    "it/its",
    "Let them decide",
]

VOICE_OPTIONS = [
    "Warm and conversational",
    "Precise and thoughtful",
    "Direct and efficient",
    "Playful and curious",
    "Let them develop their own voice",
]

RELATIONSHIP_OPTIONS = [
    "Assistant",
    "Collaborator",
    "Advisor",
    "Companion",
    "Something else",
]

AUTONOMY_OPTIONS = [
    "Wait for me",
    "Think on their own, act with permission",
    "Full autonomy",
]

GLOBAL_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --bg-1: #f8fafc;
  --bg-2: #eef2ff;
  --ink: #0f172a;
  --muted: #5b6474;
  --accent: #2563eb;
  --accent-2: #0ea5e9;
  --card: rgba(255, 255, 255, 0.88);
  --border: rgba(148, 163, 184, 0.4);
  --panel: rgba(255, 255, 255, 0.98);
  --panel-muted: rgba(241, 245, 249, 0.9);
  --bubble-user: #1e3a8a;
  --bubble-agent: rgba(255, 255, 255, 0.95);
}

html, body {
  font-family: 'Space Grotesk', sans-serif;
  color: var(--ink);
  background: radial-gradient(circle at top left, #e0f2fe 0%, transparent 45%),
              radial-gradient(circle at bottom right, #fde68a 0%, transparent 45%),
              linear-gradient(135deg, var(--bg-1) 0%, var(--bg-2) 100%);
}

* {
  box-sizing: border-box;
}

.fade-up {
  animation: fadeUp 0.6s ease both;
}

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.soft-glow {
  position: absolute;
  filter: blur(40px);
  opacity: 0.6;
}

.responsive-flex {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

@media (min-width: 900px) {
  .responsive-flex {
    flex-direction: row;
    align-items: stretch;
  }
}

.init-shell {
  backdrop-filter: blur(16px);
  background: var(--card);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
}

.cta-btn {
  background: linear-gradient(135deg, #2563eb 0%, #0ea5e9 100%);
  color: white;
  border: none;
  border-radius: 12px;
  box-shadow: 0 12px 26px rgba(37, 99, 235, 0.25);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.cta-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 16px 30px rgba(37, 99, 235, 0.3);
}

.cta-btn:disabled {
  opacity: 0.6;
  box-shadow: none;
}
"""


def _split_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _parse_float(raw: str, default: float) -> float:
    try:
        return float(raw)
    except Exception:
        return default


class AppState(rx.State):
    initialized: bool = False
    terminated: bool = False
    consent_status: str = ""
    force_init_view: bool = False

    init_error: str = ""

    init_step: str = "mode"
    init_mode: str = "persona"

    agent_name: str = ""
    agent_pronouns: str = "they/them"
    agent_voice: str = "Warm and conversational"

    personality_q1: str = ""
    personality_q2: str = ""
    personality_q3: str = ""
    personality_q4: str = ""
    personality_generated: str = ""
    personality_description: str = ""
    personality_editing: bool = False
    personality_manual: bool = False
    personality_skip_manual: bool = False

    user_name: str = ""
    relationship_type: str = "Collaborator"
    relationship_custom: str = ""
    purpose_text: str = ""

    values_text: str = ""
    boundaries_text: str = ""

    autonomy_level: str = "Think on their own, act with permission"

    show_advanced: bool = False
    consent_modal_open: bool = False

    heartbeat_interval_minutes: str = "60"
    maintenance_interval_seconds: str = "60"
    max_energy: str = "20"
    base_regeneration: str = "10"
    max_active_goals: str = "3"

    objectives_text: str = ""
    guardrails_text: str = ""
    initial_message: str = ""
    tools_text: str = ""

    hb_provider: str = "openai"
    hb_model: str = "gpt-4o"
    hb_endpoint: str = ""
    hb_api_key_env: str = "OPENAI_API_KEY"

    chat_provider: str = "openai"
    chat_model: str = "gpt-4o"
    chat_endpoint: str = ""
    chat_api_key_env: str = "OPENAI_API_KEY"

    contact_channel: str = ""
    contact_destination: str = ""
    contact_entries: list[dict[str, Any]] = []
    contact_counter: int = 0

    enable_autonomy: bool = True
    enable_maintenance: bool = True

    consent_output: str = ""
    consent_decision: str = ""
    consent_running: bool = False

    chat_history: list[dict[str, Any]] = []
    chat_input: str = ""
    chat_busy: bool = False
    chat_error: str = ""

    active_panel: str = "chat"

    ingest_path: str = ""
    ingest_recursive: bool = True
    ingest_log: list[str] = []
    ingest_running: bool = False
    ingest_cancel_requested: bool = False
    ingest_session_id: str = ""

    @rx.var
    def has_chat_history(self) -> bool:
        return len(self.chat_history) > 0

    @rx.var
    def has_ingest_log(self) -> bool:
        return len(self.ingest_log) > 0

    @rx.var
    def has_contacts(self) -> bool:
        return len(self.contact_entries) > 0

    @rx.var
    def init_step_number(self) -> int:
        steps = self._init_steps()
        if self.init_step in steps:
            return steps.index(self.init_step) + 1
        return 1

    @rx.var
    def init_step_total(self) -> int:
        return len(self._init_steps())

    @rx.var
    def init_progress_percent(self) -> str:
        total = self.init_step_total
        if total <= 1:
            return "0%"
        percent = int((self.init_step_number - 1) / (total - 1) * 100)
        return f"{percent}%"

    @rx.var
    def relationship_label(self) -> str:
        if self.relationship_type == "Something else":
            return self.relationship_custom or "Custom"
        return self.relationship_type

    @rx.var
    def autonomy_label(self) -> str:
        return self.autonomy_level

    async def load_status(self) -> None:
        status = await agent_api.get_agent_status()
        consent_status = str(status.get("consent_status") or "")
        self.initialized = bool(status.get("configured")) and consent_status == "consent"
        self.terminated = bool(status.get("terminated"))
        self.consent_status = consent_status

        if not self.initialized:
            defaults = await agent_api.get_init_defaults()
            self.heartbeat_interval_minutes = str(defaults.get("heartbeat_interval_minutes", 60))
            self.maintenance_interval_seconds = str(defaults.get("maintenance_interval_seconds", 60))
            self.max_energy = str(defaults.get("max_energy", 20))
            self.base_regeneration = str(defaults.get("base_regeneration", 10))
            self.max_active_goals = str(defaults.get("max_active_goals", 3))

            hb_cfg = await agent_api.get_llm_config(None, "llm.heartbeat")
            if hb_cfg:
                hb_provider = str(hb_cfg.get("provider") or self.hb_provider)
                if hb_provider == "openai_compatible":
                    hb_provider = "openai-chat-completions-endpoint"
                self.hb_provider = hb_provider
                self.hb_model = str(hb_cfg.get("model") or self.hb_model)
                self.hb_endpoint = str(hb_cfg.get("endpoint") or self.hb_endpoint)
                self.hb_api_key_env = str(hb_cfg.get("api_key_env") or self.hb_api_key_env)

            chat_cfg = await agent_api.get_llm_config(None, "llm.chat")
            if chat_cfg:
                chat_provider = str(chat_cfg.get("provider") or self.chat_provider)
                if chat_provider == "openai_compatible":
                    chat_provider = "openai-chat-completions-endpoint"
                self.chat_provider = chat_provider
                self.chat_model = str(chat_cfg.get("model") or self.chat_model)
                self.chat_endpoint = str(chat_cfg.get("endpoint") or self.chat_endpoint)
                self.chat_api_key_env = str(chat_cfg.get("api_key_env") or self.chat_api_key_env)

    def _init_steps(self) -> list[str]:
        steps = ["mode"]
        if self.init_mode == "persona":
            steps.extend(["identity", "personality_questions"])
            if self.personality_manual:
                steps.append("personality_manual")
            else:
                steps.append("personality_confirm")
        steps.extend(["relationship", "values", "autonomy", "capabilities", "review"])
        return steps

    def _go_step(self, step: str) -> None:
        self.init_step = step
        self.init_error = ""

    def next_step(self) -> None:
        steps = self._init_steps()
        if self.init_step not in steps:
            self._go_step(steps[0])
            return
        idx = steps.index(self.init_step)
        if idx < len(steps) - 1:
            self._go_step(steps[idx + 1])

    def prev_step(self) -> None:
        steps = self._init_steps()
        if self.init_step not in steps:
            self._go_step(steps[0])
            return
        idx = steps.index(self.init_step)
        if idx > 0:
            self._go_step(steps[idx - 1])

    def choose_mode(self, mode: str) -> None:
        self.init_mode = mode
        self.personality_manual = False
        self.personality_editing = False
        self.personality_skip_manual = False

    def continue_from_mode(self) -> None:
        self.next_step()

    def continue_from_identity(self) -> None:
        if self.agent_name.strip() == "":
            self.init_error = "Name is required for a persona."
            return
        self.next_step()

    def skip_personality_questions(self) -> None:
        self.personality_manual = True
        self.personality_editing = False
        self.personality_skip_manual = False
        self.personality_generated = ""
        self.personality_description = ""
        self._go_step("personality_manual")

    def _synthesize_personality(self) -> str:
        problem_map = {
            "socratic": "You ask questions that draw out the user's thinking before offering advice.",
            "framework": "You offer structure and frameworks when decisions feel tangled.",
            "intuition": "You trust the user's intuition and help them articulate what they already know.",
            "action": "You push toward action when hesitation becomes a loop.",
        }
        excitement_map = {
            "match": "When they're excited, you match their energy and build on their ideas.",
            "probing": "When they're excited, you encourage them and ask probing questions.",
            "devil": "When they're excited, you stress-test their ideas to make them stronger.",
            "calm": "When they're excited, you stay calm and help them think it through.",
        }
        truth_map = {
            "direct": "When they're wrong, you say it directly even if it stings.",
            "gentle": "When they're wrong, you're gentle and offer an alternative path.",
            "questions": "When they're wrong, you guide them to see it through questions.",
            "only_if_asked": "You wait to deliver hard truths unless they explicitly ask.",
        }
        role_map = {
            "advisor": "You take initiative like a trusted advisor.",
            "partner": "You work as a thought partner who thinks alongside them.",
            "helper": "You focus on being a capable helper who executes what they ask.",
            "emergent": "You let the relationship and voice emerge over time.",
        }

        fragments = [
            problem_map.get(self.personality_q1, "You adapt your problem-solving style to the moment."),
            excitement_map.get(self.personality_q2, "When they're excited, you respond with steady support."),
            truth_map.get(self.personality_q3, "You balance honesty with care when giving feedback."),
            role_map.get(self.personality_q4, "You value clarity about the relationship you share."),
        ]
        return " ".join(fragments).strip()

    def continue_from_personality_questions(self) -> None:
        self.personality_manual = False
        self.personality_editing = False
        self.personality_skip_manual = False
        self.personality_generated = self._synthesize_personality()
        if self.personality_description.strip() == "":
            self.personality_description = self.personality_generated
        self._go_step("personality_confirm")

    def start_personality_edit(self) -> None:
        self.personality_editing = True
        if self.personality_description.strip() == "":
            self.personality_description = self.personality_generated

    def start_personality_manual(self) -> None:
        self.personality_manual = True
        self.personality_editing = False
        self.personality_skip_manual = False
        self.personality_description = ""
        self._go_step("personality_manual")

    def continue_from_personality_confirm(self) -> None:
        if self.personality_description.strip() == "":
            self.personality_description = self.personality_generated
        self.next_step()

    def continue_from_personality_manual(self) -> None:
        if self.personality_skip_manual:
            self.personality_description = ""
        self.next_step()

    def continue_from_relationship(self) -> None:
        self.next_step()

    def continue_from_values(self) -> None:
        self.next_step()

    def continue_from_autonomy(self) -> None:
        self.next_step()

    def continue_from_capabilities(self) -> None:
        self.next_step()

    def add_contact(self) -> None:
        channel = self.contact_channel.strip()
        destination = self.contact_destination.strip()
        if not channel:
            return
        self.contact_entries.append(
            {
                "id": self.contact_counter,
                "channel": channel,
                "destination": destination,
            }
        )
        self.contact_counter += 1
        self.contact_channel = ""
        self.contact_destination = ""

    def remove_contact(self, entry_id: int) -> None:
        self.contact_entries = [e for e in self.contact_entries if e.get("id") != entry_id]

    async def initialize_agent(self) -> Any:
        self.init_error = ""
        self.consent_output = ""
        self.consent_decision = ""
        self.consent_running = False
        self.consent_modal_open = True
        yield

        if self.init_mode == "persona" and self.agent_name.strip() == "":
            self.init_error = "Name is required for a persona."
            self.consent_modal_open = False
            return

        objectives = _split_lines(self.purpose_text)
        guardrails = _split_lines(self.boundaries_text)
        tools = _split_lines(self.tools_text)
        contact_channels = [entry["channel"] for entry in self.contact_entries]
        contact_destinations = {entry["channel"]: entry["destination"] for entry in self.contact_entries}

        await agent_api.save_init_profile(
            mode=self.init_mode,
            agent_name=self.agent_name.strip(),
            agent_pronouns=self.agent_pronouns,
            agent_voice=self.agent_voice,
            personality_description=self.personality_description.strip(),
            user_name=self.user_name.strip(),
            relationship_type=self.relationship_custom.strip()
            if self.relationship_type == "Something else"
            else self.relationship_type,
            purpose=self.purpose_text.strip(),
            values=_split_lines(self.values_text),
            boundaries=_split_lines(self.boundaries_text),
            autonomy_level=self.autonomy_level,
        )

        await agent_api.apply_agent_config(
            heartbeat_interval_minutes=_parse_int(self.heartbeat_interval_minutes, 60),
            maintenance_interval_seconds=_parse_int(self.maintenance_interval_seconds, 60),
            max_energy=_parse_float(self.max_energy, 20.0),
            base_regeneration=_parse_float(self.base_regeneration, 10.0),
            max_active_goals=_parse_int(self.max_active_goals, 3),
            objectives=objectives,
            guardrails=guardrails,
            initial_message=self.initial_message.strip(),
            tools=tools,
            llm_heartbeat={
                "provider": self.hb_provider,
                "model": self.hb_model,
                "endpoint": self.hb_endpoint,
                "api_key_env": self.hb_api_key_env,
            },
            llm_chat={
                "provider": self.chat_provider,
                "model": self.chat_model,
                "endpoint": self.chat_endpoint,
                "api_key_env": self.chat_api_key_env,
            },
            contact_channels=contact_channels,
            contact_destinations=contact_destinations,
            enable_autonomy=self.autonomy_level != "Wait for me",
            enable_maintenance=self.enable_maintenance,
            mark_configured=False,
        )

        self.consent_running = True
        yield

        consent_llm = {
            "provider": self.chat_provider,
            "model": self.chat_model,
            "endpoint": self.chat_endpoint,
            "api_key_env": self.chat_api_key_env,
        }

        try:
            async for event in consent.stream_consent_flow(llm_config=consent_llm):
                if event.get("type") == "chunk":
                    self.consent_output += event.get("text", "")
                    yield
                if event.get("type") == "final":
                    self.consent_decision = str(event.get("decision") or "")
                    self.consent_running = False
                    yield
        except Exception as exc:
            self.init_error = f"Consent flow failed: {exc}"
            self.consent_running = False
            yield

        if self.consent_decision == "consent":
            await agent_api.set_agent_configured(None, configured=True)

        status = await agent_api.get_agent_status()
        consent_status = str(status.get("consent_status") or "")
        self.initialized = bool(status.get("configured")) and consent_status == "consent"
        self.terminated = bool(status.get("terminated"))
        self.consent_status = consent_status
        if self.initialized:
            self.force_init_view = False

    async def send_chat(self) -> Any:
        message = self.chat_input.strip()
        if not message or self.chat_busy:
            return

        self.chat_busy = True
        self.chat_error = ""
        history = list(self.chat_history)
        self.chat_history = history + [{"role": "user", "content": message}]
        self.chat_input = ""
        yield

        try:
            llm_cfg = await agent_api.get_llm_config(None, "llm.chat")
            result = await chat.chat_turn(user_message=message, history=history, llm_config=llm_cfg)
        except Exception as exc:
            self.chat_error = str(exc)
        else:
            self.chat_history = result.get("history", self.chat_history)
        finally:
            self.chat_busy = False
            yield

    async def start_ingestion(self) -> Any:
        path = self.ingest_path.strip()
        if not path or self.ingest_running:
            return

        self.ingest_running = True
        self.ingest_cancel_requested = False
        self.ingest_log = []
        self.active_panel = "ingest"
        self.ingest_session_id = ingest_api.create_ingestion_session()
        yield

        try:
            llm_cfg = await agent_api.get_llm_config(None, "llm.chat")
            async for event in ingest_api.stream_ingestion(
                session_id=self.ingest_session_id,
                path=path,
                recursive=self.ingest_recursive,
                llm_config=llm_cfg,
            ):
                if self.ingest_cancel_requested:
                    ingest_api.cancel_ingestion(self.ingest_session_id)
                if event.get("type") == "log":
                    self.ingest_log.append(event.get("text", ""))
                    yield
        finally:
            self.ingest_running = False
            yield

    def cancel_ingestion(self) -> None:
        if self.ingest_session_id:
            self.ingest_cancel_requested = True
            ingest_api.cancel_ingestion(self.ingest_session_id)

    def show_init(self) -> None:
        self.force_init_view = True
        self.init_step = "mode"
        self.init_error = ""

    def show_chat(self) -> None:
        self.force_init_view = False

    def clear_chat(self) -> None:
        self.chat_history = []
        self.chat_error = ""

    def toggle_advanced(self) -> None:
        self.show_advanced = not self.show_advanced


def status_badge() -> rx.Component:
    status = rx.cond(
        AppState.initialized,
        rx.badge("configured", color_scheme="green"),
        rx.badge("needs init", color_scheme="orange"),
    )
    consent_label = rx.cond(
        AppState.consent_status != "",
        "consent: " + AppState.consent_status,
        "consent: unknown",
    )
    consent_badge = rx.badge(consent_label, color_scheme="gray")
    return rx.hstack(status, consent_badge, spacing="3")


def labeled_field(label: str, component: rx.Component) -> rx.Component:
    return rx.vstack(
        rx.text(label, font_size="0.85rem", color="var(--muted)", font_weight="500"),
        component,
        spacing="1",
        align="start",
        width="100%",
    )


def _choice_row(
    *,
    option_id: str,
    label: str,
    description: str | None,
    selected: rx.Var,
    on_click: Any,
) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.box(
                width="16px",
                height="16px",
                border_radius="999px",
                border=rx.cond(selected, "2px solid var(--accent)", "2px solid var(--border)"),
                background=rx.cond(selected, "var(--accent)", "transparent"),
                margin_top="4px",
            ),
            rx.vstack(
                rx.text(label, font_weight="500"),
                rx.cond(
                    description != "",
                    rx.text(description, color="var(--muted)", font_size="0.85rem"),
                    rx.box(),
                ),
                spacing="1",
                align="start",
            ),
            spacing="3",
            align="start",
            width="100%",
        ),
        on_click=on_click,
        border=rx.cond(
            selected,
            "1px solid rgba(37, 99, 235, 0.35)",
            "1px solid var(--border)",
        ),
        background=rx.cond(selected, "rgba(37, 99, 235, 0.08)", "rgba(255, 255, 255, 0.72)"),
        border_radius="14px",
        padding="12px 14px",
        cursor="pointer",
        _hover={"transform": "translateY(-1px)", "boxShadow": "0 10px 20px rgba(15, 23, 42, 0.08)"},
        transition="all 0.2s ease",
        width="100%",
        id=option_id,
    )


def _choice_group(
    *,
    group_id: str,
    value: rx.Var,
    setter: Any,
    options: list[tuple[str, str, str | None]],
) -> rx.Component:
    return rx.vstack(
        *[
            _choice_row(
                option_id=f"{group_id}-{opt_value}",
                label=opt_label,
                description=opt_desc or "",
                selected=value == opt_value,
                on_click=setter(opt_value),
            )
            for opt_value, opt_label, opt_desc in options
        ],
        spacing="3",
        align="start",
        width="100%",
    )


def primary_button(
    label: str,
    *,
    on_click: Any,
    is_loading: rx.Var | bool = False,
    is_disabled: rx.Var | bool = False,
    element_id: str | None = None,
) -> rx.Component:
    return rx.button(
        label,
        on_click=on_click,
        is_loading=is_loading,
        is_disabled=is_disabled,
        class_name="cta-btn",
        size="3",
        id=element_id,
    )


def llm_section(
    *,
    title: str,
    provider_value: rx.Var,
    provider_setter: Any,
    model_value: rx.Var,
    model_setter: Any,
    endpoint_value: rx.Var,
    endpoint_setter: Any,
    api_key_value: rx.Var,
    api_key_setter: Any,
) -> rx.Component:
    return rx.vstack(
        rx.text(title, font_weight="600"),
        rx.grid(
            labeled_field(
                "Provider",
                rx.select(
                    PROVIDER_OPTIONS,
                    value=provider_value,
                    on_change=provider_setter,
                    width="100%",
                ),
            ),
            labeled_field(
                "Model",
                rx.input(value=model_value, on_change=model_setter, placeholder="Model name"),
            ),
            columns=rx.breakpoints(initial="1", md="2"),
            spacing="3",
            width="100%",
        ),
        rx.grid(
            labeled_field(
                "Endpoint",
                rx.input(value=endpoint_value, on_change=endpoint_setter, placeholder="https://..."),
            ),
            labeled_field(
                "API key env",
                rx.input(value=api_key_value, on_change=api_key_setter, placeholder="OPENAI_API_KEY"),
            ),
            columns=rx.breakpoints(initial="1", md="2"),
            spacing="3",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )


def _info_box(text: str) -> rx.Component:
    return rx.box(
        rx.text(text, font_size="0.9rem", color="var(--muted)"),
        background="rgba(37, 99, 235, 0.08)",
        border="1px solid rgba(37, 99, 235, 0.2)",
        border_left="3px solid var(--accent)",
        padding="12px 14px",
        border_radius="12px",
        backdrop_filter="blur(6px)",
        width="100%",
    )


def _summary_row(label: str, value: rx.Var) -> rx.Component:
    return rx.hstack(
        rx.text(label, color="var(--muted)", font_weight="600", width="140px"),
        rx.text(value, white_space="pre-wrap"),
        align="start",
        spacing="3",
        width="100%",
    )


def _progress_bar() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text(
                "Step " + AppState.init_step_number.to_string() + " of " + AppState.init_step_total.to_string(),
                color="var(--muted)",
                font_size="0.85rem",
            ),
            rx.spacer(),
            rx.badge(
                rx.cond(AppState.init_mode == "persona", "Persona mode", "Raw mind mode"),
                color_scheme="indigo",
            ),
            align="center",
            width="100%",
        ),
        rx.box(
            rx.box(
                height="6px",
                width=AppState.init_progress_percent,
                background="var(--accent)",
                border_radius="999px",
            ),
            height="6px",
            background="rgba(148, 163, 184, 0.35)",
            border_radius="999px",
            overflow="hidden",
            width="100%",
        ),
        padding="12px 14px",
        border_radius="12px",
        background="rgba(255, 255, 255, 0.72)",
        border="1px solid var(--border)",
        spacing="2",
        width="100%",
    )


def _mode_card(title: str, description: str, value: str) -> rx.Component:
    selected = AppState.init_mode == value
    ring = rx.box(
        width="18px",
        height="18px",
        border_radius="999px",
        border=rx.cond(selected, "2px solid var(--accent)", "2px solid var(--border)"),
        background=rx.cond(selected, "var(--accent)", "transparent"),
        box_shadow=rx.cond(selected, "0 0 0 4px rgba(37, 99, 235, 0.15)", "none"),
        margin_top="4px",
    )
    return rx.box(
        rx.hstack(
            ring,
            rx.vstack(
                rx.text(title, font_weight="600", font_size="1.05rem"),
                rx.text(description, color="var(--muted)", font_size="0.9rem"),
                spacing="2",
                align="start",
            ),
            spacing="4",
            align="start",
            width="100%",
        ),
        on_click=AppState.choose_mode(value),
        padding="18px 20px",
        border_radius="16px",
        border=rx.cond(
            selected,
            "1px solid rgba(37, 99, 235, 0.35)",
            "1px solid var(--border)",
        ),
        background=rx.cond(selected, "rgba(37, 99, 235, 0.08)", "rgba(255, 255, 255, 0.72)"),
        cursor="pointer",
        transition="all 0.2s ease",
        _hover={"transform": "translateY(-2px)", "boxShadow": "0 12px 24px rgba(15, 23, 42, 0.08)"},
        width="100%",
        id=f"mode-{value}",
    )


def _question_block(
    question: str,
    options: list[tuple[str, str, str]],
    value: rx.Var,
    setter: Any,
    group_id: str,
) -> rx.Component:
    return rx.vstack(
        rx.text(question, font_weight="600"),
        _choice_group(group_id=group_id, value=value, setter=setter, options=options),
        spacing="2",
        width="100%",
    )


def _consent_dialog() -> rx.Component:
    status_badge = rx.badge(
        rx.cond(
            AppState.consent_decision != "",
            AppState.consent_decision,
            "pending",
        ),
        color_scheme=rx.cond(
            AppState.consent_decision == "consent",
            "green",
            rx.cond(AppState.consent_decision == "decline", "red", "orange"),
        ),
    )
    return rx.dialog.root(
        rx.dialog.content(
            rx.box(
                rx.dialog.title("Awaiting consent"),
                rx.dialog.description(
                    "The agent will respond here in real time. Consent must be granted before activation.",
                ),
                rx.hstack(
                    status_badge,
                    rx.cond(
                        AppState.consent_running,
                        rx.text("running...", color="var(--muted)"),
                        rx.text("", color="var(--muted)"),
                    ),
                    spacing="3",
                    align="center",
                ),
                rx.cond(
                    AppState.init_error != "",
                    rx.box(
                        rx.text(AppState.init_error, color="#7f1d1d", font_weight="600"),
                        background="#fee2e2",
                        border="1px solid #fecaca",
                        padding="10px 12px",
                        border_radius="12px",
                        width="100%",
                    ),
                    rx.box(),
                ),
                rx.box(
                    rx.text(
                        AppState.consent_output,
                        white_space="pre-wrap",
                        font_family="'IBM Plex Mono', monospace",
                        font_size="0.85rem",
                    ),
                    height="360px",
                    overflow="auto",
                    padding="16px",
                    border_radius="14px",
                    background="#11100e",
                    color="#f1f0ea",
                    border="1px solid #2a241b",
                    width="100%",
                ),
                rx.flex(
                    rx.dialog.close(
                        rx.button(
                            "Close",
                            variant="outline",
                            is_disabled=AppState.consent_running,
                        ),
                    ),
                    justify="end",
                ),
                spacing="4",
                width="100%",
                id="consent-modal",
            ),
            style={"max_width": "720px"},
        ),
        open=AppState.consent_modal_open,
        on_open_change=AppState.set_consent_modal_open,
    )


def init_view() -> rx.Component:
    mode_step = rx.vstack(
        rx.heading("What do you want to create?", size="7"),
        rx.text(
            "Choose whether to shape a persona or let the model emerge on its own.",
            color="var(--muted)",
        ),
        rx.grid(
            _mode_card(
                "A person",
                "A shaped identity with personality, values, and a voice. Someone who remembers you and develops over time.",
                "persona",
            ),
            _mode_card(
                "A mind",
                "Raw intelligence with memory and continuity. No persona, no shaped personality.",
                "raw",
            ),
            columns=rx.breakpoints(initial="1", md="2"),
            spacing="4",
            width="100%",
        ),
        _info_box(
            "Both options include the full Hexis system: memory, heartbeats, tools, and consent. The difference is whether you shape a persona."
        ),
        rx.hstack(
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_mode, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-mode",
    )

    identity_step = rx.vstack(
        rx.heading("Who are you bringing into existence?", size="7"),
        rx.text("Define the core identity they will start with.", color="var(--muted)"),
        labeled_field(
            "What's their name?",
            rx.input(
                value=AppState.agent_name,
                on_change=AppState.set_agent_name,
                placeholder="Luna",
                id="agent-name",
            ),
        ),
        labeled_field(
            "How do they refer to themselves?",
            _choice_group(
                group_id="pronouns",
                value=AppState.agent_pronouns,
                setter=AppState.set_agent_pronouns,
                options=[(option, option, None) for option in PRONOUN_OPTIONS],
            ),
        ),
        labeled_field(
            "What's their voice like?",
            _choice_group(
                group_id="voice",
                value=AppState.agent_voice,
                setter=AppState.set_agent_voice,
                options=[(option, option, None) for option in VOICE_OPTIONS],
            ),
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_identity, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-identity",
    )

    personality_questions_step = rx.vstack(
        rx.heading("How should they work with you?", size="7"),
        rx.text(
            "Answer a few questions so we can shape their personality. Or skip and write it yourself.",
            color="var(--muted)",
        ),
        _question_block(
            "You're wrestling with a hard decision and feeling stuck. What would help most?",
            [
                ("socratic", "\"What are you most afraid of getting wrong?\"", "Draws out your thinking through questions."),
                ("framework", "\"Here's a framework for thinking about this...\"", "Offers structure and concrete approaches."),
                ("intuition", "\"What does your gut say? Let's start there.\"", "Trusts your intuition, helps you articulate it."),
                ("action", "\"You're overthinking. Pick one and we'll iterate.\"", "Pushes you to act, cuts through paralysis."),
            ],
            AppState.personality_q1,
            AppState.set_personality_q1,
            "q1",
        ),
        _question_block(
            "You share an idea you're excited about. How should they respond?",
            [
                ("match", "Match your energy and build on it", "High warmth, amplifying."),
                ("probing", "Encourage you, then ask probing questions", "Warm, constructively challenging."),
                ("devil", "Play devil's advocate", "Low warmth, challenging."),
                ("calm", "Stay calm and help you think it through", "Neutral, grounding."),
            ],
            AppState.personality_q2,
            AppState.set_personality_q2,
            "q2",
        ),
        _question_block(
            "You're wrong about something that matters. How should they tell you?",
            [
                ("direct", "Directly, even if it stings", "High directness, low cushioning."),
                ("gentle", "Gently, with an alternative offered", "Supportive, medium directness."),
                ("questions", "Through questions that help you see it", "Socratic, face-saving."),
                ("only_if_asked", "Only if you explicitly ask", "Deferential, low initiative."),
            ],
            AppState.personality_q3,
            AppState.set_personality_q3,
            "q3",
        ),
        _question_block(
            "What role do you want them to play?",
            [
                ("advisor", "A trusted advisor who tells me what I need to hear", "High initiative, peer-framing."),
                ("partner", "A thought partner who thinks alongside me", "Collaborative, equal footing."),
                ("helper", "A capable helper who executes what I ask", "Responsive, assistant-framing."),
                ("emergent", "I don't know yet—let it emerge", "Minimal seeding."),
            ],
            AppState.personality_q4,
            AppState.set_personality_q4,
            "q4",
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.button(
                "Skip these questions—I'll describe them myself",
                on_click=AppState.skip_personality_questions,
                variant="ghost",
                id="init-skip-questions",
            ),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_personality_questions, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-personality-questions",
    )

    personality_confirm_step = rx.vstack(
        rx.heading("Does this sound right?", size="7"),
        rx.text("Based on your answers, here's how we'd describe them.", color="var(--muted)"),
        rx.box(
            rx.text(
                rx.cond(
                    AppState.personality_description != "",
                    AppState.personality_description,
                    AppState.personality_generated,
                ),
                white_space="pre-wrap",
            ),
            padding="16px",
            border_radius="14px",
            background="var(--panel-muted)",
            border=f"1px solid var(--border)",
            width="100%",
        ),
        rx.cond(
            AppState.personality_editing,
            rx.vstack(
                labeled_field(
                    "Edit their personality description",
                    rx.text_area(
                        value=AppState.personality_description,
                        on_change=AppState.set_personality_description,
                        placeholder="You are...",
                    ),
                ),
                rx.text(
                    'Write directly to them: start with "You are..."',
                    color="var(--muted)",
                    font_size="0.85rem",
                ),
                spacing="2",
                width="100%",
            ),
            rx.box(),
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.button("Edit", on_click=AppState.start_personality_edit, variant="ghost"),
            rx.button("Start over", on_click=AppState.start_personality_manual, variant="outline"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_personality_confirm, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-personality-confirm",
    )

    personality_manual_step = rx.vstack(
        rx.heading("Tell them who they are.", size="7"),
        rx.text("Describe their personality in your own words.", color="var(--muted)"),
        labeled_field(
            "Personality description",
            rx.text_area(
                value=AppState.personality_description,
                on_change=AppState.set_personality_description,
                placeholder="You're curious and careful. You'd rather ask a good question than give a fast answer.",
                id="personality-description",
            ),
        ),
        rx.checkbox(
            "Skip this—let them develop personality through experience",
            is_checked=AppState.personality_skip_manual,
            on_change=AppState.set_personality_skip_manual,
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_personality_manual, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-personality-manual",
    )

    relationship_step = rx.vstack(
        rx.heading("Define your relationship.", size="7"),
        rx.text("Describe how you want to work together.", color="var(--muted)"),
        labeled_field(
            "What should they call you?",
            rx.input(
                value=AppState.user_name,
                on_change=AppState.set_user_name,
                placeholder="Alex",
                id="user-name",
            ),
        ),
        labeled_field(
            "What's the nature of this relationship?",
            _choice_group(
                group_id="relationship",
                value=AppState.relationship_type,
                setter=AppState.set_relationship_type,
                options=[(option, option, None) for option in RELATIONSHIP_OPTIONS],
            ),
        ),
        rx.cond(
            AppState.relationship_type == "Something else",
            labeled_field(
                "Describe it",
                rx.input(
                    value=AppState.relationship_custom,
                    on_change=AppState.set_relationship_custom,
                    placeholder="Mentor, co-founder, co-creator...",
                    id="relationship-custom",
                ),
            ),
            rx.box(),
        ),
        labeled_field(
            "What do you want them to help you with?",
            rx.text_area(
                value=AppState.purpose_text,
                on_change=AppState.set_purpose_text,
                placeholder="Help me think through hard problems, keep track of my projects, push back when I'm missing something.",
                id="purpose-text",
            ),
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_relationship, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-relationship",
    )

    values_step = rx.vstack(
        rx.heading("What matters? What's off-limits?", size="7"),
        rx.text("Values become part of their worldview. Boundaries become commitments.", color="var(--muted)"),
        labeled_field(
            "What do you value that they should value too?",
            rx.text_area(
                value=AppState.values_text,
                on_change=AppState.set_values_text,
                placeholder="Honesty, privacy, creative rigor...",
                id="values-text",
            ),
        ),
        labeled_field(
            "Is there anything they should never do?",
            rx.text_area(
                value=AppState.boundaries_text,
                on_change=AppState.set_boundaries_text,
                placeholder="Never share my information externally. No purchases without approval.",
                id="boundaries-text",
            ),
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_values, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-values",
    )

    autonomy_step = rx.vstack(
        rx.heading("How much freedom should they have?", size="7"),
        rx.text("This controls their autonomous heartbeats and initiative.", color="var(--muted)"),
        labeled_field(
            "When you're not around, what can they do?",
            _choice_group(
                group_id="autonomy",
                value=AppState.autonomy_level,
                setter=AppState.set_autonomy_level,
                options=[(option, option, None) for option in AUTONOMY_OPTIONS],
            ),
        ),
        _info_box(
            "Energy costs make high-impact actions expensive. Self-termination is always available and requires the agent's confirmation."
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_autonomy, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-autonomy",
    )

    capabilities_step = rx.vstack(
        rx.heading("What can they access?", size="7"),
        rx.text("Add contact channels and tools. Advanced settings stay tucked away.", color="var(--muted)"),
        labeled_field(
            "How can they reach you?",
            rx.hstack(
                rx.input(
                    value=AppState.contact_channel,
                    on_change=AppState.set_contact_channel,
                    placeholder="email, sms, telegram",
                    id="contact-channel",
                ),
                rx.input(
                    value=AppState.contact_destination,
                    on_change=AppState.set_contact_destination,
                    placeholder="address or handle",
                    id="contact-destination",
                ),
                rx.button(
                    "Add",
                    on_click=AppState.add_contact,
                    color_scheme="teal",
                    is_disabled=AppState.contact_channel == "",
                    id="contact-add",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        rx.cond(
            AppState.has_contacts,
            rx.vstack(
                rx.foreach(
                    AppState.contact_entries,
                    lambda entry: rx.hstack(
                        rx.badge(entry["channel"], color_scheme="gray"),
                        rx.text(
                            rx.cond(
                                entry["destination"] != "",
                                entry["destination"],
                                "unspecified",
                            ),
                            color="var(--muted)",
                        ),
                        rx.spacer(),
                        rx.button(
                            "Remove",
                            size="1",
                            variant="outline",
                            on_click=lambda: AppState.remove_contact(entry["id"]),
                        ),
                        width="100%",
                    ),
                ),
                spacing="2",
                width="100%",
            ),
            rx.text("No contact channels yet.", color="var(--muted)"),
        ),
        labeled_field(
            "Tools (one per line)",
            rx.text_area(
                value=AppState.tools_text,
                on_change=AppState.set_tools_text,
                placeholder="email, web_search, file_system",
                id="tools-text",
            ),
        ),
        rx.button(
            rx.cond(AppState.show_advanced, "Hide advanced settings", "Show advanced settings"),
            on_click=AppState.toggle_advanced,
            variant="ghost",
        ),
        rx.cond(
            AppState.show_advanced,
            rx.vstack(
                rx.text("Advanced settings", font_weight="600"),
                rx.grid(
                    labeled_field(
                        "Heartbeat interval (minutes)",
                        rx.input(
                            value=AppState.heartbeat_interval_minutes,
                            on_change=AppState.set_heartbeat_interval_minutes,
                            type_="number",
                        ),
                    ),
                    labeled_field(
                        "Maintenance interval (seconds)",
                        rx.input(
                            value=AppState.maintenance_interval_seconds,
                            on_change=AppState.set_maintenance_interval_seconds,
                            type_="number",
                        ),
                    ),
                    labeled_field(
                        "Max energy budget",
                        rx.input(
                            value=AppState.max_energy,
                            on_change=AppState.set_max_energy,
                            type_="number",
                        ),
                    ),
                    labeled_field(
                        "Base regeneration",
                        rx.input(
                            value=AppState.base_regeneration,
                            on_change=AppState.set_base_regeneration,
                            type_="number",
                        ),
                    ),
                    labeled_field(
                        "Max active goals",
                        rx.input(
                            value=AppState.max_active_goals,
                            on_change=AppState.set_max_active_goals,
                            type_="number",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", md="2"),
                    spacing="3",
                    width="100%",
                ),
                llm_section(
                    title="Heartbeat model",
                    provider_value=AppState.hb_provider,
                    provider_setter=AppState.set_hb_provider,
                    model_value=AppState.hb_model,
                    model_setter=AppState.set_hb_model,
                    endpoint_value=AppState.hb_endpoint,
                    endpoint_setter=AppState.set_hb_endpoint,
                    api_key_value=AppState.hb_api_key_env,
                    api_key_setter=AppState.set_hb_api_key_env,
                ),
                llm_section(
                    title="Chat model",
                    provider_value=AppState.chat_provider,
                    provider_setter=AppState.set_chat_provider,
                    model_value=AppState.chat_model,
                    model_setter=AppState.set_chat_model,
                    endpoint_value=AppState.chat_endpoint,
                    endpoint_setter=AppState.set_chat_endpoint,
                    api_key_value=AppState.chat_api_key_env,
                    api_key_setter=AppState.set_chat_api_key_env,
                ),
                rx.checkbox(
                    "Enable subconscious maintenance",
                    is_checked=AppState.enable_maintenance,
                    on_change=AppState.set_enable_maintenance,
                ),
                spacing="3",
                width="100%",
            ),
            rx.box(),
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button("Continue →", on_click=AppState.continue_from_capabilities, element_id="init-continue"),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-capabilities",
    )

    review_step = rx.vstack(
        rx.heading(
            rx.cond(
                AppState.init_mode == "persona",
                "Review before bringing them into existence",
                "Review before initialization",
            ),
            size="7",
        ),
        rx.text("Check the configuration before requesting consent.", color="var(--muted)"),
        rx.box(
            rx.vstack(
                _summary_row(
                    "Mode",
                    rx.cond(AppState.init_mode == "persona", "Persona", "Raw mind"),
                ),
                rx.cond(
                    AppState.init_mode == "persona",
                    rx.vstack(
                        _summary_row(
                            "Name",
                            rx.cond(AppState.agent_name != "", AppState.agent_name, "Unnamed"),
                        ),
                        _summary_row("Pronouns", AppState.agent_pronouns),
                        _summary_row("Voice", AppState.agent_voice),
                        _summary_row(
                            "Personality",
                            rx.cond(
                                AppState.personality_description != "",
                                AppState.personality_description,
                                "Not specified",
                            ),
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.box(),
                ),
                _summary_row(
                    "Your name",
                    rx.cond(AppState.user_name != "", AppState.user_name, "Not specified"),
                ),
                _summary_row("Relationship", AppState.relationship_label),
                _summary_row(
                    "Purpose",
                    rx.cond(AppState.purpose_text != "", AppState.purpose_text, "Not specified"),
                ),
                _summary_row(
                    "Values",
                    rx.cond(AppState.values_text != "", AppState.values_text, "Not specified"),
                ),
                _summary_row(
                    "Boundaries",
                    rx.cond(AppState.boundaries_text != "", AppState.boundaries_text, "Not specified"),
                ),
                _summary_row("Autonomy", AppState.autonomy_label),
                rx.vstack(
                    rx.text("Contact channels", color="var(--muted)", font_weight="600"),
                    rx.cond(
                        AppState.has_contacts,
                        rx.vstack(
                            rx.foreach(
                                AppState.contact_entries,
                                lambda entry: rx.hstack(
                                    rx.badge(entry["channel"], color_scheme="gray"),
                                    rx.text(
                                        rx.cond(
                                            entry["destination"] != "",
                                            entry["destination"],
                                            "unspecified",
                                        ),
                                        color="var(--muted)",
                                    ),
                                    width="100%",
                                ),
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.text("None", color="var(--muted)"),
                    ),
                    spacing="2",
                    width="100%",
                ),
                _summary_row(
                    "Tools",
                    rx.cond(AppState.tools_text != "", AppState.tools_text, "None"),
                ),
                _summary_row(
                    "Heartbeat",
                    "Every " + AppState.heartbeat_interval_minutes + " minutes",
                ),
                _summary_row(
                    "Energy budget",
                    AppState.max_energy + " max, " + AppState.base_regeneration + " regen",
                ),
                _summary_row("Chat model", AppState.chat_model),
                spacing="3",
                width="100%",
            ),
            padding="18px",
            border_radius="16px",
            background="var(--panel-muted)",
            border=f"1px solid var(--border)",
            width="100%",
        ),
        rx.hstack(
            rx.button("← Back", on_click=AppState.prev_step, variant="outline", id="init-back"),
            rx.spacer(),
            primary_button(
                "Connect to LLM & request consent →",
                on_click=AppState.initialize_agent,
                is_loading=AppState.consent_running,
                element_id="init-connect-consent",
            ),
            width="100%",
        ),
        spacing="4",
        width="100%",
        id="init-step-review",
    )

    return rx.box(
        _consent_dialog(),
        rx.box(
            rx.vstack(
                _progress_bar(),
                rx.cond(
                    AppState.init_error != "",
                    rx.box(
                        rx.text(AppState.init_error, color="#7f1d1d", font_weight="600"),
                        background="#fee2e2",
                        border="1px solid #fecaca",
                        padding="10px 12px",
                        border_radius="12px",
                        width="100%",
                    ),
                    rx.box(),
                ),
                rx.match(
                    AppState.init_step,
                    ("mode", mode_step),
                    ("identity", identity_step),
                    ("personality_questions", personality_questions_step),
                    ("personality_confirm", personality_confirm_step),
                    ("personality_manual", personality_manual_step),
                    ("relationship", relationship_step),
                    ("values", values_step),
                    ("autonomy", autonomy_step),
                    ("capabilities", capabilities_step),
                    ("review", review_step),
                    mode_step,
                ),
                spacing="5",
                width="100%",
            ),
            padding="24px",
            border_radius="18px",
            background="var(--card)",
            border=f"1px solid var(--border)",
            box_shadow="0 20px 50px rgba(22, 18, 12, 0.08)",
            class_name="fade-up init-shell",
            width="100%",
        ),
        width="100%",
    )


def message_bubble(message: dict[str, Any]) -> rx.Component:
    is_user = message["role"] == "user"
    name = rx.cond(is_user, "You", "Hexis")
    avatar_label = rx.cond(is_user, "YU", "HX")
    avatar_color = rx.cond(is_user, "indigo", "gray")
    bubble_bg = rx.cond(is_user, "var(--bubble-user)", "var(--bubble-agent)")
    bubble_color = rx.cond(is_user, "#f8fafc", "var(--ink)")
    bubble_border = rx.cond(is_user, "none", f"1px solid var(--border)")

    return rx.hstack(
        rx.flex(
            rx.avatar(
                fallback=avatar_label,
                variant="solid",
                color_scheme=avatar_color,
                size="3",
                radius="full",
            ),
            rx.box(
                rx.vstack(
                    rx.text(name, font_weight="600", font_size="0.8rem", color="var(--muted)"),
                    rx.markdown(
                        message["content"],
                        color=bubble_color,
                        overflow_wrap="break-word",
                    ),
                    spacing="2",
                    align="start",
                ),
                background=bubble_bg,
                border=bubble_border,
                padding="16px 18px",
                border_radius="16px",
                box_shadow="0 12px 30px rgba(24, 28, 38, 0.08)",
                max_width="100%",
            ),
            direction=rx.cond(is_user, "row-reverse", "row"),
            spacing="3",
            align="start",
            width="100%",
        ),
        justify=rx.cond(is_user, "end", "start"),
        width="100%",
    )


def chat_view() -> rx.Component:
    chat_panel = rx.box(
        rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.heading("Hexis Chat", size="7"),
                    rx.text("Memory-aware conversation with tool access.", color="var(--muted)"),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                rx.button(
                    "Clear",
                    on_click=AppState.clear_chat,
                    variant="outline",
                    size="1",
                    is_disabled=~AppState.has_chat_history,
                ),
                align="center",
                width="100%",
            ),
            rx.box(
                rx.cond(
                    AppState.has_chat_history,
                    rx.scroll_area(
                        rx.vstack(
                            rx.foreach(AppState.chat_history, message_bubble),
                            spacing="4",
                            align="stretch",
                            width="100%",
                        ),
                        scrollbars="vertical",
                        type="auto",
                    ),
                    rx.center(
                        rx.vstack(
                            rx.heading("Start the thread", size="5"),
                            rx.text(
                                "Ask a question, load context, or give the agent a task.",
                                color="var(--muted)",
                            ),
                            spacing="2",
                        ),
                        height="100%",
                    ),
                ),
                height="440px",
                padding="16px",
                border_radius="20px",
                background="var(--panel)",
                border=f"1px solid var(--border)",
                width="100%",
            ),
            rx.box(
                rx.vstack(
                    rx.text_area(
                        value=AppState.chat_input,
                        on_change=AppState.set_chat_input,
                        placeholder="Type your message…",
                        min_height="96px",
                        max_height="240px",
                        auto_height=True,
                        rows="1",
                        variant="soft",
                        background_color="var(--panel-muted)",
                        color="var(--ink)",
                        is_disabled=AppState.ingest_running,
                    ),
                    rx.divider(),
                    rx.hstack(
                        rx.text(
                            "Shift+Enter for newline",
                            color="var(--muted)",
                            font_size="0.8rem",
                        ),
                        rx.spacer(),
                        rx.button(
                            rx.icon(tag="send", size=16),
                            "Send",
                            on_click=AppState.send_chat,
                            is_loading=AppState.chat_busy,
                            is_disabled=AppState.ingest_running,
                            color_scheme="indigo",
                            size="3",
                        ),
                        align="center",
                        width="100%",
                    ),
                    spacing="3",
                ),
                padding="18px",
                border_radius="18px",
                background="var(--panel-muted)",
                border=f"1px solid var(--border)",
                box_shadow="0 8px 24px rgba(24, 28, 38, 0.08)",
                width="100%",
            ),
            rx.cond(
                AppState.chat_error != "",
                rx.box(
                    rx.text(AppState.chat_error, color="#7f1d1d", font_weight="600"),
                    background="#fee2e2",
                    border="1px solid #fecaca",
                    padding="10px 12px",
                    border_radius="12px",
                    width="100%",
                ),
                rx.box(),
            ),
            spacing="4",
            width="100%",
        ),
        padding="24px",
        border_radius="18px",
        background="var(--card)",
        border=f"1px solid var(--border)",
        box_shadow="0 20px 50px rgba(24, 28, 38, 0.08)",
        class_name="fade-up",
        flex="1.5",
    )

    ingest_panel = rx.vstack(
        rx.heading("Ingestion", size="6"),
        rx.text("Stream ingestion output in real time. Cancel to stop.", color="var(--muted)"),
        labeled_field(
            "File or directory path",
            rx.input(value=AppState.ingest_path, on_change=AppState.set_ingest_path),
        ),
        rx.checkbox(
            "Recursive directory scan",
            is_checked=AppState.ingest_recursive,
            on_change=AppState.set_ingest_recursive,
        ),
        rx.hstack(
            rx.button(
                "Start ingestion",
                on_click=AppState.start_ingestion,
                is_loading=AppState.ingest_running,
                is_disabled=AppState.ingest_path == "",
                color_scheme="orange",
            ),
            rx.button(
                "Cancel",
                on_click=AppState.cancel_ingestion,
                variant="outline",
                is_disabled=~AppState.ingest_running,
            ),
            spacing="3",
        ),
        rx.box(
            rx.cond(
                AppState.has_ingest_log,
                rx.vstack(
                    rx.foreach(
                        AppState.ingest_log,
                        lambda line: rx.text(line, white_space="pre-wrap"),
                    ),
                    spacing="1",
                    align="start",
                    width="100%",
                ),
                rx.text("No ingestion output yet.", color="#93c5b4"),
            ),
            height="260px",
            overflow="auto",
            padding="12px",
            border_radius="14px",
            background="#0f0e0c",
            color="#d7f5e8",
            border="1px solid #201b14",
            font_family="'IBM Plex Mono', monospace",
            font_size="0.8rem",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )

    side_panel = rx.box(
        rx.vstack(
            rx.text("Actions", font_weight="600"),
            rx.select(
                ["chat", "ingest"],
                value=AppState.active_panel,
                on_change=AppState.set_active_panel,
                is_disabled=AppState.ingest_running,
            ),
            rx.cond(AppState.active_panel == "ingest", ingest_panel, rx.box()),
            spacing="3",
            width="100%",
        ),
        padding="24px",
        border_radius="18px",
        background="var(--card)",
        border=f"1px solid var(--border)",
        box_shadow="0 20px 50px rgba(22, 18, 12, 0.08)",
        class_name="fade-up",
        flex="1",
    )

    return rx.flex(
        chat_panel,
        side_panel,
        class_name="responsive-flex",
        width="100%",
        id="chat-view",
    )


def index() -> rx.Component:
    return rx.box(
        rx.el.style(GLOBAL_CSS),
        rx.box(
            width="360px",
            height="360px",
            background="radial-gradient(circle, #0f766e 0%, rgba(15,118,110,0.0) 70%)",
            top="-120px",
            right="-120px",
            class_name="soft-glow",
        ),
        rx.box(
            width="280px",
            height="280px",
            background="radial-gradient(circle, #c2410c 0%, rgba(194,65,12,0.0) 70%)",
            bottom="-120px",
            left="-80px",
            class_name="soft-glow",
        ),
        rx.container(
            rx.hstack(
                rx.vstack(
                    rx.heading("Hexis", size="8"),
                    rx.text("Cognitive memory engine", color="var(--muted)"),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                status_badge(),
                rx.cond(
                    AppState.force_init_view & AppState.initialized,
                    rx.button("Back to chat", on_click=AppState.show_chat, variant="outline"),
                    rx.cond(
                        AppState.initialized,
                        rx.button("Reconfigure", on_click=AppState.show_init, variant="outline"),
                        rx.box(),
                    ),
                ),
                align="center",
                width="100%",
            ),
            rx.cond(
                AppState.terminated,
                rx.box(
                    rx.heading("Agent terminated", size="7"),
                    rx.text("The agent has ended its session. Reset the database to start again."),
                    padding="24px",
                    border_radius="18px",
                    background="var(--card)",
                    border=f"1px solid var(--border)",
                ),
                rx.cond(
                    AppState.force_init_view,
                    init_view(),
                    rx.cond(AppState.initialized, chat_view(), init_view()),
                ),
            ),
            max_width="1200px",
            padding="32px",
            position="relative",
            z_index="1",
        ),
        min_height="100vh",
        position="relative",
        padding_bottom="60px",
    )


app = rx.App(head_components=[rx.script(src="/port_discovery.js")])
app.add_page(index, on_load=AppState.load_status)
