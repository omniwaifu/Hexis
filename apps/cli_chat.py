"""Hexis CLI chat — streaming conversation via AgentLoop.

Supports:
  - Token-by-token streaming with rich rendering
  - Tool call visibility as inline dim text
  - Markdown rendering for assistant responses
  - Slash commands: /clear, /recall <q>, /status, /quit, /verbose, /debug
  - --verbose flag to show hydrated context and tool I/O
  - --debug flag to also dump the full system prompt and LLM request
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
import uuid
from typing import Any

from dotenv import load_dotenv

from apps.cli_theme import console, err_console


def _fmt_json(obj: Any, max_len: int = 400) -> str:
    """Format an object as compact JSON, truncating if needed."""
    try:
        s = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _print_debug_panel(title: str, content: str, *, style: str = "blue") -> None:
    from rich.panel import Panel
    from rich.syntax import Syntax

    # Try to render as syntax-highlighted if it looks like JSON
    try:
        parsed = json.loads(content)
        content = json.dumps(parsed, indent=2, ensure_ascii=False)
        renderable = Syntax(content, "json", theme="monokai", word_wrap=True)
    except (json.JSONDecodeError, TypeError):
        renderable = content  # type: ignore[assignment]

    console.print(Panel(
        renderable,
        title=f"[bold]{title}[/bold]",
        border_style=style,
        expand=True,
    ))


async def _run_chat(dsn: str, *, verbose: bool = False, debug: bool = False) -> int:
    import asyncpg
    from core.agent_api import get_agent_profile_context
    from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
    from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt
    from core.llm_config import load_llm_config
    from core.tools import ToolContext, create_default_registry
    from services.chat import _build_system_prompt, _remember_conversation
    from rich.panel import Panel
    from rich.table import Table

    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
    history: list[dict[str, Any]] = []

    try:
        # Load config once
        async with pool.acquire() as conn:
            llm_config = await load_llm_config(conn, "llm.chat", fallback_key="llm")

        registry = create_default_registry(pool)
        agent_profile = await get_agent_profile_context(dsn)
        system_prompt = await _build_system_prompt(agent_profile, registry)

        agent_name = "Hexis"
        if isinstance(agent_profile, dict):
            agent_name = agent_profile.get("name", "Hexis")

        console.print(f"\n[accent]Hexis Chat[/accent] [muted]— streaming conversation with {agent_name}[/muted]")
        mode_flags = []
        if verbose:
            mode_flags.append("verbose")
        if debug:
            mode_flags.append("debug")
        if mode_flags:
            console.print(f"[muted]Mode: {', '.join(mode_flags)} — showing {'full prompt + ' if debug else ''}hydrated context and tool I/O[/muted]")
        console.print("[muted]Type /quit to exit, /clear to reset, /recall <q> to search, /verbose or /debug to toggle[/muted]\n")

        # Debug: show system prompt and LLM config on startup
        if debug:
            _print_debug_panel(
                f"LLM Config ({llm_config.get('provider', '?')}/{llm_config.get('model', '?')})",
                json.dumps({k: v for k, v in llm_config.items() if k != "api_key"}, indent=2),
                style="cyan",
            )
            _print_debug_panel(
                f"System Prompt ({len(system_prompt)} chars)",
                system_prompt,
                style="magenta",
            )

        while True:
            try:
                console.print("[accent]you:[/accent] ", end="")
                user_input = input().strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[muted]Goodbye.[/muted]")
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                cmd_parts = user_input.split(maxsplit=1)
                cmd = cmd_parts[0].lower()

                if cmd in ("/quit", "/exit"):
                    console.print("[muted]Goodbye.[/muted]")
                    break

                elif cmd == "/clear":
                    history.clear()
                    console.print("[muted]Conversation cleared.[/muted]\n")
                    continue

                elif cmd == "/recall":
                    query = cmd_parts[1] if len(cmd_parts) > 1 else ""
                    if not query:
                        err_console.print("[fail]Usage: /recall <query>[/fail]")
                        continue
                    async with CognitiveMemory.connect(dsn) as mem:
                        result = await mem.recall(query, limit=5)
                    if not result.memories:
                        console.print("[muted]No memories found.[/muted]\n")
                    else:
                        for m in result.memories:
                            content = m.content[:100] + "..." if len(m.content) > 100 else m.content
                            console.print(f"  [teal]{m.type}[/teal] {content} [muted](sim: {m.similarity:.2f})[/muted]")
                        console.print()
                    continue

                elif cmd == "/status":
                    from core.cli_api import status_payload_rich
                    from apps.hexis_cli import _print_rich_status
                    payload = await status_payload_rich(dsn)
                    _print_rich_status(payload)
                    continue

                elif cmd == "/verbose":
                    verbose = not verbose
                    console.print(f"[muted]Verbose mode: {'on' if verbose else 'off'}[/muted]\n")
                    continue

                elif cmd == "/debug":
                    debug = not debug
                    verbose = verbose or debug  # debug implies verbose
                    console.print(f"[muted]Debug mode: {'on' if debug else 'off'} (verbose: {'on' if verbose else 'off'})[/muted]\n")
                    if debug:
                        _print_debug_panel(
                            f"System Prompt ({len(system_prompt)} chars)",
                            system_prompt,
                            style="magenta",
                        )
                    continue

                elif cmd == "/prompt":
                    _print_debug_panel(
                        f"System Prompt ({len(system_prompt)} chars)",
                        system_prompt,
                        style="magenta",
                    )
                    continue

                elif cmd == "/tools":
                    specs = await registry.get_specs(ToolContext.CHAT)
                    table = Table(title="Available Tools", show_lines=False, border_style="dim")
                    table.add_column("Name", style="teal")
                    table.add_column("Description", style="dim", max_width=80)
                    for spec in specs:
                        func = spec.get("function", {})
                        table.add_row(func.get("name", "?"), _truncate(func.get("description", ""), 80))
                    console.print(table)
                    console.print()
                    continue

                elif cmd == "/history":
                    if not history:
                        console.print("[muted]No conversation history yet.[/muted]\n")
                    else:
                        for i, msg in enumerate(history):
                            role = msg["role"]
                            content = _truncate(msg["content"], 120)
                            console.print(f"  [dim]{i}[/dim] [teal]{role}[/teal]: {content}")
                        console.print()
                    continue

                else:
                    err_console.print(f"[fail]Unknown command: {cmd}[/fail]")
                    err_console.print("[muted]Commands: /quit /clear /recall /status /verbose /debug /prompt /tools /history[/muted]")
                    continue

            # Normal message — stream response
            session_id = str(uuid.uuid4())

            try:
                async with CognitiveMemory.connect(dsn) as mem_client:
                    # Hydrate memory context
                    context = await mem_client.hydrate(
                        user_input,
                        memory_limit=10,
                        include_partial=True,
                        include_identity=True,
                        include_worldview=True,
                        include_emotional_state=True,
                        include_goals=True,
                        include_drives=True,
                    )
                    if context.memories:
                        await mem_client.touch_memories([m.id for m in context.memories])

                    memory_context = format_context_for_prompt(context, max_memories=10)
                    if memory_context:
                        enriched = f"{memory_context}\n\n[USER MESSAGE]\n{user_input}"
                    else:
                        enriched = user_input

                    # Verbose: show hydrated context
                    if verbose:
                        console.print(Panel(
                            memory_context or "[dim]No memory context[/dim]",
                            title="Hydrated Context",
                            border_style="dim",
                            expand=False,
                        ))

                    # Debug: show the full enriched user message that gets sent to the LLM
                    if debug:
                        _print_debug_panel(
                            f"Enriched User Message ({len(enriched)} chars)",
                            enriched,
                            style="yellow",
                        )

                        # Show conversation history being sent
                        if history:
                            hist_summary = "\n".join(
                                f"[{m['role']}] {_truncate(m['content'], 150)}"
                                for m in history
                            )
                            _print_debug_panel(
                                f"Conversation History ({len(history)} messages)",
                                hist_summary,
                                style="dim",
                            )

                    # Configure agent loop
                    loop_config = AgentLoopConfig(
                        tool_context=ToolContext.CHAT,
                        system_prompt=system_prompt,
                        llm_config=llm_config,
                        registry=registry,
                        pool=pool,
                        energy_budget=None,
                        max_iterations=6,
                        timeout_seconds=120.0,
                        temperature=0.7,
                        max_tokens=1200,
                        session_id=session_id,
                    )

                    agent = AgentLoop(loop_config)
                    full_text = ""
                    tool_calls_log: list[dict[str, Any]] = []

                    if context.memories:
                        console.print(f"  [muted]Recalled {len(context.memories)} memories[/muted]")

                    # Stream tokens
                    console.print(f"[teal]{agent_name}:[/teal] ", end="")
                    async for event in agent.stream(enriched, history=history):
                        if event.event == AgentEvent.TEXT_DELTA:
                            text = event.data.get("text", "")
                            if text:
                                full_text += text
                                sys.stdout.write(text)
                                sys.stdout.flush()

                        elif event.event == AgentEvent.TOOL_START:
                            tool_name = event.data.get("tool_name", "tool")
                            arguments = event.data.get("arguments", {})
                            tool_calls_log.append({"tool": tool_name, "args": arguments})
                            if verbose:
                                console.print(f"\n  [dim]{tool_name}({_fmt_json(arguments, 300)})[/dim]", end="")
                            else:
                                console.print(f"\n  [dim]{tool_name}...[/dim]", end="")

                        elif event.event == AgentEvent.TOOL_RESULT:
                            tool_name = event.data.get("tool_name", "tool")
                            success = event.data.get("success", False)
                            duration = event.data.get("duration")
                            dur_str = f" [{duration:.1f}s]" if isinstance(duration, (int, float)) else ""
                            if success:
                                console.print(f" [ok]done[/ok][dim]{dur_str}[/dim]")
                                if verbose:
                                    display = event.data.get("display_output") or event.data.get("output")
                                    if display:
                                        console.print(f"    [dim]{_fmt_json(display, 500)}[/dim]")
                            else:
                                error_msg = event.data.get("error", "")
                                console.print(f" [fail]failed[/fail][dim]{dur_str}[/dim] [muted]{error_msg[:120]}[/muted]")

                        elif event.event == AgentEvent.LOOP_START:
                            if debug:
                                tool_count = event.data.get("tool_count", 0)
                                energy = event.data.get("energy_budget", "unlimited")
                                console.print(f"  [dim]Loop started: {tool_count} tools, energy={energy}[/dim]")

                        elif event.event == AgentEvent.LOOP_END:
                            if debug:
                                reason = event.data.get("stopped_reason", "?")
                                iters = event.data.get("iterations", 0)
                                energy_spent = event.data.get("energy_spent", 0)
                                timed_out = event.data.get("timed_out", False)
                                console.print(
                                    f"  [dim]Loop ended: reason={reason}, iterations={iters}, "
                                    f"energy_spent={energy_spent}{', TIMED OUT' if timed_out else ''}[/dim]"
                                )

                        elif event.event == AgentEvent.ERROR:
                            error_msg = event.data.get("error", "Unknown error")
                            console.print(f"\n[fail]Error: {error_msg}[/fail]")

                    # End the streaming line
                    sys.stdout.write("\n")

                    # Debug: post-turn summary
                    if debug and tool_calls_log:
                        summary = "\n".join(
                            f"  {tc['tool']}({json.dumps(tc['args'], default=str)[:100]})"
                            for tc in tool_calls_log
                        )
                        console.print(f"[dim]Tool calls this turn:\n{summary}[/dim]")

                    console.print()

                    # Update history
                    history.append({"role": "user", "content": user_input})
                    history.append({"role": "assistant", "content": full_text})

                    # Memory formation
                    if full_text:
                        try:
                            await _remember_conversation(
                                mem_client,
                                user_message=user_input,
                                assistant_message=full_text,
                            )
                        except Exception:
                            pass

            except Exception as e:
                err_console.print(f"\n[fail]Error: {e}[/fail]\n")

    finally:
        await pool.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hexis chat",
        description="Interactive streaming chat with your Hexis agent.",
    )
    p.add_argument("--dsn", default=None, help="Postgres DSN; defaults to POSTGRES_* env vars")
    p.add_argument("-v", "--verbose", action="store_true", help="Show hydrated context and tool I/O")
    p.add_argument("-d", "--debug", action="store_true", help="Full debug: system prompt, enriched messages, LLM config, tool specs")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    from core.agent_api import db_dsn_from_env

    args = build_parser().parse_args(argv)
    dsn = args.dsn or db_dsn_from_env()
    verbose = args.verbose or args.debug
    debug = args.debug

    try:
        return asyncio.run(_run_chat(dsn, verbose=verbose, debug=debug))
    except KeyboardInterrupt:
        console.print("\n[muted]Goodbye.[/muted]")
        return 0
    except Exception as e:
        err_console.print(f"[fail]Chat failed: {e}[/fail]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
