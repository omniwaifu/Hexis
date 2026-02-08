"""
Hexis Channel Worker Service

Stateless service that runs channel adapters (Discord, Telegram, etc.)
and routes messages through the conversation pipeline.

Usage:
    hexis-channels                    # Start all configured channels
    hexis-channels --channel discord  # Start only Discord
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from typing import Any

import asyncpg
from dotenv import load_dotenv

from core.agent_api import db_dsn_from_env

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("channel_worker")

CHANNEL_CONFIG_POLL_INTERVAL_S = float(os.getenv("HEXIS_CHANNEL_CONFIG_POLL_INTERVAL_S", "15"))
SUPPORTED_CHANNEL_TYPES = [
    "discord",
    "telegram",
    "slack",
    "signal",
    "whatsapp",
    "imessage",
    "matrix",
]


async def _load_channel_config(conn: asyncpg.Connection, channel_type: str) -> dict:
    """Load channel config from the DB config table."""
    prefix = f"channel.{channel_type}."
    rows = await conn.fetch(
        "SELECT key, value FROM config WHERE key LIKE $1",
        prefix + "%",
    )
    config: dict = {}
    for row in rows:
        key = str(row["key"]).removeprefix(prefix)
        value = row["value"]
        # Try to parse JSON values
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass
        config[key] = value
    return config


def _wanted_channel_types(channels: list[str] | None) -> list[str]:
    if channels is None:
        return list(SUPPORTED_CHANNEL_TYPES)

    # Preserve CLI ordering but dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for c in channels:
        if c in SUPPORTED_CHANNEL_TYPES and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _env_or_config_present(config: dict[str, Any], key: str, env_name: str) -> bool:
    # Some adapters store the env var NAME in config (not the value).
    return bool(config.get(key)) or bool(os.getenv(env_name))


def _is_configured_discord(config: dict[str, Any]) -> bool:
    try:
        from channels.discord_adapter import _resolve_token

        return bool(_resolve_token(config))
    except Exception:
        # If resolution logic changes, fall back to a conservative check.
        return _env_or_config_present(config, "bot_token", "DISCORD_BOT_TOKEN")


def _is_configured_telegram(config: dict[str, Any]) -> bool:
    try:
        from channels.telegram_adapter import _resolve_token

        return bool(_resolve_token(config))
    except Exception:
        return _env_or_config_present(config, "bot_token", "TELEGRAM_BOT_TOKEN")


def _is_configured_slack(config: dict[str, Any]) -> bool:
    try:
        from channels.slack_adapter import _resolve_token

        return bool(_resolve_token(config, "bot_token", "SLACK_BOT_TOKEN"))
    except Exception:
        return _env_or_config_present(config, "bot_token", "SLACK_BOT_TOKEN")


def _is_configured_signal(config: dict[str, Any]) -> bool:
    try:
        from channels.signal_adapter import _resolve_token

        return bool(_resolve_token(config))
    except Exception:
        return _env_or_config_present(config, "phone_number", "SIGNAL_PHONE_NUMBER")


def _is_configured_whatsapp(config: dict[str, Any]) -> bool:
    try:
        from channels.whatsapp_adapter import _resolve_token

        access_token = _resolve_token(config, "access_token", "WHATSAPP_ACCESS_TOKEN")
    except Exception:
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN") or config.get("access_token")

    phone_number_id = str(config.get("phone_number_id") or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "")
    return bool(access_token) and bool(phone_number_id)


def _is_configured_imessage(config: dict[str, Any]) -> bool:
    try:
        from channels.imessage_adapter import _resolve_config

        return bool(_resolve_config(config, "password", "IMESSAGE_PASSWORD"))
    except Exception:
        return _env_or_config_present(config, "password", "IMESSAGE_PASSWORD")


def _is_configured_matrix(config: dict[str, Any]) -> bool:
    homeserver = str(config.get("homeserver") or os.getenv("MATRIX_HOMESERVER") or "")
    user_id = str(config.get("user_id") or os.getenv("MATRIX_USER_ID") or "")
    if not homeserver or not user_id:
        return False

    try:
        from channels.matrix_adapter import _resolve_token

        return bool(_resolve_token(config, "access_token", "MATRIX_ACCESS_TOKEN"))
    except Exception:
        return _env_or_config_present(config, "access_token", "MATRIX_ACCESS_TOKEN")


def _is_channel_configured(channel_type: str, config: dict[str, Any]) -> bool:
    if channel_type == "discord":
        return _is_configured_discord(config)
    if channel_type == "telegram":
        return _is_configured_telegram(config)
    if channel_type == "slack":
        return _is_configured_slack(config)
    if channel_type == "signal":
        return _is_configured_signal(config)
    if channel_type == "whatsapp":
        return _is_configured_whatsapp(config)
    if channel_type == "imessage":
        return _is_configured_imessage(config)
    if channel_type == "matrix":
        return _is_configured_matrix(config)
    return False


async def _ensure_configured_adapters_running(manager, conn: asyncpg.Connection, channels: list[str] | None) -> int:
    """
    Detect newly-configured channels and start their adapters.

    This only starts adapters when required credentials appear resolvable
    (token/password/etc) to avoid crash/restart loops.
    """
    started = 0

    for channel_type in _wanted_channel_types(channels):
        if channel_type in manager.adapters:
            continue

        config = await _load_channel_config(conn, channel_type)
        if not _is_channel_configured(channel_type, config):
            continue

        try:
            if channel_type == "discord":
                try:
                    from channels.discord_adapter import DiscordAdapter

                    adapter = DiscordAdapter(config)
                except ImportError:
                    logger.warning("discord.py not installed, skipping Discord channel")
                    continue

            elif channel_type == "telegram":
                try:
                    from channels.telegram_adapter import TelegramAdapter

                    adapter = TelegramAdapter(config)
                except ImportError:
                    logger.warning("python-telegram-bot not installed, skipping Telegram channel")
                    continue

            elif channel_type == "slack":
                try:
                    from channels.slack_adapter import SlackAdapter

                    adapter = SlackAdapter(config)
                except ImportError:
                    logger.warning("slack-bolt not installed, skipping Slack channel")
                    continue

            elif channel_type == "signal":
                from channels.signal_adapter import SignalAdapter

                adapter = SignalAdapter(config)

            elif channel_type == "whatsapp":
                from channels.whatsapp_adapter import WhatsAppAdapter

                adapter = WhatsAppAdapter(config)

            elif channel_type == "imessage":
                from channels.imessage_adapter import IMessageAdapter

                adapter = IMessageAdapter(config)

            elif channel_type == "matrix":
                try:
                    from channels.matrix_adapter import MatrixAdapter

                    adapter = MatrixAdapter(config)
                except ImportError:
                    logger.warning("matrix-nio not installed, skipping Matrix channel")
                    continue

            else:
                continue

            if await manager.ensure_started(adapter):
                started += 1

        except Exception:
            logger.exception("Failed to start channel adapter: %s", channel_type)

    return started


async def run_channel_worker(
    channels: list[str] | None = None,
    instance: str | None = None,
) -> None:
    """
    Main entry point for the channel worker.

    Args:
        channels: List of channel types to start, or None for all configured.
        instance: Target a specific Hexis instance.
    """
    from channels.manager import ChannelManager

    dsn = db_dsn_from_env(instance)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
    logger.info("Connected to database")

    manager = ChannelManager(pool)

    # Initial config scan (may find zero channels).
    async with pool.acquire() as conn:
        try:
            is_ready = await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()")
            if not is_ready:
                logger.warning("Agent not configured. Run 'hexis init' first.")
        except Exception:
            logger.warning("Failed to check agent readiness", exc_info=True)

        await _ensure_configured_adapters_running(manager, conn, channels)

    # Set up graceful shutdown
    stop_event = asyncio.Event()

    def shutdown_handler(sig, frame):
        logger.info("Received %s, shutting down...", signal.Signals(sig).name)
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Mark the manager as running and start any adapters we found.
    logger.info("Starting %d channel adapter(s)...", len(manager.adapters))
    await manager.start_all()

    # Start outbox consumer as a background task
    outbox_consumer = None
    outbox_task = None
    try:
        from channels.outbox import ChannelOutboxConsumer
        outbox_consumer = ChannelOutboxConsumer(manager, pool)
        outbox_task = asyncio.create_task(
            outbox_consumer.start(),
            name="outbox-consumer",
        )
        logger.info("Outbox consumer started")
    except Exception:
        logger.warning("Failed to start outbox consumer", exc_info=True)

    if not manager.adapters:
        logger.info(
            "No channels configured. Staying idle and polling DB config every %.0fs "
            "for channel.* settings.",
            CHANNEL_CONFIG_POLL_INTERVAL_S,
        )

    async def _config_watch_loop() -> None:
        while not stop_event.is_set():
            try:
                async with pool.acquire() as conn:
                    started = await _ensure_configured_adapters_running(manager, conn, channels)
                    if started:
                        logger.info("Detected new channel config; started %d adapter(s)", started)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Channel config refresh failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=CHANNEL_CONFIG_POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    config_task = asyncio.create_task(_config_watch_loop(), name="channel-config-watcher")

    # Wait for shutdown signal
    await stop_event.wait()

    # Graceful shutdown
    logger.info("Stopping channel adapters...")
    config_task.cancel()
    try:
        await config_task
    except asyncio.CancelledError:
        pass
    if outbox_consumer:
        await outbox_consumer.stop()
    if outbox_task:
        outbox_task.cancel()
        try:
            await outbox_task
        except asyncio.CancelledError:
            pass
    await manager.stop_all()
    await pool.close()
    logger.info("Channel worker stopped")


def main() -> int:
    """CLI entry point for hexis-channels."""
    p = argparse.ArgumentParser(
        prog="hexis-channels",
        description="Run Hexis channel adapters (Discord, Telegram, etc.)",
    )
    p.add_argument(
        "--channel", "-c",
        action="append",
        choices=["discord", "telegram", "slack", "signal", "whatsapp", "imessage", "matrix"],
        help="Start only specific channel(s). Can be repeated. Default: all configured.",
    )
    p.add_argument(
        "--instance", "-i",
        default=os.getenv("HEXIS_INSTANCE"),
        help="Target a specific instance.",
    )
    args = p.parse_args()
    asyncio.run(run_channel_worker(channels=args.channel, instance=args.instance))
    return 0


if __name__ == "__main__":
    main()
