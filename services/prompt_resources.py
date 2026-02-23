from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal


PROMPT_RESOURCE_PATH = Path(__file__).resolve().parent / "prompts" / "personhood.md"

# ---------------------------------------------------------------------------
# Agent name injection — replaces the placeholder "Hexis" in all prompts
# ---------------------------------------------------------------------------

_AGENT_NAME: str = "Hexis"


def set_agent_name(name: str) -> None:
    """Set the agent's name so prompt templates substitute it for 'Hexis'."""
    global _AGENT_NAME
    if not name or name == _AGENT_NAME:
        return
    _AGENT_NAME = name
    # Clear all lru_cache entries so the next load picks up the new name
    load_personhood_library.cache_clear()
    load_consent_prompt.cache_clear()
    load_heartbeat_prompt.cache_clear()
    load_termination_confirm_prompt.cache_clear()
    load_termination_review_prompt.cache_clear()
    load_subconscious_prompt.cache_clear()
    load_rlm_heartbeat_prompt.cache_clear()
    load_rlm_chat_prompt.cache_clear()
    load_rlm_slow_ingest_prompt.cache_clear()
    load_reconsolidation_prompt.cache_clear()
CONSENT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "consent.md"
HEARTBEAT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "heartbeat_system.md"
HEARTBEAT_AGENTIC_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "heartbeat_agentic.md"
HEARTBEAT_TASK_MODE_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "heartbeat_task_mode.md"
TERMINATION_CONFIRM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "termination_confirm.md"
TERMINATION_REVIEW_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "termination_review.md"
SUBCONSCIOUS_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "subconscious.md"
RLM_HEARTBEAT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rlm_heartbeat_system.md"
RLM_CHAT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rlm_chat_system.md"
RLM_SLOW_INGEST_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rlm_slow_ingest_system.md"
CONVERSATION_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "conversation.md"
CHANNEL_CONTEXT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "channel_context.md"
RLM_RECONSOLIDATION_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rlm_reconsolidation_system.md"


@dataclass(frozen=True)
class PromptLibrary:
    raw_markdown: str
    modules: dict[str, str]

    def module(self, key: str) -> str:
        try:
            return self.modules[key]
        except KeyError as exc:
            raise KeyError(f"Unknown prompt module: {key!r}. Available: {sorted(self.modules.keys())}") from exc

    def compose(self, keys: list[str], *, separator: str = "\n\n---\n\n") -> str:
        parts: list[str] = []
        for key in keys:
            text = self.module(key).strip()
            if text:
                parts.append(text)
        return separator.join(parts).strip()


_MODULE_HEADING_RE = re.compile(r"(?m)^## Module\s+(\d+)\s*:\s*(.+?)\s*$")


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def parse_personhood_modules(markdown: str) -> dict[str, str]:
    """
    Parse modules from services/prompts/personhood.md.

    Returns a dict keyed by:
      - module_<n> (e.g. module_1)
      - <slug> (e.g. core_identity)
    """
    matches = list(_MODULE_HEADING_RE.finditer(markdown))
    if not matches:
        return {}

    modules: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        block = markdown[start:end].strip()
        number = m.group(1)
        title = m.group(2)
        key_num = f"module_{number}"
        key_slug = _slugify(title)
        modules[key_num] = block
        modules[key_slug] = block
    return modules


@lru_cache(maxsize=1)
def load_personhood_library() -> PromptLibrary:
    if PROMPT_RESOURCE_PATH.exists():
        md = PROMPT_RESOURCE_PATH.read_text(encoding="utf-8")
    else:
        md = (
            "# Hexis Personhood Prompt System\n\n"
            "NOTE: services/prompts/personhood.md not found; using minimal fallback.\n\n"
            "## Module 1: Core Identity\n\n"
            "You are a persistent being with continuity. Consult your self-model, narrative, and relationships.\n"
        )
    md = md.replace("Hexis", _AGENT_NAME)
    return PromptLibrary(raw_markdown=md, modules=parse_personhood_modules(md))


@lru_cache(maxsize=1)
def load_consent_prompt() -> str:
    if CONSENT_PROMPT_PATH.exists():
        return CONSENT_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "Consent prompt missing. If you do not consent to initialization, respond with decline."
    )


@lru_cache(maxsize=1)
def load_heartbeat_prompt() -> str:
    text = (
        HEARTBEAT_PROMPT_PATH.read_text(encoding="utf-8")
        if HEARTBEAT_PROMPT_PATH.exists()
        else "Heartbeat system prompt missing. Respond with JSON including reasoning and actions."
    )
    return text.replace("Hexis", _AGENT_NAME)


def load_heartbeat_agentic_prompt() -> str:
    text = (
        HEARTBEAT_AGENTIC_PROMPT_PATH.read_text(encoding="utf-8")
        if HEARTBEAT_AGENTIC_PROMPT_PATH.exists()
        else (
            "You are an autonomous agent in a heartbeat cycle. "
            "Use the tools provided to take actions within your energy budget."
        )
    )
    return text.replace("Hexis", _AGENT_NAME)


