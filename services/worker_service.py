from __future__ import annotations

import argparse
import asyncio
import logging
import os
import asyncpg
from dotenv import load_dotenv

from core.agent_api import db_dsn_from_env
from core.rabbitmq_bridge import RabbitMQBridge
from core.state import (
    is_agent_terminated,
    mark_subconscious_decider_run,
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


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("heartbeat_worker")

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", 1.0))
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", 3))


class HeartbeatWorker:
    """Stateless worker that bridges the database and external APIs."""

    def __init__(self, instance: str | None = None):
        self.instance = instance or os.getenv("HEXIS_INSTANCE")
        self.pool: asyncpg.Pool | None = None
        self.running = False
        self.bridge: RabbitMQBridge | None = None
        self.call_processor = ExternalCallProcessor(max_retries=MAX_RETRIES)
        self._tool_registry = None
        self._mcp_manager = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(dsn=db_dsn_from_env(self.instance), min_size=2, max_size=10)
        logger.info("Connected to database")
        self.bridge = RabbitMQBridge(self.pool)
        await self.bridge.ensure_ready()

        # Initialize tool registry
        try:
            from core.tools import create_default_registry, create_mcp_manager

            self._tool_registry = create_default_registry(self.pool)
            self.call_processor.set_tool_registry(self._tool_registry)
            logger.info("Tool registry initialized")

            # Load MCP servers
            self._mcp_manager = await create_mcp_manager(self._tool_registry)
            mcp_count = len(self._mcp_manager.list_servers())
            if mcp_count > 0:
                logger.info(f"Loaded {mcp_count} MCP server(s)")

        except Exception as e:
            logger.warning(f"Failed to initialize tool registry: {e}")

    async def disconnect(self) -> None:
        # Shutdown MCP servers
        if self._mcp_manager:
            try:
                await self._mcp_manager.shutdown()
                logger.info("MCP servers shut down")
            except Exception as e:
                logger.warning(f"Error shutting down MCP servers: {e}")

        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from database")

    async def _publish_outbox(self, messages: list[dict]) -> None:
        if not messages:
            return
        if self.bridge:
            await self.bridge.publish_outbox_payloads(messages)

    async def _run_heartbeat_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            payload = await run_heartbeat(conn)
            if not payload:
                return
            heartbeat_id = payload.get("heartbeat_id")
            if heartbeat_id:
                logger.info(f"Heartbeat started: {heartbeat_id}")

            outbox_messages = payload.get("outbox_messages")
            if isinstance(outbox_messages, list):
                await self._publish_outbox(outbox_messages)

            # Check if agentic heartbeat is enabled
            if await self._is_agentic_heartbeat_enabled(conn) and self._tool_registry:
                await self._run_agentic_heartbeat(conn, payload, heartbeat_id)
                return

            external_calls = payload.get("external_calls")
            if not isinstance(external_calls, list):
                return

            for call in external_calls:
                if not isinstance(call, dict):
                    continue
                call_type = str(call.get("call_type") or "")
                call_input = call.get("input") or {}
                if not isinstance(call_input, dict):
                    call_input = {}
                try:
                    result = await self.call_processor.process_call_payload(conn, call_type, call_input)
                    applied = await self.call_processor.apply_result(conn, call, result)
                except Exception as exc:
                    logger.error(f"Error processing external call: {exc}")
                    continue

                if isinstance(applied, dict):
                    outbox_messages = applied.get("outbox_messages")
                    if isinstance(outbox_messages, list):
                        await self._publish_outbox(outbox_messages)

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
                        call_processor=self.call_processor,
                        pre_executed_actions=result.get("rlm_repl_actions"),
                    )
                    if isinstance(exec_result, dict):
                        outbox_messages = exec_result.get("outbox_messages")
                        if isinstance(outbox_messages, list):
                            await self._publish_outbox(outbox_messages)
                        if exec_result.get("terminated") is True:
                            logger.info("Termination executed; stopping workers.")
                            self.stop()
                    return

    async def _is_agentic_heartbeat_enabled(self, conn) -> bool:
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

    async def _run_agentic_heartbeat(self, conn, payload: dict, heartbeat_id: str | None) -> None:
        """Run heartbeat using the unified AgentLoop instead of legacy external calls."""
        if not heartbeat_id:
            logger.warning("Agentic heartbeat: no heartbeat_id in payload")
            return

        # Extract context from the first think external call
        context = self._extract_heartbeat_context(payload)

        try:
            result = await run_agentic_heartbeat(
                conn,
                pool=self.pool,
                registry=self._tool_registry,
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
                await self._publish_outbox(outbox_messages)
        except Exception as exc:
            logger.error(f"Heartbeat finalization failed: {exc}")

    @staticmethod
    def _extract_heartbeat_context(payload: dict) -> dict:
        """Extract heartbeat context from the run_heartbeat() payload.

        The DB function returns external_calls with the heartbeat context
        embedded in the first think call's input.context field.
        """
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
                    # If context is not nested, the input itself may be the context
                    return call_input

        # Fallback: return the whole payload as context
        return payload

    async def run(self) -> None:
        self.running = True
        logger.info("Heartbeat worker starting...")
        await self.connect()

        try:
            while self.running:
                try:
                    if await self._is_agent_terminated():
                        logger.info("Agent is terminated; heartbeat worker exiting.")
                        break
                    if not await self._is_agent_ready():
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    await self._run_heartbeat_if_due()
                except Exception as exc:
                    logger.error(f"Worker loop error: {exc}")
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await self.disconnect()

    def stop(self) -> None:
        self.running = False
        logger.info("Heartbeat worker stopping...")

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

    async def _run_maintenance_if_due(self) -> None:
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            stats = await run_maintenance_if_due(conn, {})
            if stats is None:
                return
            if not stats.get("skipped"):
                logger.info(f"Subconscious maintenance: {stats}")

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


async def _amain(mode: str, instance: str | None = None) -> None:
    hb_worker = HeartbeatWorker(instance)
    maint_worker = MaintenanceWorker(instance)

    import signal

    def shutdown(signum, frame):
        hb_worker.stop()
        maint_worker.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = (mode or "both").strip().lower()
    instance_info = f" (instance: {instance})" if instance else ""
    logger.info(f"Starting worker in {mode} mode{instance_info}")

    if mode == "heartbeat":
        await hb_worker.run()
        return
    if mode == "maintenance":
        await maint_worker.run()
        return
    if mode == "both":
        await asyncio.gather(hb_worker.run(), maint_worker.run())
        return
    raise ValueError("mode must be one of: heartbeat, maintenance, both")


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
    "main",
    "MAX_RETRIES",
]
