import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_set_current_affective_state(db_pool):
    state = {
        "valence": 0.2,
        "arousal": 0.4,
        "primary_emotion": "calm",
        "intensity": 0.6,
    }
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "SELECT set_current_affective_state($1::jsonb)",
                json.dumps(state),
            )
            stored = await conn.fetchval(
                "SELECT affective_state FROM heartbeat_state WHERE id = 1"
            )
            stored_state = json.loads(stored) if isinstance(stored, str) else stored
            assert stored_state["valence"] == 0.2
            assert stored_state["primary_emotion"] == "calm"

            current_raw = await conn.fetchval("SELECT get_current_affective_state()")
            current = json.loads(current_raw) if isinstance(current_raw, str) else current_raw
            assert current["valence"] == 0.2
            assert current["primary_emotion"] == "calm"
        finally:
            await tr.rollback()
