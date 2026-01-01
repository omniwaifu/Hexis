import json

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.db]


async def test_apply_termination_confirmation(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            call_id = await conn.fetchval(
                """
                INSERT INTO external_calls (call_type, input)
                VALUES ('think', $1::jsonb)
                RETURNING id
                """,
                json.dumps({"params": {"last_will": "Final message"}}),
            )

            declined_raw = await conn.fetchval(
                "SELECT apply_termination_confirmation($1::uuid, $2::jsonb)",
                call_id,
                json.dumps({"confirm": False}),
            )
            declined = json.loads(declined_raw) if isinstance(declined_raw, str) else declined_raw
            assert declined.get("confirmed") is False
            assert declined.get("terminated") is False

            confirmed_raw = await conn.fetchval(
                "SELECT apply_termination_confirmation($1::uuid, $2::jsonb)",
                call_id,
                json.dumps(
                    {
                        "confirm": True,
                        "last_will": "Goodbye",
                        "options": {"skip_graph": True},
                        "farewells": [{"message": "See you."}],
                    }
                ),
            )
            confirmed = json.loads(confirmed_raw) if isinstance(confirmed_raw, str) else confirmed_raw
            assert confirmed.get("confirmed") is True
            assert confirmed.get("terminated") is True
            assert confirmed.get("result", {}).get("terminated") is True
        finally:
            await tr.rollback()
