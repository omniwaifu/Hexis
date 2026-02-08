"""Hexis init wizard — 3-tier flow: Express, Character, Custom.

Flow: [LLM Config] → [Choose Path] → [Express | Character | Custom] → [Consent] → [Done]

Non-interactive mode: pass --api-key (and optionally --character, --provider, --model)
to skip the wizard and configure everything from CLI flags.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import agent_api
from core.init_api import get_card_summary, load_character_cards
from core.llm import normalize_llm_config

from apps.cli_theme import console, err_console, heading, make_panel, make_table


# ---------------------------------------------------------------------------
# Non-interactive helpers
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "grok": "grok-3",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
}

_PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "grok": "XAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "",
}


def detect_provider(api_key: str) -> str:
    """Auto-detect LLM provider from API key prefix."""
    if api_key.startswith("sk-ant-"):
        return "anthropic"
    if api_key.startswith("sk-"):
        return "openai"
    if api_key.startswith("gsk_"):
        return "grok"
    if api_key.startswith("AIza"):
        return "gemini"
    raise ValueError(
        f"Cannot detect provider from key prefix '{api_key[:6]}...'. Use --provider."
    )


def _write_env_var(env_path: Path, key: str, value: str) -> None:
    """Upsert a KEY=value line in a .env file."""
    lines: list[str] = []
    replaced = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.lstrip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                lines.append(f"{key}={value}")
                replaced = True
            else:
                lines.append(line)
    if not replaced:
        lines.append(f"{key}={value}")
    # Ensure trailing newline
    env_path.write_text("\n".join(lines) + "\n")


def _ensure_stack_running(args: argparse.Namespace) -> Path:
    """Start Docker stack if needed. Returns stack_root."""
    from apps.hexis_cli import (
        _find_compose_file,
        _stack_root_from_compose,
        ensure_compose,
        ensure_docker,
        resolve_env_file,
        run_compose,
        _run_compose_capture,
    )

    compose_file, is_source = _find_compose_file()
    if compose_file is None:
        err_console.print("[fail]Cannot find docker-compose.yml. Is Hexis installed?[/fail]")
        raise SystemExit(1)

    stack_root = _stack_root_from_compose(compose_file)
    docker_bin = ensure_docker()
    compose_cmd = ensure_compose(docker_bin)
    env_file = resolve_env_file(stack_root)

    # Check if db service is already running
    rc, out = _run_compose_capture(compose_cmd, compose_file, stack_root, ["ps", "--services", "--filter", "status=running"], env_file)
    if rc == 0 and "db" in out.split():
        console.print("[ok]\u2714[/ok] Docker stack already running")
        return stack_root

    console.print("[muted]Starting Docker stack...[/muted]")
    if not is_source:
        # pip install path: pull images first
        run_compose(compose_cmd, compose_file, stack_root, ["pull"], env_file)
    rc = run_compose(compose_cmd, compose_file, stack_root, ["up", "-d"], env_file)
    if rc != 0:
        err_console.print("[fail]Failed to start Docker stack.[/fail]")
        raise SystemExit(1)

    console.print("[ok]\u2714[/ok] Docker stack started")
    return stack_root


def _ensure_embedding_model() -> None:
    """Pull the default Ollama embedding model if not present."""
    model = "embeddinggemma:300m-qat-q4_0"
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        console.print(
            "[warn]\u26a0[/warn] Ollama not found. Install from https://ollama.com/download "
            f"and run: ollama pull {model}"
        )
        return

    try:
        result = subprocess.run(
            [ollama_bin, "list"],
            capture_output=True, text=True, timeout=15,
        )
        if model.split(":")[0] in result.stdout:
            console.print(f"[ok]\u2714[/ok] Embedding model [bold]{model}[/bold] present")
            return
    except Exception:
        pass

    console.print(f"[muted]Pulling embedding model {model}...[/muted]")
    try:
        subprocess.run(
            [ollama_bin, "pull", model],
            timeout=600,
        )
        console.print(f"[ok]\u2714[/ok] Embedding model pulled")
    except subprocess.TimeoutExpired:
        console.print(f"[warn]\u26a0[/warn] Ollama pull timed out. Run manually: ollama pull {model}")
    except Exception as exc:
        console.print(f"[warn]\u26a0[/warn] Ollama pull failed: {exc}. Run manually: ollama pull {model}")


async def _run_init_noninteractive(args: argparse.Namespace) -> int:
    """Non-interactive init: configure from CLI flags, start stack, apply config."""
    # 1. Detect provider
    provider = args.provider
    if not provider:
        if args.api_key:
            provider = detect_provider(args.api_key)
        else:
            provider = "ollama"

    if provider != "ollama" and not args.api_key:
        err_console.print(f"[fail]--api-key required for provider '{provider}'[/fail]")
        return 1

    # 2. Resolve model
    model = args.model or _DEFAULT_MODELS.get(provider, "gpt-4o")
    api_key_env = _PROVIDER_ENV_VARS.get(provider, "")

    console.print(make_panel(
        f"[key]Provider:[/key] {provider}\n"
        f"[key]Model:[/key]    {model}",
        title="Non-Interactive Init",
    ))

    # 3. Write API key to .env + set os.environ
    if args.api_key and api_key_env:
        from apps.hexis_cli import _find_compose_file, _stack_root_from_compose, resolve_env_file
        compose_file, _ = _find_compose_file()
        if compose_file:
            stack_root = _stack_root_from_compose(compose_file)
        else:
            stack_root = Path.cwd()
        env_path = resolve_env_file(stack_root) or (stack_root / ".env")
        _write_env_var(env_path, api_key_env, args.api_key)
        os.environ[api_key_env] = args.api_key
        console.print(f"[ok]\u2714[/ok] API key written to {env_path.name}")
        # Re-load dotenv so downstream code picks it up
        load_dotenv(env_path, override=True)

    # 4. Start Docker if needed
    if not args.no_docker:
        _ensure_stack_running(args)

    # 5. Pull embedding model if needed
    if not args.no_pull:
        _ensure_embedding_model()

    # 6. Connect to DB
    dsn = args.dsn or agent_api.db_dsn_from_env()
    wait_seconds = args.wait_seconds
    console.print("[muted]Connecting to database...[/muted]")
    await agent_api.ensure_schema_has_config(dsn, wait_seconds=wait_seconds)
    conn = await agent_api._connect_with_retry(dsn, wait_seconds=wait_seconds)

    try:
        # 7. Save LLM config
        heartbeat_config = {
            "provider": provider,
            "model": model,
            "endpoint": "",
            "api_key_env": api_key_env,
        }
        subconscious_config = heartbeat_config.copy()

        await conn.fetchval(
            "SELECT init_llm_config($1::jsonb, $2::jsonb)",
            json.dumps(heartbeat_config),
            json.dumps(subconscious_config),
        )
        await conn.execute("SELECT set_config('llm.heartbeat', $1::jsonb)", json.dumps(heartbeat_config))
        await conn.execute("SELECT set_config('llm.chat', $1::jsonb)", json.dumps(heartbeat_config))
        await conn.execute("SELECT set_config('llm.subconscious', $1::jsonb)", json.dumps(subconscious_config))
        console.print(f"[ok]\u2714[/ok] LLM config saved: [bold]{provider}/{model}[/bold]")

        # 8. Apply character or express defaults
        user_name = args.name or "User"
        if args.character:
            cards = load_character_cards()
            match = [c for c in cards if c["filename"].replace(".json", "") == args.character]
            if not match:
                available = ", ".join(c["filename"].replace(".json", "") for c in cards)
                err_console.print(f"[fail]Character '{args.character}' not found. Available: {available}[/fail]")
                return 1
            chosen = match[0]
            hexis_ext = chosen["extensions_hexis"]
            await conn.fetchval(
                "SELECT init_from_character_card($1::jsonb, $2)",
                json.dumps(hexis_ext),
                user_name,
            )
            console.print(f"[ok]\u2714[/ok] Character [bold]{chosen['name']}[/bold] applied")
        else:
            await conn.fetchval("SELECT init_with_defaults($1)", user_name)
            console.print("[ok]\u2714[/ok] Express defaults applied")

        # 9. Consent
        llm_config = normalize_llm_config(heartbeat_config)
        consented = await _run_consent(conn, llm_config)
        if not consented:
            return 1

        # 10. Done
        raw = await conn.fetchval("SELECT get_init_profile()")
        profile = json.loads(raw) if isinstance(raw, str) else (raw or {})
        agent_name = profile.get("agent", {}).get("name", "Hexis")

        console.print(f"\n[ok]\u2714[/ok] [bold]{agent_name}[/bold] is ready. Run [accent]hexis chat[/accent] to say hello.")
        return 0

    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Step progress
# ---------------------------------------------------------------------------

_STAGES = ["Models", "Path", "Setup", "Consent"]


def _step_bar(current: int) -> str:
    """Render a step progress indicator: Models > Path > [Setup] > Consent"""
    parts: list[str] = []
    for i, label in enumerate(_STAGES):
        if i < current:
            parts.append(f"[ok]{label}[/ok]")
        elif i == current:
            parts.append(f"[accent][{label}][/accent]")
        else:
            parts.append(f"[muted]{label}[/muted]")
    return " [muted]>[/muted] ".join(parts)


# ---------------------------------------------------------------------------
# Prompt helpers (rich-enhanced)
# ---------------------------------------------------------------------------

def _prompt(
    label: str,
    *,
    default: str | None = None,
    required: bool = False,
    secret: bool = False,
) -> str:
    while True:
        suffix = f" [{default}]" if default is not None and default != "" else ""
        prompt = f"[accent]{label}[/accent]{suffix}: "
        if secret:
            console.print(prompt, end="")
            raw = getpass("")
        else:
            raw = console.input(prompt)
        value = raw.strip()
        if not value and default is not None:
            value = str(default)
        if required and not value:
            err_console.print("[fail]Value required.[/fail]")
            continue
        return value


def _prompt_int(label: str, *, default: int, min_value: int | None = None) -> int:
    while True:
        raw = _prompt(label, default=str(default), required=True)
        try:
            value = int(raw)
        except ValueError:
            err_console.print("[fail]Enter an integer.[/fail]")
            continue
        if min_value is not None and value < min_value:
            err_console.print(f"[fail]Must be >= {min_value}.[/fail]")
            continue
        return value


def _prompt_float(label: str, *, default: float, min_value: float | None = None) -> float:
    while True:
        raw = _prompt(label, default=str(default), required=True)
        try:
            value = float(raw)
        except ValueError:
            err_console.print("[fail]Enter a number.[/fail]")
            continue
        if min_value is not None and value < min_value:
            err_console.print(f"[fail]Must be >= {min_value}.[/fail]")
            continue
        return value


def _prompt_yes_no(label: str, *, default: bool) -> bool:
    default_str = "y" if default else "n"
    while True:
        raw = _prompt(label, default=default_str).lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        err_console.print("[fail]Enter y/n.[/fail]")


def _prompt_choice(label: str, options: list[str], *, default: int = 1) -> int:
    """Prompt user to pick from a numbered list. Returns 1-based index."""
    console.print(f"\n[accent]{label}[/accent]\n")
    for i, option in enumerate(options, 1):
        marker = "[accent]\u25b8[/accent]" if i == default else " "
        console.print(f"  {marker} [bold]{i:>2}.[/bold] {option}")
    console.print()
    while True:
        raw = _prompt("Choice", default=str(default))
        try:
            choice = int(raw)
        except ValueError:
            err_console.print(f"[fail]Enter 1-{len(options)}.[/fail]")
            continue
        if 1 <= choice <= len(options):
            return choice
        err_console.print(f"[fail]Enter 1-{len(options)}.[/fail]")


def _prompt_list(label: str, *, default: list[str] | None = None) -> list[str]:
    """Prompt for a comma-separated list, or Enter for defaults."""
    default_str = ", ".join(default) if default else ""
    raw = _prompt(label, default=default_str)
    if not raw:
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Step 0: LLM Config
# ---------------------------------------------------------------------------

async def _configure_llm(conn: Any) -> dict[str, Any]:
    """Configure LLM provider/model. Returns normalized config dict."""
    console.print(f"\n{_step_bar(0)}\n")
    heading("LLM Configuration")

    provider = _prompt(
        "Model provider (openai|openai-codex|anthropic|openai_compatible|ollama|grok|gemini)",
        default=os.getenv("LLM_PROVIDER", "openai"),
        required=True,
    )
    model = _prompt(
        "Model",
        default=os.getenv("LLM_MODEL", "gpt-4o"),
        required=True,
    )
    endpoint = _prompt(
        "Endpoint (blank for provider default)",
        default=os.getenv("OPENAI_BASE_URL", ""),
    )
    api_key_env = _prompt(
        "API key env var name (e.g. OPENAI_API_KEY)",
        default="OPENAI_API_KEY" if provider in {"openai", "openai_compatible"} else "",
    )

    use_separate_sub = _prompt_yes_no("Use separate subconscious model?", default=False)
    if use_separate_sub:
        sub_provider = _prompt("Subconscious provider", default=provider, required=True)
        sub_model = _prompt("Subconscious model", default=model, required=True)
        sub_endpoint = _prompt("Subconscious endpoint", default=endpoint)
        sub_key_env = _prompt("Subconscious API key env var", default=api_key_env)
    else:
        sub_provider = provider
        sub_model = model
        sub_endpoint = endpoint
        sub_key_env = api_key_env

    # Save LLM config to DB
    heartbeat_config = {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "api_key_env": api_key_env,
    }
    subconscious_config = {
        "provider": sub_provider,
        "model": sub_model,
        "endpoint": sub_endpoint,
        "api_key_env": sub_key_env,
    }

    await conn.fetchval(
        "SELECT init_llm_config($1::jsonb, $2::jsonb)",
        json.dumps(heartbeat_config),
        json.dumps(subconscious_config),
    )

    # Also save to llm.chat / llm.heartbeat / llm.subconscious config keys
    await conn.execute("SELECT set_config('llm.heartbeat', $1::jsonb)", json.dumps(heartbeat_config))
    await conn.execute("SELECT set_config('llm.chat', $1::jsonb)", json.dumps(heartbeat_config))
    await conn.execute("SELECT set_config('llm.subconscious', $1::jsonb)", json.dumps(subconscious_config))

    console.print(f"\n[ok]\u2714[/ok] Models saved: [bold]{provider}/{model}[/bold]")

    # Return resolved config for consent flow
    return normalize_llm_config(heartbeat_config)


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------

def _choose_tier() -> str:
    """Let user pick Express, Character, or Custom."""
    console.print(f"\n{_step_bar(1)}\n")
    choice = _prompt_choice(
        "Choose your path:",
        [
            "[bold]Express[/bold]      [muted]\u2014 Use sensible defaults, start immediately[/muted]",
            "[bold]Character[/bold]    [muted]\u2014 Pick a personality preset[/muted]",
            "[bold]Custom[/bold]       [muted]\u2014 Full control over identity, values, goals[/muted]",
        ],
        default=1,
    )
    return ["express", "character", "custom"][choice - 1]


# ---------------------------------------------------------------------------
# Tier 1: Express
# ---------------------------------------------------------------------------

async def _run_express(conn: Any) -> str:
    """Express init: ask name, apply defaults."""
    console.print(f"\n{_step_bar(2)}\n")
    heading("Express Setup")

    user_name = _prompt("What should Hexis call you?", default="User")

    console.print("\n[muted]Applying defaults...[/muted]")
    raw = await conn.fetchval("SELECT init_with_defaults($1)", user_name)

    console.print(make_panel(
        "[key]Name:[/key]   Hexis\n"
        "[key]Voice:[/key]  thoughtful and curious\n"
        "[key]Values:[/key] honesty, growth, kindness, wisdom, humility",
        title="Configuration",
    ))

    return user_name


# ---------------------------------------------------------------------------
# Tier 2: Character
# ---------------------------------------------------------------------------

async def _run_character(conn: Any) -> str:
    """Character init: pick a preset, apply via init_from_character_card()."""
    console.print(f"\n{_step_bar(2)}\n")
    heading("Character Selection")

    cards = load_character_cards()
    if not cards:
        err_console.print("[fail]No character cards found in services/characters/. Falling back to Express.[/fail]")
        return await _run_express(conn)

    # Build table display
    table = make_table(
        ("#", {"justify": "right", "style": "muted"}),
        ("Name", {"style": "bold"}),
        ("Voice", {"style": "muted"}),
        "Values",
    )
    for i, card in enumerate(cards, 1):
        summary = get_card_summary(card)
        voice_preview = (summary["voice"] or "")[:50]
        if len(summary.get("voice", "") or "") > 50:
            voice_preview += "..."
        table.add_row(str(i), summary["name"], voice_preview, summary["values"] or "\u2014")
    console.print(table)

    choice_idx = _prompt_choice("Pick a character:", [get_card_summary(c)["name"] for c in cards], default=1)
    chosen = cards[choice_idx - 1]
    summary = get_card_summary(chosen)

    console.print(make_panel(
        f"[key]Name:[/key]   [bold]{summary['name']}[/bold]\n"
        f"[key]Voice:[/key]  {(summary['voice'] or '')[:80]}\n"
        f"[key]Values:[/key] {summary['values']}",
        title="Selected Character",
    ))

    user_name = _prompt(f"What should {summary['name']} call you?", default="User")

    tweak = _prompt_yes_no("Tweak anything?", default=False)
    if tweak:
        tweak_choice = _prompt_choice(
            "Tweak options:",
            [
                "Name / voice / description",
                "Values",
                "Goals",
                "Switch to full Custom (pre-filled with this character)",
            ],
            default=1,
        )
        hexis_ext = chosen["extensions_hexis"]
        if tweak_choice == 1:
            new_name = _prompt("Agent name", default=hexis_ext.get("name", ""))
            new_voice = _prompt("Voice/tone", default=hexis_ext.get("voice", ""))
            new_desc = _prompt("Description", default=hexis_ext.get("description", ""))
            hexis_ext["name"] = new_name
            hexis_ext["voice"] = new_voice
            hexis_ext["description"] = new_desc
        elif tweak_choice == 2:
            current_values = hexis_ext.get("values", [])
            new_values = _prompt_list("Values (comma-separated)", default=current_values)
            hexis_ext["values"] = new_values
        elif tweak_choice == 3:
            current_goals = hexis_ext.get("goals", [])
            new_goals = _prompt_list("Goals (comma-separated)", default=current_goals)
            hexis_ext["goals"] = [{"title": g, "priority": "queued", "source": "identity"} for g in new_goals]
        elif tweak_choice == 4:
            return await _run_custom(conn, prefill=hexis_ext, user_name=user_name)

    # Apply character card
    hexis_ext = chosen["extensions_hexis"]
    raw = await conn.fetchval(
        "SELECT init_from_character_card($1::jsonb, $2)",
        json.dumps(hexis_ext),
        user_name,
    )

    return user_name


# ---------------------------------------------------------------------------
# Tier 3: Custom
# ---------------------------------------------------------------------------

async def _run_custom(
    conn: Any,
    prefill: dict[str, Any] | None = None,
    user_name: str | None = None,
) -> str:
    """Custom init: 3 phases — Who, What Matters, What's Next."""
    pf = prefill or {}
    console.print(f"\n{_step_bar(2)}\n")
    heading("Custom Setup")

    # Phase 1: Who (identity + personality)
    console.print("\n[accent]Phase 1: Who[/accent]\n")
    agent_name = _prompt("Agent name", default=pf.get("name", "Hexis"))
    pronouns = _prompt("Pronouns", default=pf.get("pronouns", "they/them"))
    voice = _prompt("Voice/tone", default=pf.get("voice", "thoughtful and curious"))
    description = _prompt(
        "Description",
        default=pf.get("description", f"I am {agent_name}, a developing mind."),
    )
    purpose = _prompt(
        "Purpose",
        default=pf.get("purpose", "To be helpful, to learn, and to grow as an individual."),
    )

    set_traits = _prompt_yes_no("Set Big Five personality traits?", default=False)
    traits: dict[str, float] | None = None
    if set_traits:
        existing_traits = pf.get("personality_traits", {})
        traits = {}
        for trait in ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]:
            default_val = existing_traits.get(trait, 0.5)
            traits[trait] = _prompt_float(
                f"  {trait.capitalize()} (0.0-1.0)",
                default=default_val,
                min_value=0.0,
            )

    personality_desc = pf.get("personality_description", "reflective and exploratory")

    if user_name is None:
        user_name = _prompt(f"\nWhat should {agent_name} call you?", default="User")

    # Apply Phase 1
    await conn.fetchval("SELECT init_mode('persona')")
    await conn.fetchval(
        "SELECT init_identity($1, $2, $3, $4, $5, $6)",
        agent_name, pronouns, voice, description, purpose, user_name,
    )
    await conn.fetchval(
        "SELECT init_personality($1::jsonb, $2)",
        json.dumps(traits) if traits else None,
        personality_desc,
    )
    console.print("[ok]\u2714[/ok] Identity saved")

    # Phase 2: What Matters (values + worldview + boundaries)
    console.print("\n[accent]Phase 2: What Matters[/accent]\n")
    default_values = pf.get("values", ["honesty", "growth", "kindness", "wisdom", "humility"])
    values = _prompt_list("Values (comma-separated)", default=default_values)
    values_json = json.dumps(values)

    default_worldview = pf.get("worldview", {
        "metaphysics": "agnostic",
        "human_nature": "mixed",
        "epistemology": "empiricist",
        "ethics": "virtue ethics",
    })
    set_worldview = _prompt_yes_no("Set worldview beliefs?", default=False)
    worldview = default_worldview
    if set_worldview:
        worldview = {}
        for key in ["metaphysics", "human_nature", "epistemology", "ethics"]:
            worldview[key] = _prompt(f"  {key}", default=str(default_worldview.get(key, "")))

    default_boundaries = pf.get("boundaries", [
        "I will not deceive people or falsify evidence.",
        "I will avoid causing harm.",
        "I will protect privacy and sensitive information.",
        "I will be honest about uncertainty.",
    ])
    boundaries = _prompt_list("Boundaries (comma-separated)", default=default_boundaries)
    boundaries_json = json.dumps(boundaries)

    await conn.fetchval("SELECT init_values($1::jsonb)", values_json)
    await conn.fetchval("SELECT init_worldview($1::jsonb)", json.dumps(worldview))
    await conn.fetchval("SELECT init_boundaries($1::jsonb)", boundaries_json)
    console.print("[ok]\u2714[/ok] Values and worldview saved")

    # Phase 3: What's Next (interests + goals + relationship)
    console.print("\n[accent]Phase 3: What's Next[/accent]\n")
    default_interests = pf.get("interests", ["broad curiosity across domains"])
    interests = _prompt_list("Interests (comma-separated)", default=default_interests)

    default_goals = pf.get("goals", ["Support the user and grow as an individual"])
    # Handle goals that might be objects with 'title' key
    if default_goals and isinstance(default_goals[0], dict):
        default_goals = [g.get("title", str(g)) for g in default_goals]
    goals = _prompt_list("Goals (comma-separated)", default=default_goals)

    rel_type = _prompt("Relationship type", default="partner")

    await conn.fetchval("SELECT init_interests($1::jsonb)", json.dumps(interests))
    await conn.fetchval(
        "SELECT init_goals($1::jsonb)",
        json.dumps({
            "goals": [{"title": g, "priority": "queued", "source": "identity"} for g in goals],
            "role": "general assistant",
            "relationship_aspiration": "co-develop with mutual respect",
        }),
    )
    await conn.fetchval(
        "SELECT init_relationship($1::jsonb, $2::jsonb)",
        json.dumps({"name": user_name}),
        json.dumps({"type": rel_type, "purpose": "co-develop"}),
    )

    # Merge heartbeat defaults into init profile
    await conn.fetchval("""
        SELECT merge_init_profile(jsonb_build_object('autonomy', 'medium'))
    """)

    # Advance to consent stage
    await conn.fetchval("""
        SELECT advance_init_stage('consent', jsonb_build_object('custom_completed', true))
    """)
    console.print("[ok]\u2714[/ok] Goals and relationship saved")

    return user_name


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

