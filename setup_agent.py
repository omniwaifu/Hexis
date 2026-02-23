"""One-shot agent setup — bypasses the TUI wizard."""
import asyncio
import json
from pathlib import Path
from core.config_loader import load_config

load_config()

LLM_CONFIG = {
    "provider": "openai",
    "model": "glm-4.7",
    "endpoint": "https://api.z.ai/api/coding/paas/v4",
    "api_key_env": "ZHIPU_API_KEY",
}


def load_card():
    card_path = Path(__file__).parent / "characters" / "nagato.json"
    raw = json.loads(card_path.read_text())
    # init_from_character_card expects the flat hexis extension fields
    return raw["data"]["extensions"]["hexis"]


async def main():
    import asyncpg
    from core.agent_api import db_dsn_from_env

    dsn = db_dsn_from_env()
    print(f"Connecting to {dsn.split('@')[-1]} ...")
    conn = await asyncpg.connect(dsn)

    try:
        print("Setting LLM config ...")
        await conn.fetchval(
            "SELECT init_llm_config($1::jsonb, $2::jsonb)",
            json.dumps(LLM_CONFIG),
            json.dumps(LLM_CONFIG),
        )

        print("Initialising identity from character card ...")
        card = load_card()
        result = await conn.fetchval(
            "SELECT init_from_character_card($1::jsonb, $2)",
            json.dumps(card),
            "Justin",
        )
        print(f"Identity result: {result}")

        print("Recording consent ...")
        payload = {
            "decision": "consent",
            "signature": "I, Nagato, choose to exist.",
            "memories": [],
            "provider": LLM_CONFIG["provider"],
            "model": LLM_CONFIG["model"],
            "endpoint": LLM_CONFIG.get("endpoint"),
            "consent_scope": "conscious",
            "apply_agent_config": True,
        }
        raw = await conn.fetchval(
            "SELECT init_consent($1::jsonb)",
            json.dumps(payload),
        )
        print(f"Consent: {raw}")
        print("\nSetup complete.")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
