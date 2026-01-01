import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_normalize_source_reference(db_pool):
    source = {"kind": "web", "ref": "http://example.com", "trust": 1.5}
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT normalize_source_reference($1::jsonb)", json.dumps(source))
        normalized = json.loads(raw) if isinstance(raw, str) else raw
        assert normalized["kind"] == "web"
        assert normalized["ref"] == "http://example.com"
        assert normalized["trust"] == 1.0
        assert "observed_at" in normalized

        empty = await conn.fetchval("SELECT normalize_source_reference('[]'::jsonb)")
        empty_val = json.loads(empty) if isinstance(empty, str) else empty
        assert empty_val == {}


async def test_normalize_and_dedupe_sources(db_pool):
    sources = [
        {"kind": "paper", "ref": "doi:1", "observed_at": "2020-01-01T00:00:00Z", "trust": 0.7},
        {"kind": "paper", "ref": "doi:1", "observed_at": "2021-01-01T00:00:00Z", "trust": 0.9},
    ]
    async with db_pool.acquire() as conn:
        raw = await conn.fetchval("SELECT normalize_source_references($1::jsonb)", json.dumps(sources))
        normalized = json.loads(raw) if isinstance(raw, str) else raw
        assert len(normalized) == 2

        raw = await conn.fetchval("SELECT dedupe_source_references($1::jsonb)", json.dumps(sources))
        deduped = json.loads(raw) if isinstance(raw, str) else raw
        assert len(deduped) == 1
        assert deduped[0]["observed_at"].startswith("2021-01-01")


async def test_source_reinforcement_score(db_pool):
    async with db_pool.acquire() as conn:
        zero = await conn.fetchval("SELECT source_reinforcement_score('[]'::jsonb)")
        assert float(zero) == 0.0

        sources = [{"kind": "web", "ref": "a", "trust": 0.9}, {"kind": "web", "ref": "b", "trust": 0.9}]
        score = await conn.fetchval("SELECT source_reinforcement_score($1::jsonb)", json.dumps(sources))
        assert 0.0 < float(score) <= 1.0


async def test_compute_semantic_trust(db_pool):
    sources = [{"kind": "web", "ref": "a", "trust": 0.9}, {"kind": "web", "ref": "b", "trust": 0.9}]
    async with db_pool.acquire() as conn:
        trust = await conn.fetchval(
            "SELECT compute_semantic_trust(0.9, $1::jsonb, 0.5)",
            json.dumps(sources),
        )
        assert 0.0 < float(trust) <= 1.0


async def test_worldview_alignment_and_trust_sync(db_pool):
    async with db_pool.acquire() as conn:
        mem_id = await conn.fetchval(
            """
            SELECT create_semantic_memory(
                $1::text,
                0.8::float,
                NULL,
                NULL,
                $2::jsonb,
                0.5,
                NULL,
                NULL
            )
            """,
            "Trust test",
            json.dumps([{"kind": "web", "ref": "a", "trust": 0.5}]),
        )

        worldview_id = await conn.fetchval(
            """
            INSERT INTO worldview_primitives (category, belief, confidence)
            VALUES ('test', 'belief', 0.6)
            RETURNING id
            """
        )

        await conn.execute(
            """
            INSERT INTO worldview_memory_influences (worldview_id, memory_id, influence_type, strength)
            VALUES ($1, $2, 'support', 1.0)
            """,
            worldview_id,
            mem_id,
        )

        alignment = await conn.fetchval("SELECT compute_worldview_alignment($1::uuid)", mem_id)
        assert 0.0 <= float(alignment) <= 1.0

        profile_raw = await conn.fetchval("SELECT get_memory_truth_profile($1::uuid)", mem_id)
        profile = json.loads(profile_raw) if isinstance(profile_raw, str) else profile_raw
        assert profile["type"] == "semantic"
        assert profile["source_count"] >= 1

        # Trigger trust sync via update to semantic sources.
        await conn.execute(
            "UPDATE semantic_memories SET source_references = $1::jsonb WHERE memory_id = $2",
            json.dumps([{"kind": "paper", "ref": "b", "trust": 0.9}]),
            mem_id,
        )
        trust = await conn.fetchval("SELECT trust_level FROM memories WHERE id = $1", mem_id)
        assert trust is not None

        await conn.execute("DELETE FROM worldview_memory_influences WHERE worldview_id = $1", worldview_id)
        await conn.execute("DELETE FROM worldview_primitives WHERE id = $1", worldview_id)
        await conn.execute("DELETE FROM memories WHERE id = $1", mem_id)


async def test_update_worldview_confidence_from_influences(db_pool):
    async with db_pool.acquire() as conn:
        worldview_id = await conn.fetchval(
            """
            INSERT INTO worldview_primitives (category, belief, confidence)
            VALUES ('test', 'belief', 0.4)
            RETURNING id
            """
        )
        mem_id = await conn.fetchval(
            "INSERT INTO memories (type, content, embedding, trust_level) VALUES ('semantic', 'evidence', array_fill(0.1, ARRAY[embedding_dimension()])::vector, 1.0) RETURNING id"
        )
        await conn.execute(
            """
            INSERT INTO worldview_memory_influences (worldview_id, memory_id, influence_type, strength)
            VALUES ($1, $2, 'support', 1.0)
            """,
            worldview_id,
            mem_id,
        )

        before = await conn.fetchval("SELECT confidence FROM worldview_primitives WHERE id = $1", worldview_id)
        await conn.execute("SELECT update_worldview_confidence_from_influences($1::uuid)", worldview_id)
        after = await conn.fetchval("SELECT confidence FROM worldview_primitives WHERE id = $1", worldview_id)
        assert after >= before

        await conn.execute("DELETE FROM worldview_memory_influences WHERE worldview_id = $1", worldview_id)
        await conn.execute("DELETE FROM worldview_primitives WHERE id = $1", worldview_id)
        await conn.execute("DELETE FROM memories WHERE id = $1", mem_id)