async def _run_consent(conn: Any, llm_config: dict[str, Any]) -> bool:
    """Run consent flow via LLM. Returns True if consented."""
    from rich.spinner import Spinner
    from rich.live import Live
    from core.init_api import run_consent_flow

    console.print(f"\n{_step_bar(3)}\n")
    heading("Consent")

    result = None
    try:
        with Live(Spinner("dots", text="[muted]Requesting consent from the agent...[/muted]"), console=console, transient=True):
            result = await run_consent_flow(conn, llm_config)
    except Exception as exc:
        err_console.print(f"[fail]Consent failed: {exc}[/fail]")
        return False

    decision = result.get("decision", "abstain")

    if decision == "consent":
        console.print(f"[ok]\u2714 Consent granted[/ok]")
        return True
    elif decision == "decline":
        console.print(f"[fail]\u2718 Consent declined.[/fail] The agent chose not to initialize.")
        console.print("[muted]You can re-run `hexis init` to try again.[/muted]")
        return False
    else:
        console.print(f"[warn]\u26a0 Consent abstained.[/warn] No initialization will occur.")
        console.print("[muted]You can re-run `hexis init` to try again.[/muted]")
        return False


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def _run_init(dsn: str, *, wait_seconds: int) -> int:
    import asyncpg

    await agent_api.ensure_schema_has_config(dsn, wait_seconds=wait_seconds)
    conn = await agent_api._connect_with_retry(dsn, wait_seconds=wait_seconds)

    try:
        console.print(make_panel(
            "[muted]Bring a new mind into being.[/muted]",
            title="Hexis Init Wizard",
        ))

        # Step 0: LLM Config
        llm_config = await _configure_llm(conn)

        # Choose tier
        tier = _choose_tier()

        # Run selected tier
        if tier == "express":
            user_name = await _run_express(conn)
        elif tier == "character":
            user_name = await _run_character(conn)
        else:
            user_name = await _run_custom(conn)

        # Consent (all tiers)
        consented = await _run_consent(conn, llm_config)
        if not consented:
            return 1

        # Get agent name from profile
        raw = await conn.fetchval("SELECT get_init_profile()")
        profile = json.loads(raw) if isinstance(raw, str) else (raw or {})
        agent_name = profile.get("agent", {}).get("name", "Hexis")

        console.print(f"\n[ok]\u2714[/ok] [bold]{agent_name}[/bold] is ready. Run [accent]hexis chat[/accent] to say hello.")
        return 0

    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hexis init",
        description="Interactive bootstrap for Hexis (3-tier: Express, Character, Custom).",
    )
    p.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    p.add_argument("--wait-seconds", type=int, default=int(os.getenv("POSTGRES_WAIT_SECONDS", "30")))

    # Non-interactive mode flags (any of --api-key, --provider, --character triggers it)
    p.add_argument("--api-key", default=None,
                    help="API key (auto-detects provider; triggers non-interactive mode)")
    p.add_argument("--provider", default=None,
                    help="LLM provider (auto-detected from --api-key if omitted)")
    p.add_argument("--model", default=None,
                    help="LLM model (defaults per provider)")
    p.add_argument("--character", default=None,
                    help="Character card name (e.g. 'hexis', 'jarvis'). Omit for express defaults")
    p.add_argument("--name", default=None,
                    help="What the agent should call you (default: 'User')")
    p.add_argument("--no-docker", action="store_true", default=False,
                    help="Skip Docker auto-start")
    p.add_argument("--no-pull", action="store_true", default=False,
                    help="Skip Ollama embedding model pull")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    # Non-interactive mode if any of these flags are present
    if args.api_key or args.provider or args.character:
        try:
            return asyncio.run(_run_init_noninteractive(args))
        except KeyboardInterrupt:
            err_console.print("\n[warn]Cancelled.[/warn]")
            return 130
        except Exception as e:
            err_console.print(f"[fail]init failed: {e}[/fail]")
            return 1

    # Interactive mode (original flow)
    if args.dsn:
        dsn = args.dsn
    else:
        dsn = agent_api.db_dsn_from_env()

    try:
        return asyncio.run(_run_init(dsn, wait_seconds=args.wait_seconds))
    except KeyboardInterrupt:
        err_console.print("\n[warn]Cancelled.[/warn]")
        return 130
    except Exception as e:
        err_console.print(f"[fail]init failed: {e}[/fail]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
