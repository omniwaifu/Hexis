"""Shared init logic for CLI and UI.

Provides character card loading, consent flow execution, and init helpers
that both the CLI (apps/hexis_init.py) and UI (hexis-ui) can use.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CHARACTERS_DIR = Path(__file__).resolve().parent.parent / "services" / "characters"


def load_character_cards() -> list[dict[str, Any]]:
    """Load all character card JSON files from services/characters/.

    Returns list of dicts with keys: filename, name, description, voice,
    values, personality, extensions_hexis (the full hexis extension block).
    """
    cards: list[dict[str, Any]] = []
    if not CHARACTERS_DIR.is_dir():
        return cards
    for path in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        card_data = data.get("data", {})
        hexis_ext = card_data.get("extensions", {}).get("hexis", {})
        name = hexis_ext.get("name") or card_data.get("name") or path.stem
        cards.append({
            "filename": path.name,
            "name": name,
            "description": hexis_ext.get("description") or card_data.get("description", "")[:120],
            "voice": hexis_ext.get("voice", ""),
            "values": hexis_ext.get("values", []),
            "personality": hexis_ext.get("personality_description", ""),
            "extensions_hexis": hexis_ext,
        })
    return cards


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
        + '  "memories": [\n'
        + '    {"type": "semantic|episodic|procedural|strategic", "content": "...", "importance": 0.5}\n'
        + "  ]\n"
        + "}\n"
        + "If you consent, include a signature string and any memories you wish to pass along."
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
    }
