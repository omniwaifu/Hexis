from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone as tz
from typing import Any
from zoneinfo import ZoneInfo
import asyncpg
from core.config_loader import load_config

from core.agent_api import db_dsn_from_env
from core.gateway import EventSource, Gateway, GatewayConsumer, GatewayEvent
from core.rabbitmq_bridge import RabbitMQBridge
from core.state import (
    is_agent_terminated,
    mark_subconscious_decider_run,
    recompute_cron_next_runs,
    run_heartbeat,
    run_maintenance_if_due,
    run_scheduled_tasks,
    should_run_subconscious_decider,
)
from services.external_calls import ExternalCallProcessor
from services.heartbeat_agentic import finalize_heartbeat, run_agentic_heartbeat
from services.heartbeat_runner import execute_heartbeat_decision
from services.reconsolidation import run_reconsolidation_step
from services.subconscious import run_subconscious_decider


load_config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("heartbeat_worker")

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", 1.0))
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", 3))


# ---------------------------------------------------------------------------
# HeartbeatWorker — Timer that checks if heartbeat is due and submits events
# ---------------------------------------------------------------------------


class HeartbeatWorker:
    """Timer that checks if heartbeat is due and submits gateway events.

    This worker does NOT execute heartbeats — it only decides WHEN to fire
    and submits the heartbeat payload as a gateway event. The GatewayConsumer
    dequeues and executes the actual heartbeat logic.
    """

    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=db_dsn_from_env(self.instance), min_size=1, max_size=5,
        )
        logger.info("HeartbeatWorker connected to database")

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("HeartbeatWorker disconnected")

    async def _submit_heartbeat_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_heartbeat(conn)
            if not payload:
                return
            heartbeat_id = payload.get("heartbeat_id")
            if heartbeat_id:
                logger.info(f"Heartbeat due: {heartbeat_id} — submitting to gateway")

        # Submit the full payload as a gateway event for the consumer
        try:
            gw = Gateway(self.pool)
            await gw.submit(
                EventSource.HEARTBEAT,
                f"heartbeat:{heartbeat_id or 'unknown'}",
                payload,
            )
        except Exception:
            logger.error("Failed to submit heartbeat event", exc_info=True)

    async def run(self) -> None:
        self.running = True
        logger.info("HeartbeatWorker (timer) starting...")
        await self.connect()

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat timer exiting.")
                        break
                    if not await self._is_agent_ready():
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    if not await self._is_active_hour():
                        logger.debug("Outside active hours; skipping heartbeat.")
                        await asyncio.sleep(POLL_INTERVAL * 10)
                        continue
                    await self._submit_heartbeat_if_due()
                except Exception as exc:
                    logger.error(f"Heartbeat timer error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("HeartbeatWorker (timer) stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False

    async def _is_agent_ready(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()"))
        except Exception:
            return False

    async def _is_active_hour(self) -> bool:
        """Check if the current time is within configured active hours."""
        if not self.pool:
            return True
        try:
            async with self.pool.acquire() as conn:
                active_hours = await conn.fetchval(
                    "SELECT get_config_text('heartbeat.active_hours')"
                )
                if not active_hours:
                    return True

                timezone_str = await conn.fetchval(
                    "SELECT get_config_text('heartbeat.timezone')"
                ) or "UTC"

                return _check_active_hours(active_hours, timezone_str)
        except Exception:
            return True


def _check_active_hours(active_hours: str, timezone_str: str) -> bool:
    """Parse an active hours string like '08:00-22:00' and check if now is within range."""
    try:
        parts = active_hours.strip().split("-")
        if len(parts) != 2:
            return True

        start_str, end_str = parts[0].strip(), parts[1].strip()
        start_h, start_m = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        end_h, end_m = int(end_str.split(":")[0]), int(end_str.split(":")[1])

        try:
            zone = ZoneInfo(timezone_str)
        except Exception:
            zone = tz.utc

        now = datetime.now(zone)
        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        else:
            return current_minutes >= start_minutes or current_minutes < end_minutes
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Heartbeat event handler — registered with GatewayConsumer
# ---------------------------------------------------------------------------


def _extract_heartbeat_context(payload: dict) -> dict:
    """Extract heartbeat context from the run_heartbeat() payload."""
    external_calls = payload.get("external_calls")
    if not isinstance(external_calls, list):
        return payload

    for call in external_calls:
        if not isinstance(call, dict):
            continue
        call_type = str(call.get("call_type") or "")
        if call_type == "think":
            call_input = call.get("input") or {}
            if isinstance(call_input, dict):
                context = call_input.get("context")
                if isinstance(context, dict):
                    return context
                return call_input

    return payload


async def _is_agentic_heartbeat_enabled(conn) -> bool:
    """Check if the agentic heartbeat loop is enabled via config."""
    try:
        val = await conn.fetchval("SELECT get_config('heartbeat.use_agentic_loop')")
        if val is None:
            return False
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)
    except Exception:
        return False


def create_heartbeat_handler(
    *,
    pool: asyncpg.Pool,
    bridge: RabbitMQBridge | None,
    tool_registry,
    call_processor: ExternalCallProcessor,
    stop_callback: Any = None,
):
    """Factory that returns a heartbeat event handler for the GatewayConsumer.

    The handler receives a GatewayEvent whose payload is the full run_heartbeat()
    output and executes the heartbeat (agentic or legacy path).
    """

    async def _publish_outbox(messages: list[dict]) -> None:
        if not messages or not bridge:
            return
        await bridge.publish_outbox_payloads(messages)

    async def handle_heartbeat(event: GatewayEvent) -> dict[str, Any] | None:
        payload = event.payload
        heartbeat_id = payload.get("heartbeat_id")
        if heartbeat_id:
            logger.info(f"Consumer executing heartbeat: {heartbeat_id}")

        # Publish outbox messages from initialization
        outbox_messages = payload.get("outbox_messages")
        if isinstance(outbox_messages, list):
            await _publish_outbox(outbox_messages)

        async with pool.acquire() as conn:
            # Agentic heartbeat path
            if await _is_agentic_heartbeat_enabled(conn) and tool_registry:
                if not heartbeat_id:
                    logger.warning("Agentic heartbeat: no heartbeat_id in payload")
                    return {"path": "agentic", "note": "no heartbeat_id"}

                context = _extract_heartbeat_context(payload)

                try:
                    result = await run_agentic_heartbeat(
                        conn,
                        pool=pool,
                        registry=tool_registry,
                        heartbeat_id=str(heartbeat_id),
                        context=context,
                    )
                    logger.info(
                        "Agentic heartbeat %s completed: %d tools, %d energy, reason=%s",
                        str(heartbeat_id)[:8],
                        len(result.get("tool_calls_made", [])),
                        result.get("energy_spent", 0),
                        result.get("stopped_reason", "?"),
                    )
                except Exception as exc:
                    logger.error(f"Agentic heartbeat failed: {exc}")
                    result = {
                        "text": f"Heartbeat failed: {exc}",
                        "tool_calls_made": [],
                        "energy_spent": 0,
                        "stopped_reason": "error",
                    }

                try:
                    fin = await finalize_heartbeat(
                        conn,
                        heartbeat_id=str(heartbeat_id),
                        result=result,
                    )
                    outbox_messages = fin.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await _publish_outbox(outbox_messages)
                except Exception as exc:
                    logger.error(f"Heartbeat finalization failed: {exc}")

                return {"path": "agentic", "stopped_reason": result.get("stopped_reason")}

            # Legacy heartbeat path
            external_calls = payload.get("external_calls")
            if not isinstance(external_calls, list):
                return {"path": "legacy", "note": "no external_calls"}

            for call in external_calls:
                if not isinstance(call, dict):
                    continue
                call_type = str(call.get("call_type") or "")
                call_input = call.get("input") or {}
                if not isinstance(call_input, dict):
                    call_input = {}
                try:
                    result = await call_processor.process_call_payload(conn, call_type, call_input)
                    applied = await call_processor.apply_result(conn, call, result)
                except Exception as exc:
                    logger.error(f"Error processing external call: {exc}")
                    continue

                if isinstance(applied, dict):
                    outbox_messages = applied.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await _publish_outbox(outbox_messages)

                if (
                    isinstance(result, dict)
                    and result.get("kind") == "heartbeat_decision"
                    and "decision" in result
                    and heartbeat_id
                ):
                    exec_result = await execute_heartbeat_decision(
                        conn,
                        heartbeat_id=str(heartbeat_id),
                        decision=result["decision"],
                        call_processor=call_processor,
                        pre_executed_actions=result.get("rlm_repl_actions"),
                    )
                    if isinstance(exec_result, dict):
                        outbox_messages = exec_result.get("outbox_messages")
                        if isinstance(outbox_messages, list):
                            await _publish_outbox(outbox_messages)
                        if exec_result.get("terminated") is True:
                            logger.info("Termination executed; stopping workers.")
                            if stop_callback:
                                stop_callback()
                    return {"path": "legacy"}

            return {"path": "legacy"}

    return handle_heartbeat


# ---------------------------------------------------------------------------
# MaintenanceWorker — runs directly (atomic check+execute tasks)
# ---------------------------------------------------------------------------


class MaintenanceWorker:
    """Subconscious maintenance loop: consolidates/prunes substrate on its own trigger."""

    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.bridge: RabbitMQBridge | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(self.instance), min_size=1, max_size=5)
        logger.info("Connected to database")
        self.bridge = RabbitMQBridge(self.pool)
        await self.bridge.ensure_ready()

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _publish_outbox(self, messages: list[dict]) -> None:
        if not messages:
            return
        if self.bridge:
            await self.bridge.publish_outbox_payloads(messages)

    async def _run_scheduled_tasks(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_scheduled_tasks(conn)
            if not isinstance(payload, dict):
                return
            outbox_messages = payload.get("outbox_messages")
            if isinstance(outbox_messages, list):
                await self._publish_outbox(outbox_messages)
            executed = payload.get("ran") or payload.get("executed_count") or payload.get("executed")
            ran_tasks = payload.get("ran_tasks") or []
            if executed:
                try:
                    await Gateway(self.pool).record(
                        EventSource.CRON,
                        "cron::scheduled",
                        {"executed": executed, "tasks": ran_tasks},
                    )
                except Exception:
                    logger.debug("Gateway record failed (non-fatal)", exc_info=True)
            # Recompute next_run_at for cron-expression tasks (Python-side croniter)
            cron_task_ids = payload.get("cron_task_ids") or []
            if cron_task_ids:
                try:
                    await recompute_cron_next_runs(conn, cron_task_ids)
                except Exception:
                    logger.warning("Cron recompute failed (non-fatal)", exc_info=True)

    async def _run_maintenance_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            stats = await run_maintenance_if_due(conn, {})
            if stats is None:
                return
            if not stats.get("skipped"):
                logger.info(f"Subconscious maintenance: {stats}")
                try:
                    await Gateway(self.pool).record(
                        EventSource.MAINTENANCE,
                        "maintenance::consolidation",
                        {"stats": stats},
                    )
                except Exception:
                    logger.debug("Gateway record failed (non-fatal)", exc_info=True)

    async def _run_subconscious_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            should_run = await should_run_subconscious_decider(conn)
            if not should_run:
                return
            result = await run_subconscious_decider(conn)
            await mark_subconscious_decider_run(conn)
            logger.info(f"Subconscious decider: {result}")

    async def _run_reconsolidation_if_pending(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            has_pending = await conn.fetchval("SELECT has_pending_reconsolidation()")
            if not has_pending:
                return
            result = await run_reconsolidation_step(conn)
            if not result.get("skipped"):
                logger.info(f"Reconsolidation step: {result}")

    async def run(self) -> None:
        self.running = True
        logger.info("Maintenance worker starting...")
        await self.connect()
        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; maintenance worker exiting.")
                        break
                    if not await self._is_agent_ready():
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    if self.bridge:
                        await self.bridge.poll_inbox_messages()
                    await self._run_scheduled_tasks()
                    await self._run_maintenance_if_due()
                    await self._run_subconscious_if_due()
                    await self._run_reconsolidation_if_pending()
                except Exception as exc:
                    logger.error(f"Maintenance loop error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("Maintenance worker stopping...")

    async def _is_agent_terminated(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return await is_agent_terminated(conn)
        except Exception:
            return False

    async def _is_agent_ready(self) -> bool:
        if not self.pool:
            return False
        try:
            async with self.pool.acquire() as conn:
                return bool(await conn.fetchval("SELECT is_agent_configured() AND is_init_complete()"))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Webhook event handler — registered with GatewayConsumer
# ---------------------------------------------------------------------------


def create_webhook_handler(*, pool: asyncpg.Pool):
    """Factory that returns a webhook event handler for the GatewayConsumer.

    Webhook events are recorded as episodic memories so the agent
    is aware that an external system sent a notification.
    """

    async def handle_webhook(event: GatewayEvent) -> dict[str, Any] | None:
        payload = event.payload
        source_name = event.session_key.removeprefix("webhook:")
        logger.info("Processing webhook event: %s (id=%d)", source_name, event.id)

        # Record the webhook as an episodic memory
        try:
            async with pool.acquire() as conn:
                summary = json.dumps(payload)[:500] if payload else "{}"
                await conn.fetchval(
                    """
                    SELECT create_episodic_memory(
                        p_content := $1,
                        p_importance := 0.4,
                        p_emotional_valence := 0.0,
                        p_context := $2::jsonb,
                        p_source_attribution := $3::jsonb,
                        p_trust_level := 0.7
                    )
                    """,
                    f"Received webhook from {source_name}: {summary}",
                    json.dumps({"type": "webhook", "source": source_name}),
                    json.dumps({
                        "kind": "webhook",
                        "ref": str(event.correlation_id),
                        "label": f"webhook:{source_name}",
                        "trust": 0.7,
                    }),
                )
        except Exception as exc:
            logger.warning("Failed to record webhook memory: %s", exc)

        return {"source": source_name, "recorded": True}

    return handle_webhook


# ---------------------------------------------------------------------------
# Main entrypoint — wires timer + consumer + maintenance
# ---------------------------------------------------------------------------


async def _amain(mode: str, instance: str | None = None) -> None:
    hb_timer = HeartbeatWorker(instance)
    maint_worker = MaintenanceWorker(instance)

    # Shared resources for the heartbeat consumer
    dsn = db_dsn_from_env(instance)
    consumer_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    from core.usage import set_usage_pool
    set_usage_pool(consumer_pool)
    bridge = RabbitMQBridge(consumer_pool)
    await bridge.ensure_ready()

    # Initialize tool registry for heartbeat execution
    tool_registry = None
    mcp_manager = None
    call_processor = ExternalCallProcessor(max_retries=MAX_RETRIES)
    try:
        from core.tools import create_default_registry, create_mcp_manager

        tool_registry = create_default_registry(consumer_pool)
        call_processor.set_tool_registry(tool_registry)
        logger.info("Tool registry initialized for consumer")

        mcp_manager = await create_mcp_manager(tool_registry)
        mcp_count = len(mcp_manager.list_servers())
        if mcp_count > 0:
            logger.info(f"Loaded {mcp_count} MCP server(s)")
    except Exception as e:
        logger.warning(f"Failed to initialize tool registry: {e}")

    # Build the gateway consumer
    consumer = GatewayConsumer(consumer_pool, poll_interval=POLL_INTERVAL)

    def _stop_all():
        hb_timer.stop()
        maint_worker.stop()
        consumer.stop()

    heartbeat_handler = create_heartbeat_handler(
        pool=consumer_pool,
        bridge=bridge,
        tool_registry=tool_registry,
        call_processor=call_processor,
        stop_callback=_stop_all,
    )
    consumer.register(EventSource.HEARTBEAT, heartbeat_handler)
    consumer.register(EventSource.WEBHOOK, create_webhook_handler(pool=consumer_pool))

    import signal

    def shutdown(signum, frame):
        _stop_all()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "both").strip().lower()
    instance_info = f" (instance: {instance})" if instance else ""
    logger.info(f"Starting worker in {mode} mode{instance_info}")

    try:
        if mode == "heartbeat":
            await asyncio.gather(hb_timer.run(), consumer.run())
        elif mode == "maintenance":
            await maint_worker.run()
        elif mode == "both":
            await asyncio.gather(hb_timer.run(), consumer.run(), maint_worker.run())
        else:
            raise ValueError("mode must be one of: heartbeat, maintenance, both")
    finally:
        # Cleanup consumer resources
        if mcp_manager:
            try:
                await mcp_manager.shutdown()
            except Exception:
                pass
        await consumer_pool.close()


def main() -> int:
    p = argparse.ArgumentParser(prog="hexis-worker", description="Run Hexis background workers.")
    p.add_argument(
        "--mode",
        choices=["heartbeat", "maintenance", "both"],
        default=os.getenv("HEXIS_WORKER_MODE", "both"),
        help="Which worker to run.",
    )
    p.add_argument(
        "--instance", "-i",
        default=os.getenv("HEXIS_INSTANCE"),
        help="Target a specific instance (overrides HEXIS_INSTANCE env var).",
    )
    args = p.parse_args()
    asyncio.run(_amain(args.mode, args.instance))
    return 0


__all__ = [
    "HeartbeatWorker",
    "MaintenanceWorker",
    "GatewayConsumer",
    "create_heartbeat_handler",
    "create_webhook_handler",
    "main",
    "MAX_RETRIES",
]
