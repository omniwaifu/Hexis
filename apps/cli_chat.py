"""Hexis CLI chat — streaming conversation via AgentLoop.

Supports:
  - Token-by-token streaming with rich rendering
  - Tool call visibility as inline dim text
  - Markdown rendering for assistant responses
  - Slash commands: /clear, /recall <q>, /status, /quit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import Any

from dotenv import load_dotenv

from apps.cli_theme import console, err_console


async def _run_chat(dsn: str) -> int:
    import asyncpg
    from core.agent_api import get_agent_profile_context
    from core.agent_loop import AgentEvent, AgentLoop, AgentLoopConfig
    from core.cognitive_memory_api import CognitiveMemory, format_context_for_prompt
    from core.llm_config import load_llm_config
    from core.tools import ToolContext, create_default_registry
    from services.chat import _build_system_prompt, _remember_conversation
    from rich.markdown import Markdown

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
        console.print("[muted]Type /quit to exit, /clear to reset, /recall <query> to search memory, /status for agent status[/muted]\n")

        while True:
            try:
                user_input = console.input("[accent]you:[/accent] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[muted]Goodbye.[/muted]")
                break

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                cmd_parts = user_input.split(maxsplit=1)
                cmd = cmd_parts[0].lower()

                if cmd == "/quit" or cmd == "/exit":
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

                else:
                    err_console.print(f"[fail]Unknown command: {cmd}[/fail]")
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
                        include_drives=True,
                    )
                    if context.memories:
                        await mem_client.touch_memories([m.id for m in context.memories])

                    memory_context = format_context_for_prompt(context)
                    if memory_context:
                        enriched = f"{memory_context}\n\n[USER MESSAGE]\n{user_input}"
                    else:
                        enriched = user_input

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

                    if context.memories:
                        console.print(f"  [muted]Recalled {len(context.memories)} memories[/muted]")

                    # Stream tokens
                    console.print(f"[teal]{agent_name}:[/teal] ", end="")
                    async for event in agent.stream(enriched, history=history):
                        if event.event == AgentEvent.TEXT_DELTA:
                            text = event.data.get("text", "")
                            if text:
                                full_text += text
                                # Print raw text token-by-token
                                sys.stdout.write(text)
                                sys.stdout.flush()

                        elif event.event == AgentEvent.TOOL_START:
                            tool_name = event.data.get("tool_name", "tool")
                            console.print(f"\n  [dim]{tool_name}...[/dim]", end="")

                        elif event.event == AgentEvent.TOOL_RESULT:
                            tool_name = event.data.get("tool_name", "tool")
                            success = event.data.get("success", False)
                            if success:
                                console.print(f" [ok]done[/ok]")
                            else:
                                error_msg = event.data.get("error", "")
                                console.print(f" [fail]failed[/fail] [muted]{error_msg[:80]}[/muted]")

                        elif event.event == AgentEvent.ERROR:
                            error_msg = event.data.get("error", "Unknown error")
                            console.print(f"\n[fail]Error: {error_msg}[/fail]")

                    # End the streaming line
                    sys.stdout.write("\n\n")
                    sys.stdout.flush()

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
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    from core.agent_api import db_dsn_from_env

    args = build_parser().parse_args(argv)
    dsn = args.dsn or db_dsn_from_env()

    try:
        return asyncio.run(_run_chat(dsn))
    except KeyboardInterrupt:
        console.print("\n[muted]Goodbye.[/muted]")
        return 0
    except Exception as e:
        err_console.print(f"[fail]Chat failed: {e}[/fail]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
