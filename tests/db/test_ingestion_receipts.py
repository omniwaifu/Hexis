import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_record_and_get_ingestion_receipts(db_pool):
    async with db_pool.acquire() as conn:
        mem_id = await conn.fetchval(
            """
            INSERT INTO memories (type, content, embedding)
            VALUES ('semantic'::memory_type, 'Receipt memory', array_fill(0.1, ARRAY[embedding_dimension()])::vector)
            RETURNING id
            """
        )

        items = [
            {"source_file": "/tmp/a.txt", "chunk_index": 0, "content_hash": "hash_a", "memory_id": str(mem_id)},
            {"source_file": "/tmp/a.txt", "chunk_index": 1, "content_hash": "hash_a", "memory_id": str(mem_id)},
        ]
        inserted = await conn.fetchval(
            "SELECT record_ingestion_receipts($1::jsonb)",
            json.dumps(items),
        )
        assert int(inserted) == 1

        receipts = await conn.fetch("SELECT * FROM get_ingestion_receipts($1::text, $2::text[])", "/tmp/a.txt", ["hash_a", "hash_b"])
        mapping = {row["content_hash"]: row["memory_id"] for row in receipts}
        assert mapping.get("hash_a") == mem_id
        assert "hash_b" not in mapping

        await conn.execute("DELETE FROM memories WHERE id = $1", mem_id)
