import json

import pytest

from tests.utils import _coerce_json, get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_get_goals_by_priority_filters(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('heartbeat.max_active_goals', '10'::jsonb)")

            active_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, NULL, NULL)",
                f"Active {get_test_identifier('goal')}",
                "active goal",
                "curiosity",
                "active",
            )
            queued_id = await conn.fetchval(
                "SELECT create_goal($1, $2, $3, $4, NULL, NULL)",
                f"Queued {get_test_identifier('goal')}",
                "queued goal",
                "curiosity",
                "queued",
            )

            rows = await conn.fetch("SELECT * FROM get_goals_by_priority()")
            priorities = {row["priority"] for row in rows}
            assert "active" in priorities
            assert "queued" in priorities

            queued_rows = await conn.fetch(
                "SELECT * FROM get_goals_by_priority('queued')"
            )
            assert queued_rows
            assert all(row["priority"] == "queued" for row in queued_rows)
            assert queued_id in {row["id"] for row in queued_rows}

            assert active_id != queued_id
        finally:
            await tr.rollback()


async def test_get_worldview_snapshot_filters_by_confidence(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            high_id = await conn.fetchval(
                "SELECT create_worldview_memory($1, $2, $3, $4, $5, $6)",
                f"High confidence {get_test_identifier('worldview')}",
                "belief",
                0.9,
                0.8,
                0.9,
                "test",
            )
            low_id = await conn.fetchval(
                "SELECT create_worldview_memory($1, $2, $3, $4, $5, $6)",
                f"Low confidence {get_test_identifier('worldview')}",
                "belief",
                0.2,
                0.5,
                0.4,
                "test",
            )
            assert high_id is not None
            assert low_id is not None

            rows = await conn.fetch(
                "SELECT * FROM get_worldview_snapshot(10, 0.5)"
            )
            contents = {row["content"] for row in rows}
            assert any("High confidence" in content for content in contents)
            assert all("Low confidence" not in content for content in contents)
        finally:
            await tr.rollback()


async def test_get_emotional_patterns_context_returns_entries(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            pattern = f"pattern-{get_test_identifier('emotion')}"
            mem_id = await conn.fetchval(
                """
                SELECT create_strategic_memory(
                    $1,
                    $2,
                    0.7,
                    $3::jsonb,
                    NULL,
                    0.6,
                    NULL,
                    NULL
                )
                """,
                f"Emotional pattern {pattern}",
                "emotional pattern",
                json.dumps(
                    {
                        "kind": "emotional_pattern",
                        "pattern": pattern,
                        "frequency": 3,
                        "unprocessed": True,
                    }
                ),
            )
            await conn.execute(
                """
                UPDATE memories
                SET metadata = jsonb_set(metadata, '{supporting_evidence,kind}', '\"emotional_pattern\"'::jsonb)
                WHERE id = $1::uuid
                """,
                mem_id,
            )
            kind = await conn.fetchval(
                "SELECT metadata->'supporting_evidence'->>'kind' FROM memories WHERE id = $1::uuid",
                mem_id,
            )
            assert kind == "emotional_pattern"

            result = _coerce_json(await conn.fetchval("SELECT get_emotional_patterns_context(5)"))
            assert result
            assert any(pattern in entry.get("pattern", "") for entry in result)
        finally:
            await tr.rollback()


async def test_get_subconscious_and_chat_contexts(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            query_text = f"context memory {get_test_identifier('context')}"
            await conn.fetchval(
                "SELECT create_semantic_memory($1, 0.8, ARRAY['context'], NULL, NULL, 0.6)",
                query_text,
            )
            await conn.fetchval(
                """
                SELECT create_episodic_memory(
                    $1,
                    NULL,
                    jsonb_build_object('heartbeat_id', 'hb-test'),
                    NULL,
                    0.1,
                    CURRENT_TIMESTAMP,
                    0.4
                )
                """,
                f"recent {query_text}",
            )

            subconscious = _coerce_json(
                await conn.fetchval(
                    "SELECT get_subconscious_context(5, 5, 5, 2, 2, 0, 0)"
                )
            )
            assert "recent_memories" in subconscious
            assert "emotional_state" in subconscious

            chat_ctx = _coerce_json(
                await conn.fetchval(
                    "SELECT get_chat_context($1, 5)",
                    query_text,
                )
            )
            assert "relevant_memories" in chat_ctx
            assert any(
                query_text in entry.get("content", "")
                for entry in chat_ctx["relevant_memories"]
            )

            sub_chat_ctx = _coerce_json(
                await conn.fetchval(
                    "SELECT get_subconscious_chat_context($1, 5)",
                    query_text,
                )
            )
            assert any(
                query_text in entry.get("content", "")
                for entry in sub_chat_ctx["relevant_memories"]
            )
        finally:
            await tr.rollback()


async def test_record_subconscious_exchange_and_chat_turn(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            prompt = f"prompt {get_test_identifier('exchange')}"
            response = {"emotional_state": {"valence": 0.2}, "summary": "ok"}
            memory_id = await conn.fetchval(
                "SELECT record_subconscious_exchange($1, $2::jsonb)",
                prompt,
                json.dumps(response),
            )
            row = await conn.fetchrow(
                "SELECT content, type FROM memories WHERE id = $1",
                memory_id,
            )
            assert row is not None
            assert row["type"] == "episodic"
            assert prompt in row["content"]

            chat_id = await conn.fetchval(
                "SELECT record_chat_turn($1, $2, '{}'::jsonb)",
                "hello",
                "hi there",
            )
            chat_row = await conn.fetchrow(
                "SELECT content FROM memories WHERE id = $1",
                chat_id,
            )
            assert chat_row is not None
            assert "User: hello" in chat_row["content"]
            assert "Assistant: hi there" in chat_row["content"]
        finally:
            await tr.rollback()


async def test_get_contradictions_context_returns_pairs(db_pool, ensure_embedding_service):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            mem_a = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding)
                VALUES ('semantic', $1, array_fill(0.6, ARRAY[embedding_dimension()])::vector)
                RETURNING id
                """,
                f"A {get_test_identifier('contradictions')}",
            )
            mem_b = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding)
                VALUES ('semantic', $1, array_fill(0.7, ARRAY[embedding_dimension()])::vector)
                RETURNING id
                """,
                f"B {get_test_identifier('contradictions')}",
            )

            await conn.fetchval("SELECT sync_memory_node($1::uuid)", mem_a)
            await conn.fetchval("SELECT sync_memory_node($1::uuid)", mem_b)
            await conn.execute(
                "SELECT create_memory_relationship($1::uuid, $2::uuid, 'CONTRADICTS', '{}'::jsonb)",
                mem_a,
                mem_b,
            )

            contradictions = _coerce_json(
                await conn.fetchval("SELECT get_contradictions_context(5)")
            )
            assert contradictions
            contents = {entry["content_a"] for entry in contradictions} | {
                entry["content_b"] for entry in contradictions
            }
            assert any("A" in content for content in contents)
            assert any("B" in content for content in contents)
        finally:
            await tr.rollback()
