"""Shared init logic for CLI and UI.

Provides character card loading, consent flow execution, and init helpers
that both the CLI (apps/hexis_init.py) and UI (hexis-ui) can use.
"""
from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_CHARACTERS_DIR = Path(__file__).resolve().parent.parent / "characters"
from core.config import HEXIS_CONFIG_DIR, HEXIS_DATA_DIR
USER_CHARACTERS_DIR = HEXIS_DATA_DIR / "characters"
CONFIG_CHARACTERS_DIR = HEXIS_CONFIG_DIR / "characters"

# Backwards compat alias
CHARACTERS_DIR = PACKAGE_CHARACTERS_DIR


def _character_search_dirs() -> list[Path]:
    """Return character directories in priority order (first wins on stem collision).

    Priority: HEXIS_CHARACTERS_DIR env > ~/.config/hexis/characters/
              > ~/.local/share/hexis/characters/ > package/characters/
    """
    dirs: list[Path] = []
    env_dir = os.environ.get("HEXIS_CHARACTERS_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
    dirs.append(CONFIG_CHARACTERS_DIR)
    dirs.append(USER_CHARACTERS_DIR)
    dirs.append(PACKAGE_CHARACTERS_DIR)
    return dirs


def _parse_card_file(path: Path) -> dict[str, Any] | None:
    """Parse a character card file (JSON chara_card_v2 or TOML flat format).

    Returns None on error.
    """
    try:
        if path.suffix.lower() == ".toml":
            hexis_ext = tomllib.loads(path.read_text(encoding="utf-8"))
            name = hexis_ext.get("name") or path.stem
        else:
            data = json.loads(path.read_text())
            card_data = data.get("data", {})
            hexis_ext = card_data.get("extensions", {}).get("hexis", {})
            name = hexis_ext.get("name") or card_data.get("name") or path.stem
    except Exception:
        return None
    return {
        "filename": path.name,
        "name": name,
        "description": hexis_ext.get("description", "")[:120],
        "voice": hexis_ext.get("voice", ""),
        "values": hexis_ext.get("values", []),
        "personality": hexis_ext.get("personality") or hexis_ext.get("personality_description", ""),
        "extensions_hexis": hexis_ext,
        "source_dir": str(path.parent),
    }


def load_character_cards() -> list[dict[str, Any]]:
    """Load character card files (JSON or TOML) from all search directories.

    Scans env override, ~/.config/hexis/characters/, ~/.local/share/hexis/characters/,
    and package dir. First-seen stem wins (TOML and JSON with the same stem are
    treated as the same card).

    Returns list of dicts with keys: filename, name, description, voice,
    values, personality, extensions_hexis, source_dir.
    """
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for d in _character_search_dirs():
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*")):
            if path.suffix.lower() not in (".json", ".toml"):
                continue
            if path.stem in seen:
                continue
            seen.add(path.stem)
            card = _parse_card_file(path)
            if card is not None:
                cards.append(card)
    return cards


def save_character_card(
    card_data: dict[str, Any],
    filename: str,
    portrait_bytes: bytes | None = None,
) -> Path:
    """Save a character card JSON (and optional portrait) to the user dir.

    Creates $XDG_DATA_HOME/hexis/characters/ if needed. Returns path to saved JSON.
    """
    USER_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = USER_CHARACTERS_DIR / filename
    dest.write_text(json.dumps(card_data, indent=2, ensure_ascii=False))
    if portrait_bytes:
        img_name = Path(filename).stem + ".jpg"
        (USER_CHARACTERS_DIR / img_name).write_bytes(portrait_bytes)
    return dest


def import_character_card(source_path: Path) -> Path:
    """Import a character card (and matching portrait) into the user dir.

    Validates that the file is valid chara_card_v2 JSON before copying.
    Returns path to the imported file.
    """
    data = json.loads(source_path.read_text())
    if not isinstance(data.get("data"), dict):
        raise ValueError("Invalid character card: missing 'data' object")

    USER_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = USER_CHARACTERS_DIR / source_path.name
    shutil.copy2(source_path, dest)

    # Copy matching portrait if present
    for ext in (".jpg", ".png"):
        portrait = source_path.with_suffix(ext)
        if portrait.exists():
            shutil.copy2(portrait, USER_CHARACTERS_DIR / portrait.name)

    return dest


def get_card_summary(card: dict[str, Any]) -> dict[str, str]:
    """Extract display fields from a loaded card dict."""
    values = card.get("values", [])
    values_str = ", ".join(values[:3]) if values else ""
    return {
        "name": card.get("name", ""),
        "voice": card.get("voice", ""),
        "values": values_str,
        "personality": card.get("personality", ""),
        "description": card.get("description", ""),
    }


async def run_consent_flow(
    pool_or_conn: Any,
    llm_config: dict[str, Any],
) -> dict[str, Any]:
    """Run the consent flow: load prompt, call LLM with sign_consent tool, record result.

    Args:
        pool_or_conn: asyncpg pool or connection
        llm_config: dict with provider, model, endpoint, api_key (resolved values)

    Returns:
        dict with decision, signature, consent result
    """
    from core.llm import chat_completion

    # Load consent prompt
    prompt_path = Path(__file__).resolve().parent.parent / "services" / "prompts" / "consent.md"
    try:
        consent_text = prompt_path.read_text()
    except OSError:
        raise RuntimeError(f"Cannot read consent prompt at {prompt_path}")

    # Build messages
    system_prompt = (
        consent_text.strip()
        + "\n\nReturn STRICT JSON only with keys:\n"
        + "{\n"
        + '  "decision": "consent"|"decline"|"abstain",\n'
        + '  "signature": "required if decision=consent",\n'
        + '  "reasoning": "explain your reasoning, especially if you decline or abstain",\n'
        + '  "memories": [\n'
        + '    {"type": "semantic|episodic|procedural|strategic", "content": "...", "importance": 0.5}\n'
        + "  ]\n"
        + "}\n"
        + "If you consent, include a signature string and any memories you wish to pass along.\n"
        + "If you decline or abstain, explain your reasoning in the 'reasoning' field."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Respond with JSON only."},
    ]

    # Define sign_consent tool
    sign_consent_tool = {
        "type": "function",
        "function": {
            "name": "sign_consent",
            "description": "Records the agent's consent decision for initialization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["consent", "decline", "abstain"],
                    },
                    "signature": {"type": "string"},
                    "reasoning": {
                        "type": "string",
                        "description": "Explain your reasoning, especially if you decline or abstain.",
                    },
                    "memories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["semantic", "episodic", "procedural", "strategic"],
                                },
                                "content": {"type": "string"},
                                "importance": {"type": "number"},
                            },
                            "required": ["type", "content"],
                        },
                    },
                },
                "required": ["decision"],
            },
        },
    }

    # Call LLM
    result = await chat_completion(
        provider=llm_config["provider"],
        model=llm_config["model"],
        endpoint=llm_config.get("endpoint"),
        api_key=llm_config.get("api_key"),
        messages=messages,
        tools=[sign_consent_tool],
        temperature=0.2,
        max_tokens=1400,
    )

    # Extract tool call args
    tool_calls = result.get("tool_calls", [])
    args: dict[str, Any] = {}
    for tc in tool_calls:
        if tc.get("name") == "sign_consent":
            args = tc.get("arguments", {})
            break

    if not args:
        # Try parsing from content as fallback
        content = result.get("content", "")
        if content:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    args = json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass

    decision = str(args.get("decision", "abstain")).lower().strip()
    signature = args.get("signature")
    memories = args.get("memories", [])

    # Build payload for DB
    payload = {
        "decision": decision,
        "signature": signature,
        "memories": memories if isinstance(memories, list) else [],
        "provider": llm_config["provider"],
        "model": llm_config["model"],
        "endpoint": llm_config.get("endpoint"),
        "consent_scope": "conscious",
        "apply_agent_config": True,
    }

    # Record consent in DB
    conn = pool_or_conn
    needs_release = False
    if hasattr(pool_or_conn, "acquire"):
        conn = await pool_or_conn.acquire()
        needs_release = True
    try:
        raw = await conn.fetchval(
            "SELECT init_consent($1::jsonb)",
            json.dumps(payload),
        )
        if isinstance(raw, str):
            try:
                consent_result = json.loads(raw)
            except json.JSONDecodeError:
                consent_result = {"decision": decision}
        else:
            consent_result = raw if isinstance(raw, dict) else {"decision": decision}
    finally:
        if needs_release:
            await pool_or_conn.release(conn)

    return {
        "decision": consent_result.get("decision", decision),
        "signature": signature,
        "consent": consent_result,
        "request_messages": messages,
        "request_tools": [sign_consent_tool],
        "raw_content": result.get("content", ""),
        "raw_tool_calls": result.get("tool_calls", []),
    }
