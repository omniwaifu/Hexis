from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_config import load_llm_config
from core.llm_json import chat_json
from core.subconscious import apply_subconscious_observations, get_subconscious_context
from services.prompt_resources import load_subconscious_prompt

logger = logging.getLogger(__name__)


def _normalize_observations(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    def _as_list(val: Any) -> list[dict[str, Any]]:
        if isinstance(val, list):
            return [v for v in val if isinstance(v, dict)]
        return []

    emotional = doc.get("emotional_observations")
    if emotional is None:
        emotional = doc.get("emotional_patterns")
    consolidation = doc.get("consolidation_observations")
    if consolidation is None:
        consolidation = doc.get("consolidation_suggestions")

    return {
        "narrative_observations": _as_list(doc.get("narrative_observations")),
        "relationship_observations": _as_list(doc.get("relationship_observations")),
        "contradiction_observations": _as_list(doc.get("contradiction_observations")),
        "emotional_observations": _as_list(emotional),
        "consolidation_observations": _as_list(consolidation),
    }


def _coerce_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


async def _build_context(conn) -> dict[str, Any]:
    raw = await get_subconscious_context(conn)
    context = _coerce_json(raw) if raw is not None else {}
    return context if isinstance(context, dict) else {}


# ---------------------------------------------------------------------------
# Dopamine: reward prediction error computation
# ---------------------------------------------------------------------------


async def _compute_dopamine_rpe(
    conn,
    context: dict[str, Any],
    doc: dict[str, Any],
) -> dict[str, Any]:
    """Compute reward prediction error and fire dopamine spike if warranted.

    Compares the current affective state (actual emotional position) against
    what the tonic dopamine level predicted (baseline expectation).

    RPE = actual_valence - expected_valence, amplified by arousal.

    Called after the subconscious LLM observations are applied, so the
    emotional landscape has been freshly assessed.
    """
    RPE_THRESHOLD = 0.15

    try:
        # Read current dopamine state
        da_raw = await conn.fetchval("SELECT get_dopamine_state()")
        da_state = _coerce_json(da_raw) if da_raw else {}
        if not isinstance(da_state, dict):
            da_state = {}
        tonic = float(da_state.get("tonic", 0.5))

        # Read current affective state (reflects mood updates + recent heartbeats)
        affect_raw = await conn.fetchval("SELECT get_current_affective_state()")
        affect = _coerce_json(affect_raw) if affect_raw else {}
        if not isinstance(affect, dict):
            affect = {}

        current_valence = float(affect.get("valence", 0.0))
        current_arousal = float(affect.get("arousal", 0.5))

        # The tonic level encodes what we "expect" as normal.
        # Tonic 0.5 → expect valence ~0.0 (neutral)
        # Tonic 0.8 → expect valence ~0.6 (things have been going well)
        # Tonic 0.2 → expect valence ~-0.6 (things have been going poorly)
        expected_valence = (tonic - 0.5) * 2.0

        # RPE: how much reality diverges from expectation
        raw_rpe = current_valence - expected_valence

        # Arousal amplifies the signal — surprising events have higher arousal
        arousal_multiplier = 0.5 + current_arousal * 0.5  # range [0.5, 1.0]
        rpe = raw_rpe * arousal_multiplier

        # Clamp to [-1, 1]
        rpe = max(-1.0, min(1.0, rpe))

        if abs(rpe) < RPE_THRESHOLD:
            return {"fired": False, "rpe": rpe, "tonic": tonic}

        # Build trigger description from context
        trigger_parts = []

        # Use the LLM's emotional state assessment if available
        emo_state = doc.get("emotional_state", {})
        if isinstance(emo_state, dict) and emo_state.get("primary_emotion"):
            trigger_parts.append(f"feeling {emo_state['primary_emotion']}")

        # Note any emotional observations
        emo_obs = doc.get("emotional_observations") or doc.get("emotional_patterns") or []
        if isinstance(emo_obs, list):
            for obs in emo_obs[:2]:
                if isinstance(obs, dict):
                    pattern = obs.get("pattern") or obs.get("summary") or obs.get("theme", "")
                    if pattern:
                        trigger_parts.append(str(pattern)[:100])

        # Note relationship observations (social reward/punishment)
        rel_obs = doc.get("relationship_observations") or []
        if isinstance(rel_obs, list):
            for obs in rel_obs[:2]:
                if isinstance(obs, dict):
                    entity = obs.get("entity", "")
                    change = obs.get("change_type", "")
                    if entity and change:
                        trigger_parts.append(f"{change} with {entity}")

        trigger = "; ".join(trigger_parts) if trigger_parts else "affective state shift"

        # Fire the spike
        result_raw = await conn.fetchval(
            "SELECT fire_dopamine_spike($1, $2)",
            rpe,
            trigger,
        )
        result = _coerce_json(result_raw) if result_raw else {}
        if not isinstance(result, dict):
            result = {}

        logger.info(
            "Dopamine spike fired: RPE=%.3f tonic=%.2f→%.2f boosted=%d trigger=%s",
            rpe,
            tonic,
            float(result.get("tonic_new", tonic)),
            int(result.get("memories_boosted", 0)),
            trigger[:80],
        )

        return {"fired": True, "rpe": rpe, "result": result}

    except Exception as exc:
        logger.debug("Dopamine RPE computation failed: %s", exc)
        return {"fired": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_subconscious_decider(conn) -> dict[str, Any]:
    llm_config = await load_llm_config(conn, "llm.subconscious", fallback_key="llm.heartbeat")
    context = await _build_context(conn)
    user_prompt = f"Context (JSON):\n{json.dumps(context)[:12000]}"
    try:
        doc, raw = await chat_json(
            llm_config=llm_config,
            messages=[
                {"role": "system", "content": load_subconscious_prompt().strip()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1800,
            response_format={"type": "json_object"},
            fallback={},
        )
    except Exception as exc:
        return {"skipped": True, "reason": str(exc)}

    if not isinstance(doc, dict):
        doc = {}

    observations = _normalize_observations(doc)
    applied = await apply_subconscious_observations(conn, observations)

    # Dopamine: compute RPE and fire spike if warranted
    dopamine = await _compute_dopamine_rpe(conn, context, doc)

    return {"applied": applied, "dopamine": dopamine, "raw_response": raw}
