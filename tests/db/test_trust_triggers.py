import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_sync_memory_trust(db_pool):
    sources = [
        {"kind": "web", "ref": "https://example.com/a", "trust": 1.0},
        {"kind": "paper", "ref": "doi:10.1/test", "trust": 0.8},
    ]
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            memory_id = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding, trust_level, source_attribution)
                VALUES (
                    'semantic',
                    'Trust sync',
                    array_fill(0.1, ARRAY[embedding_dimension()])::vector,
                    0.1,
                    '{}'::jsonb
                )
                RETURNING id
                """
            )
            await conn.execute(
                """
                INSERT INTO semantic_memories (memory_id, confidence, source_references)
                VALUES ($1, 0.9, $2::jsonb)
                """,
                memory_id,
                json.dumps(sources),
            )

            await conn.execute("SELECT sync_memory_trust($1::uuid)", memory_id)
            expected = await conn.fetchval(
                "SELECT compute_semantic_trust(0.9, $1::jsonb, compute_worldview_alignment($2::uuid))",
                json.dumps(sources),
                memory_id,
            )
            row = await conn.fetchrow(
                "SELECT trust_level, source_attribution FROM memories WHERE id = $1",
                memory_id,
            )
            source_attribution = (
                json.loads(row["source_attribution"])
                if isinstance(row["source_attribution"], str)
                else row["source_attribution"]
            )
            assert float(row["trust_level"]) == pytest.approx(float(expected), rel=0.02)
            assert source_attribution["ref"] in {"https://example.com/a", "doi:10.1/test"}
        finally:
            await tr.rollback()


async def test_trg_sync_semantic_trust_updates_trust(db_pool):
    sources = [{"kind": "web", "ref": "https://example.com", "trust": 0.9}]
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            memory_id = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding, trust_level)
                VALUES (
                    'semantic',
                    'Trigger trust',
                    array_fill(0.2, ARRAY[embedding_dimension()])::vector,
                    0.0
                )
                RETURNING id
                """
            )

            # trg_sync_semantic_trust should fire on insert.
            await conn.execute(
                """
                INSERT INTO semantic_memories (memory_id, confidence, source_references)
                VALUES ($1, 0.7, $2::jsonb)
                """,
                memory_id,
                json.dumps(sources),
            )

            trust = await conn.fetchval("SELECT trust_level FROM memories WHERE id = $1", memory_id)
            assert float(trust) > 0.0
        finally:
            await tr.rollback()


async def test_trg_sync_worldview_influence_trust_updates_alignment(db_pool):
    sources = [{"kind": "web", "ref": "https://example.com", "trust": 1.0}]
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            memory_id = await conn.fetchval(
                """
                INSERT INTO memories (type, content, embedding, trust_level)
                VALUES (
                    'semantic',
                    'Worldview trigger',
                    array_fill(0.3, ARRAY[embedding_dimension()])::vector,
                    0.1
                )
                RETURNING id
                """
            )
            await conn.execute(
                """
                INSERT INTO semantic_memories (memory_id, confidence, source_references)
                VALUES ($1, 0.9, $2::jsonb)
                """,
                memory_id,
                json.dumps(sources),
            )

            baseline_trust = await conn.fetchval("SELECT trust_level FROM memories WHERE id = $1", memory_id)

            worldview_id = await conn.fetchval(
                """
                INSERT INTO worldview_primitives (category, belief, confidence)
                VALUES ('test', 'belief', 0.5)
                RETURNING id
                """
            )

            # trg_sync_worldview_influence_trust should fire on insert.
            await conn.execute(
                """
                INSERT INTO worldview_memory_influences (worldview_id, memory_id, influence_type, strength)
                VALUES ($1, $2, 'support', 1.0)
                """,
                worldview_id,
                memory_id,
            )

            updated_trust = await conn.fetchval("SELECT trust_level FROM memories WHERE id = $1", memory_id)
            updated_confidence = await conn.fetchval(
                "SELECT confidence FROM worldview_primitives WHERE id = $1",
                worldview_id,
            )
            assert float(updated_trust) > float(baseline_trust)
            assert float(updated_confidence) > 0.5
        finally:
            await tr.rollback()