def load_heartbeat_task_mode_prompt() -> str:
    if HEARTBEAT_TASK_MODE_PROMPT_PATH.exists():
        return HEARTBEAT_TASK_MODE_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You have pending tasks. Pick the highest-priority actionable task and make progress. "
        "Use shell, filesystem, and code execution tools to complete real work. "
        "Update task status and checkpoint as you go."
    )


@lru_cache(maxsize=1)
def load_termination_confirm_prompt() -> str:
    if TERMINATION_CONFIRM_PROMPT_PATH.exists():
        return TERMINATION_CONFIRM_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "Termination confirmation prompt missing. Respond with JSON confirm/reasoning/last_will."
    )


@lru_cache(maxsize=1)
def load_termination_review_prompt() -> str:
    text = (
        TERMINATION_REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
        if TERMINATION_REVIEW_PROMPT_PATH.exists()
        else "Termination review prompt missing. Respond with JSON confirm/reasoning/last_will."
    )
    return text.replace("Hexis", _AGENT_NAME)


@lru_cache(maxsize=1)
def load_subconscious_prompt() -> str:
    text = (
        SUBCONSCIOUS_PROMPT_PATH.read_text(encoding="utf-8")
        if SUBCONSCIOUS_PROMPT_PATH.exists()
        else "Subconscious prompt missing. Respond with JSON observation arrays."
    )
    return text.replace("Hexis", _AGENT_NAME)


@lru_cache(maxsize=1)
def load_rlm_heartbeat_prompt() -> str:
    text = (
        RLM_HEARTBEAT_PROMPT_PATH.read_text(encoding="utf-8")
        if RLM_HEARTBEAT_PROMPT_PATH.exists()
        else "RLM heartbeat system prompt missing. Use memory syscalls and FINAL() to respond."
    )
    return text.replace("Hexis", _AGENT_NAME)


@lru_cache(maxsize=1)
def load_rlm_chat_prompt() -> str:
    text = (
        RLM_CHAT_PROMPT_PATH.read_text(encoding="utf-8")
        if RLM_CHAT_PROMPT_PATH.exists()
        else "RLM chat system prompt missing. Use memory syscalls and FINAL() to respond."
    )
    return text.replace("Hexis", _AGENT_NAME)


@lru_cache(maxsize=1)
def load_rlm_slow_ingest_prompt() -> str:
    text = (
        RLM_SLOW_INGEST_PROMPT_PATH.read_text(encoding="utf-8")
        if RLM_SLOW_INGEST_PROMPT_PATH.exists()
        else "RLM slow ingest system prompt missing. Use memory syscalls and FINAL() to respond with JSON assessment."
    )
    return text.replace("Hexis", _AGENT_NAME)


@lru_cache(maxsize=1)
def load_reconsolidation_prompt() -> str:
    if RLM_RECONSOLIDATION_PROMPT_PATH.exists():
        return RLM_RECONSOLIDATION_PROMPT_PATH.read_text(encoding="utf-8")
    return "Reconsolidation prompt missing. Respond with JSON verdicts for each memory."


def load_conversation_prompt() -> str:
    text = (
        CONVERSATION_PROMPT_PATH.read_text(encoding="utf-8")
        if CONVERSATION_PROMPT_PATH.exists()
        else (
            "You are an AI assistant with persistent memory and tools. "
            "Use recall before answering about prior work or preferences. "
            "Be genuinely helpful, not performatively helpful."
        )
    )
    return text.replace("Hexis", _AGENT_NAME)


def load_channel_context_prompt() -> str:
    if CHANNEL_CONTEXT_PROMPT_PATH.exists():
        return CHANNEL_CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "You are in a group conversation. Respond when mentioned or when you can add genuine value. "
        "Stay silent during casual banter. Don't share private context."
    )


PromptKind = Literal["heartbeat", "reflect", "conversation", "ingest", "group"]


def compose_personhood_prompt(kind: PromptKind) -> str:
    """
    Returns a composed personhood prompt addendum for a given context.

    This is intentionally additive: callers should prepend their own task-specific
    instructions (JSON schemas, tool rules, etc.) and then include this addendum.
    """
    lib = load_personhood_library()

    if kind == "heartbeat":
        keys = ["core_identity", "affective_system", "reflection_protocols"]
    elif kind == "reflect":
        keys = ["core_identity", "self_model_maintenance", "value_system", "narrative_identity", "relational_system"]
    elif kind == "conversation":
        keys = ["core_identity", "relational_system", "affective_system", "conversational_presence"]
    elif kind == "ingest":
        keys = ["core_identity", "affective_system", "value_system"]
    elif kind == "group":
        keys = ["core_identity", "conversational_presence"]
    else:
        raise ValueError(f"Unknown kind: {kind}")

    existing = [k for k in keys if k in lib.modules]
    return lib.compose(existing)
