"""
Hexis Channel System - Channel Manager

Orchestrates multiple channel adapters, routing inbound messages to the
conversation handler and providing a unified send interface for outbound.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .base import ChannelAdapter, ChannelMessage
from .commands import CommandRegistry, parse_command
from .conversation import process_channel_message, stream_channel_message

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class ChannelManager:
    """
    Manages the lifecycle of channel adapters and routes messages.

    Usage:
        manager = ChannelManager(pool)
        manager.register(discord_adapter)
        manager.register(telegram_adapter)
        await manager.start_all()
        ...
        await manager.stop_all()
    """

    def __init__(self, pool: asyncpg.Pool, *, commands: CommandRegistry | None = None) -> None:
        self._pool = pool
        self._adapters: dict[str, ChannelAdapter] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False
        self._commands = commands or CommandRegistry()

    @property
    def adapters(self) -> dict[str, ChannelAdapter]:
        """Registered adapters by channel_type."""
        return dict(self._adapters)

    def register(self, adapter: ChannelAdapter) -> None:
        """Register a channel adapter."""
        ctype = adapter.channel_type
        if ctype in self._adapters:
            logger.warning("Channel adapter %r already registered, replacing", ctype)
        self._adapters[ctype] = adapter
        logger.info("Registered channel adapter: %s", ctype)

    async def ensure_started(self, adapter: ChannelAdapter) -> bool:
        """
        Register and start an adapter if it isn't already registered.

        Returns:
            True if the adapter was newly registered (and started if the manager
            is running), False if it already existed.
        """
        ctype = adapter.channel_type
        if ctype in self._adapters:
            return False

        self.register(adapter)

        # If the manager has already been started, ensure the new adapter
        # launches immediately.
        if self._running:
            await self._start_adapter(ctype, adapter)

        return True

    async def start_all(self) -> None:
        """Start all registered adapters."""
        self._running = True
        for ctype, adapter in self._adapters.items():
            await self._start_adapter(ctype, adapter)

    async def _start_adapter(self, ctype: str, adapter: ChannelAdapter) -> None:
        """Start a single adapter with error isolation."""
        try:

            async def on_message(msg: ChannelMessage) -> None:
                await self._handle_message(msg)

            # Each adapter's start() runs its own event loop (blocking).
            # Wrap in a task so they run concurrently.
            task = asyncio.create_task(
                self._run_adapter(ctype, adapter, on_message),
                name=f"channel-{ctype}",
            )
            self._tasks[ctype] = task
            logger.info("Started channel adapter: %s", ctype)

        except Exception:
            logger.exception("Failed to start channel adapter: %s", ctype)

    async def _run_adapter(self, ctype: str, adapter: ChannelAdapter, on_message) -> None:
        """Run an adapter with restart-on-crash."""
        while self._running:
            try:
                await adapter.start(on_message)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Channel adapter %s crashed, restarting in 10s", ctype)
                await asyncio.sleep(10)

    async def _handle_message(self, msg: ChannelMessage) -> None:
        """Handle an inbound message by routing to conversation handler."""
        adapter = self._adapters.get(msg.channel_type)
        if not adapter:
            logger.warning("No adapter for channel type: %s", msg.channel_type)
            return

        # I.3: Per-channel user allowlisting
        if not await self._check_user_allowed(msg):
            logger.debug(
                "Ignoring message from non-allowed user %s on %s",
                msg.sender_id, msg.channel_type,
            )
            return

        logger.info(
            "Channel message: %s/%s from %s: %s",
            msg.channel_type,
            msg.channel_id,
            msg.sender_name,
            msg.content[:80],
        )

        # Check for slash commands
        parsed = parse_command(msg.content)
        if parsed:
            cmd_name, cmd_args = parsed
            if self._commands.has(cmd_name):
                response = await self._commands.execute(cmd_name, cmd_args, self._pool)
                if response:
                    try:
                        await adapter.send(
                            msg.channel_id,
                            response,
                            reply_to=msg.message_id,
                            thread_id=msg.thread_id,
                        )
                    except Exception:
                        logger.exception("Failed to send command response for /%s", cmd_name)
                return

        # Keep typing indicator alive for the duration of processing
        typing_task: asyncio.Task | None = None
        if adapter.capabilities.typing_indicator:
            async def _keep_typing() -> None:
                try:
                    while True:
                        try:
                            await adapter.send_typing(msg.channel_id)
                        except Exception:
                            pass
                        await asyncio.sleep(4)
                except asyncio.CancelledError:
                    pass
            typing_task = asyncio.create_task(_keep_typing())

        try:
            # Use streaming for channels that support edit_message
            if adapter.capabilities.edit_message:
                await stream_channel_message(msg, self._pool, adapter)
            else:
                # Fall back to chunked delivery
                max_len = adapter.capabilities.max_message_length
                response_chunks = await process_channel_message(
                    msg,
                    self._pool,
                    max_message_length=max_len,
                )

                reply_to = msg.message_id
                for i, chunk in enumerate(response_chunks):
                    try:
                        await adapter.send(
                            msg.channel_id,
                            chunk,
                            reply_to=reply_to if i == 0 else None,
                            thread_id=msg.thread_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to send response chunk %d to %s/%s",
                            i, msg.channel_type, msg.channel_id,
                        )
                        break
        except Exception as exc:
            logger.exception("Error processing message from %s/%s", msg.channel_type, msg.channel_id)
            try:
                await adapter.send(msg.channel_id, f"⚠ Error: {exc}", thread_id=msg.thread_id)
            except Exception:
                pass
        finally:
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

    async def send(
        self,
        channel_type: str,
        channel_id: str,
        text: str,
        **kwargs: Any,
    ) -> str | None:
        """
        Send an outbound message to a specific channel.

        Used by heartbeat/outbox for proactive messaging.
        """
        adapter = self._adapters.get(channel_type)
        if not adapter:
            logger.error("No adapter for channel type: %s", channel_type)
            return None
        return await adapter.send(channel_id, text, **kwargs)

    async def stop_all(self) -> None:
        """Stop all adapters gracefully."""
        self._running = False

        # Cancel all adapter tasks
        for ctype, task in self._tasks.items():
            task.cancel()

        # Wait for tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        # Stop each adapter
        for ctype, adapter in self._adapters.items():
            try:
                await adapter.stop()
                logger.info("Stopped channel adapter: %s", ctype)
            except Exception:
                logger.exception("Error stopping channel adapter: %s", ctype)

        self._tasks.clear()
        logger.info("All channel adapters stopped")

    async def _check_user_allowed(self, msg: ChannelMessage) -> bool:
        """I.3: Check per-channel user allowlist from config.

        Config key: channel.{type}.allowed_users
        Value: JSON array of user IDs, or "*" to allow all.
        """
        try:
            async with self._pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT get_config_text($1)",
                    f"channel.{msg.channel_type}.allowed_users",
                )
            if raw is None or raw == "*":
                return True
            import json
            try:
                allowed = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                return True
            if isinstance(allowed, list):
                return msg.sender_id in [str(v) for v in allowed]
            return True
        except Exception:
            # Fail open — don't block messages if config lookup fails
            return True

    async def get_session_ttl(self, channel_type: str) -> int:
        """I.4: Get configurable session lifetime in seconds.

        Config key: channel.session_ttl or channel.{type}.session_ttl
        Default: 3600 (1 hour).
        """
        try:
            async with self._pool.acquire() as conn:
                # Check channel-specific TTL first, then global
                ttl = await conn.fetchval(
                    "SELECT get_config_int($1)",
                    f"channel.{channel_type}.session_ttl",
                )
                if ttl is not None:
                    return int(ttl)
                ttl = await conn.fetchval(
                    "SELECT get_config_int($1)",
                    "channel.session_ttl",
                )
                if ttl is not None:
                    return int(ttl)
        except Exception:
            pass
        return 3600  # Default 1 hour

    def status(self) -> list[dict[str, Any]]:
        """Return status of all registered adapters."""
        result = []
        for ctype, adapter in self._adapters.items():
            result.append({
                "channel_type": ctype,
                "connected": adapter.is_connected,
                "capabilities": {
                    "threads": adapter.capabilities.threads,
                    "reactions": adapter.capabilities.reactions,
                    "media": adapter.capabilities.media,
                    "typing_indicator": adapter.capabilities.typing_indicator,
                    "max_message_length": adapter.capabilities.max_message_length,
                },
            })
        return result
