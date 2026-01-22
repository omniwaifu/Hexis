-- Hexis schema: core heartbeat functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION start_heartbeat()
RETURNS JSONB AS $$
DECLARE
    heartbeat_id UUID;
    state_record RECORD;
    base_regen FLOAT;
    max_energy FLOAT;
    new_energy FLOAT;
    context JSONB;
    decision_max_tokens INT;
    hb_number INT;
    external_calls JSONB := '[]'::jsonb;
BEGIN
    IF NOT is_agent_configured() THEN
        RETURN NULL;
    END IF;
    IF NOT is_init_complete() THEN
        RETURN NULL;
    END IF;

    PERFORM ensure_emotion_bootstrap();
    PERFORM ensure_self_node();
    PERFORM ensure_current_life_chapter();
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    base_regen := get_config_float('heartbeat.base_regeneration');
    max_energy := get_config_float('heartbeat.max_energy');
    new_energy := LEAST(state_record.current_energy + base_regen, max_energy);
    hb_number := state_record.heartbeat_count + 1;
    heartbeat_id := gen_random_uuid();
    PERFORM update_drives();
    UPDATE heartbeat_state SET
        current_energy = new_energy,
        heartbeat_count = hb_number,
        last_heartbeat_at = CURRENT_TIMESTAMP,
        active_heartbeat_id = heartbeat_id,
        active_heartbeat_number = hb_number,
        active_actions = '[]'::jsonb,
        active_reasoning = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    context := gather_turn_context();
    decision_max_tokens := COALESCE(get_config_int('heartbeat.max_decision_tokens'), 2048);
    external_calls := jsonb_build_array(
        build_external_call(
            'think',
            jsonb_build_object(
                'kind', 'heartbeat_decision',
                'context', context,
                'heartbeat_id', heartbeat_id,
                'max_tokens', decision_max_tokens
            )
        )
    );

    RETURN jsonb_build_object(
        'heartbeat_id', heartbeat_id,
        'heartbeat_number', hb_number,
        'external_calls', external_calls,
        'outbox_messages', '[]'::jsonb
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION run_heartbeat()
RETURNS JSONB AS $$
DECLARE
    hb_payload JSONB;
BEGIN
    IF NOT should_run_heartbeat() THEN
        RETURN NULL;
    END IF;
    hb_payload := start_heartbeat();

    RETURN hb_payload;
END;
$$ LANGUAGE plpgsql;

SET check_function_bodies = on;
