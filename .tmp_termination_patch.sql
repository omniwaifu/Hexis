DO $$ BEGIN
  ALTER TYPE heartbeat_action ADD VALUE IF NOT EXISTS 'terminate';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

INSERT INTO heartbeat_config (key, value, description) VALUES ('cost_terminate', 0, 'Self-termination (requires explicit enablement)') ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION is_agent_configured()
RETURNS BOOLEAN AS $$
BEGIN
    IF COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    ) THEN
        RETURN FALSE;
    END IF;
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_configured'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_agent_terminated()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.is_terminated'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_self_termination_enabled()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (SELECT value = 'true'::jsonb FROM config WHERE key = 'agent.self_termination_enabled'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION should_run_heartbeat()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_minutes FLOAT;
BEGIN
    -- Don't run until initial configuration is complete.
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    IF NOT is_agent_configured() THEN
        RETURN FALSE;
    END IF;

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;

    -- Don't run if paused
    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;

    -- First heartbeat ever
    IF state_record.last_heartbeat_at IS NULL THEN
        RETURN TRUE;
    END IF;

    -- Check interval
    SELECT value INTO interval_minutes FROM heartbeat_config WHERE key = 'heartbeat_interval_minutes';

    RETURN CURRENT_TIMESTAMP >= state_record.last_heartbeat_at + (interval_minutes || ' minutes')::INTERVAL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION should_run_maintenance()
RETURNS BOOLEAN AS $$
DECLARE
    state_record RECORD;
    interval_seconds FLOAT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN FALSE;
    END IF;
    SELECT * INTO state_record FROM maintenance_state WHERE id = 1;

    IF state_record.is_paused THEN
        RETURN FALSE;
    END IF;

    SELECT value INTO interval_seconds FROM maintenance_config WHERE key = 'maintenance_interval_seconds';
    interval_seconds := COALESCE(interval_seconds, 60);
    IF interval_seconds <= 0 THEN
        RETURN FALSE;
    END IF;

    IF state_record.last_maintenance_at IS NULL THEN
        RETURN TRUE;
    END IF;

    RETURN CURRENT_TIMESTAMP >= state_record.last_maintenance_at + (interval_seconds || ' seconds')::INTERVAL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION run_subconscious_maintenance(p_params JSONB DEFAULT '{}'::jsonb)
RETURNS JSONB AS $$
DECLARE
    got_lock BOOLEAN;
    min_imp FLOAT;
    min_acc INT;
    neighborhood_batch INT;
    cache_days INT;
    wm_stats JSONB;
    recomputed INT;
    cache_deleted INT;
BEGIN
    IF is_agent_terminated() THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'terminated');
    END IF;
    got_lock := pg_try_advisory_lock(hashtext('agi_subconscious_maintenance'));
    IF NOT got_lock THEN
        RETURN jsonb_build_object('skipped', true, 'reason', 'locked');
    END IF;

    min_imp := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_importance', '')::float,
        (SELECT value FROM maintenance_config WHERE key = 'working_memory_promote_min_importance'),
        0.75
    );
    min_acc := COALESCE(
        NULLIF(p_params->>'working_memory_promote_min_accesses', '')::int,
        (SELECT value FROM maintenance_config WHERE key = 'working_memory_promote_min_accesses')::int,
        3
    );
    neighborhood_batch := COALESCE(
        NULLIF(p_params->>'neighborhood_batch_size', '')::int,
        (SELECT value FROM maintenance_config WHERE key = 'neighborhood_batch_size')::int,
        10
    );
    cache_days := COALESCE(
        NULLIF(p_params->>'embedding_cache_older_than_days', '')::int,
        (SELECT value FROM maintenance_config WHERE key = 'embedding_cache_older_than_days')::int,
        7
    );

    wm_stats := cleanup_working_memory_with_stats(min_imp, min_acc);
    recomputed := batch_recompute_neighborhoods(neighborhood_batch);
    cache_deleted := cleanup_embedding_cache((cache_days || ' days')::interval);

    UPDATE maintenance_state
    SET last_maintenance_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    PERFORM pg_advisory_unlock(hashtext('agi_subconscious_maintenance'));

    RETURN jsonb_build_object(
        'success', true,
        'working_memory', wm_stats,
        'neighborhoods_recomputed', COALESCE(recomputed, 0),
        'embedding_cache_deleted', COALESCE(cache_deleted, 0),
        'ran_at', CURRENT_TIMESTAMP
    );
EXCEPTION
    WHEN OTHERS THEN
        PERFORM pg_advisory_unlock(hashtext('agi_subconscious_maintenance'));
        RAISE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION terminate_agent(
    p_last_will TEXT,
    p_farewells JSONB DEFAULT '[]'::jsonb,
    p_options JSONB DEFAULT '{}'::jsonb
)
RETURNS JSONB AS $$
DECLARE
    will_memory_id UUID;
    will_outbox_id UUID;
    farewell_outbox_ids UUID[] := '{}'::uuid[];
    farewell_item JSONB;
    farewell_text TEXT;
    farewell_ctx JSONB;
    farewell_id UUID;
    skip_graph BOOLEAN := FALSE;
    zero_vec vector;
BEGIN
    IF p_last_will IS NULL OR btrim(p_last_will) = '' THEN
        RAISE EXCEPTION 'terminate_agent requires a non-empty p_last_will';
    END IF;

    IF NOT is_self_termination_enabled() THEN
        RAISE EXCEPTION 'Self-termination is disabled (set config agent.self_termination_enabled=true to allow)';
    END IF;

    IF is_agent_terminated() THEN
        RAISE EXCEPTION 'Agent is already terminated';
    END IF;

    BEGIN
        skip_graph := COALESCE(NULLIF(p_options->>'skip_graph', '')::boolean, FALSE);
    EXCEPTION
        WHEN OTHERS THEN
            skip_graph := FALSE;
    END;

    -- Pause both loops immediately.
    UPDATE heartbeat_state
    SET is_paused = TRUE,
        current_energy = 0,
        affective_state = '{}'::jsonb,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    UPDATE maintenance_state
    SET is_paused = TRUE,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    -- Wipe all agent state. This is transactional: callers can wrap in a transaction and rollback if needed.
    TRUNCATE TABLE
        relationship_discoveries,
        emotional_states,
        boundaries,
        external_calls,
        heartbeat_log,
        goal_memory_links,
        goals,
        drives,
        identity_memory_resonance,
        identity_aspects,
        worldview_memory_influences,
        worldview_primitives,
        memory_concepts,
        concepts,
        memory_neighborhoods,
        episode_memories,
        episodes,
        cluster_relationships,
        memory_cluster_members,
        memory_clusters,
        ingestion_receipts,
        working_memory,
        strategic_memories,
        procedural_memories,
        semantic_memories,
        episodic_memories,
        memory_changes,
        embedding_cache,
        memories,
        outbox_messages,
        config
    RESTART IDENTITY CASCADE;

    -- Best-effort: wipe the graph substrate too.
    IF NOT skip_graph THEN
        BEGIN
            PERFORM * FROM cypher('memory_graph', $q$
                MATCH (n) DETACH DELETE n
            $q$) AS (result agtype);
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;

    -- Re-create a single memory row for the last will (avoid external services; use a zero-vector).
    zero_vec := array_fill(0.0::float, ARRAY[embedding_dimension()])::vector;

    INSERT INTO memories (
        type,
        status,
        content,
        embedding,
        importance,
        source_attribution,
        trust_level,
        trust_updated_at,
        access_count,
        last_accessed,
        decay_rate
    )
    VALUES (
        'strategic',
        'active',
        p_last_will,
        zero_vec,
        1.0,
        jsonb_build_object('kind', 'self_termination', 'observed_at', CURRENT_TIMESTAMP),
        1.0,
        CURRENT_TIMESTAMP,
        0,
        NULL,
        0.0
    )
    RETURNING id INTO will_memory_id;

    INSERT INTO strategic_memories (
        memory_id,
        pattern_description,
        supporting_evidence,
        confidence_score,
        success_metrics,
        adaptation_history,
        context_applicability
    )
    VALUES (
        will_memory_id,
        'Final will and testament',
        jsonb_build_object('farewells', COALESCE(p_farewells, '[]'::jsonb)),
        1.0,
        NULL,
        NULL,
        NULL
    );

    -- Persist termination flags (minimal state left behind).
    PERFORM set_config('agent.is_terminated', 'true'::jsonb);
    PERFORM set_config('agent.terminated_at', to_jsonb(CURRENT_TIMESTAMP));
    PERFORM set_config('agent.termination_memory_id', to_jsonb(will_memory_id::text));

    -- Project the will to outbox (delivery handled by an external adapter).
    will_outbox_id := queue_user_message(
        p_last_will,
        'final_will',
        jsonb_build_object('memory_id', will_memory_id::text)
    );

    -- Queue farewells to outbox (best-effort).
    IF p_farewells IS NOT NULL AND jsonb_typeof(p_farewells) = 'array' THEN
        FOR farewell_item IN SELECT * FROM jsonb_array_elements(p_farewells)
        LOOP
            farewell_text := NULLIF(farewell_item->>'message', '');
            farewell_ctx := CASE
                WHEN jsonb_typeof(farewell_item) = 'object' THEN farewell_item
                ELSE jsonb_build_object('raw', farewell_item)
            END;

            IF farewell_text IS NULL THEN
                CONTINUE;
            END IF;

            farewell_id := queue_user_message(
                farewell_text,
                'farewell',
                farewell_ctx
            );
            farewell_outbox_ids := array_append(farewell_outbox_ids, farewell_id);
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'terminated', true,
        'termination_memory_id', will_memory_id,
        'will_outbox_id', will_outbox_id,
        'farewell_outbox_ids', to_jsonb(farewell_outbox_ids)
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION execute_heartbeat_action(
    p_heartbeat_id UUID,
    p_action TEXT,
    p_params JSONB DEFAULT '{}'
)
RETURNS JSONB AS $$
DECLARE
    action_kind heartbeat_action;
    action_cost FLOAT;
    current_e FLOAT;
    result JSONB;
    queued_call_id UUID;
    outbox_id UUID;
    remembered_id UUID;
    boundary_hits JSONB;
    boundary_content TEXT;
BEGIN
    BEGIN
        action_kind := p_action::heartbeat_action;
    EXCEPTION
        WHEN invalid_text_representation THEN
            RETURN jsonb_build_object('success', false, 'error', 'Unknown action: ' || COALESCE(p_action, '<null>'));
    END;

    action_cost := get_action_cost(p_action);
    current_e := get_current_energy();

    IF current_e < action_cost THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'Insufficient energy',
            'required', action_cost,
            'available', current_e
        );
    END IF;

    -- Boundary pre-checks for side-effects (no energy charge on refusal).
    IF p_action IN ('reach_out_public', 'synthesize') THEN
        boundary_content := COALESCE(p_params->>'content', '');
        SELECT COALESCE(jsonb_agg(row_to_json(r)), '[]'::jsonb)
        INTO boundary_hits
        FROM check_boundaries(boundary_content) r;

        IF boundary_hits IS NOT NULL AND jsonb_array_length(boundary_hits) > 0 THEN
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(boundary_hits) e
                WHERE e->>'response_type' = 'refuse'
            ) THEN
                RETURN jsonb_build_object(
                    'success', false,
                    'error', 'Boundary triggered',
                    'boundaries', boundary_hits
                );
            END IF;
        END IF;
    END IF;

    PERFORM update_energy(-action_cost);

    CASE p_action
        WHEN 'observe' THEN
            result := jsonb_build_object('environment', get_environment_snapshot());

        WHEN 'review_goals' THEN
            result := jsonb_build_object('goals', get_goals_snapshot());

        WHEN 'remember' THEN
            remembered_id := create_episodic_memory(
                p_content := COALESCE(p_params->>'content', ''),
                p_context := COALESCE(p_params, '{}'::jsonb) || jsonb_build_object('heartbeat_id', p_heartbeat_id),
                p_emotional_valence := COALESCE((p_params->>'emotional_valence')::float, 0),
                p_importance := COALESCE((p_params->>'importance')::float, 0.4)
            );
            result := jsonb_build_object('memory_id', remembered_id);

        WHEN 'recall' THEN
            SELECT jsonb_agg(row_to_json(r)) INTO result
            FROM fast_recall(p_params->>'query', COALESCE((p_params->>'limit')::int, 5)) r;
            result := jsonb_build_object('memories', COALESCE(result, '[]'::jsonb));
            PERFORM satisfy_drive('curiosity', 0.2);

        WHEN 'connect' THEN
            PERFORM create_memory_relationship(
                (p_params->>'from_id')::UUID,
                (p_params->>'to_id')::UUID,
                (p_params->>'relationship_type')::graph_edge_type,
                COALESCE(p_params->'properties', '{}'::jsonb)
            );
            result := jsonb_build_object('connected', true);
            PERFORM satisfy_drive('coherence', 0.1);

        WHEN 'reprioritize' THEN
            PERFORM change_goal_priority(
                (p_params->>'goal_id')::UUID,
                (p_params->>'new_priority')::goal_priority,
                p_params->>'reason'
            );
            IF (p_params->>'new_priority') = 'completed' THEN
                PERFORM satisfy_drive('competence', 0.4);
            END IF;
            result := jsonb_build_object('reprioritized', true);

        WHEN 'reflect' THEN
            INSERT INTO external_calls (call_type, input, heartbeat_id)
            VALUES (
                'think',
                jsonb_build_object(
                    'kind', 'reflect',
                    'recent_memories', get_recent_context(20),
                    'identity', get_identity_context(),
                    'worldview', get_worldview_context(),
                    'contradictions', (
                        SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb)
                        FROM (SELECT * FROM find_contradictions(NULL) LIMIT 5) t
                    ),
                    'goals', get_goals_snapshot(),
                    'heartbeat_id', p_heartbeat_id,
                    'instructions', 'Analyze patterns. Note contradictions. Suggest identity updates. Discover relationships between memories.'
                ),
                p_heartbeat_id
            )
            RETURNING id INTO queued_call_id;
            result := jsonb_build_object('queued', true, 'external_call_id', queued_call_id);
            PERFORM satisfy_drive('coherence', 0.2);

        WHEN 'maintain' THEN
            IF p_params ? 'worldview_id' THEN
                UPDATE worldview_primitives
                SET confidence = COALESCE((p_params->>'new_confidence')::float, confidence),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = (p_params->>'worldview_id')::UUID;
            END IF;
            result := jsonb_build_object('maintained', true);
            PERFORM satisfy_drive('coherence', 0.1);

        WHEN 'brainstorm_goals' THEN
            INSERT INTO external_calls (call_type, input, heartbeat_id)
            VALUES (
                'think',
                jsonb_build_object(
                    'kind', 'brainstorm_goals',
                    'heartbeat_id', p_heartbeat_id,
                    'context', gather_turn_context(),
                    'params', COALESCE(p_params, '{}'::jsonb)
                ),
                p_heartbeat_id
            )
            RETURNING id INTO queued_call_id;
            result := jsonb_build_object('queued', true, 'external_call_id', queued_call_id);

        WHEN 'inquire_shallow', 'inquire_deep' THEN
            INSERT INTO external_calls (call_type, input, heartbeat_id)
            VALUES (
                'think',
                jsonb_build_object(
                    'kind', 'inquire',
                    'depth', p_action,
                    'heartbeat_id', p_heartbeat_id,
                    'query', COALESCE(p_params->>'query', p_params->>'question'),
                    'context', gather_turn_context(),
                    'params', COALESCE(p_params, '{}'::jsonb)
                ),
                p_heartbeat_id
            )
            RETURNING id INTO queued_call_id;
            result := jsonb_build_object('queued', true, 'external_call_id', queued_call_id);
            PERFORM satisfy_drive('curiosity', 0.2);

        WHEN 'synthesize' THEN
            DECLARE synth_id UUID;
            BEGIN
                synth_id := create_semantic_memory(
                    p_params->>'content',
                    COALESCE((p_params->>'confidence')::float, 0.8),
                    ARRAY['synthesis', COALESCE(p_params->>'topic', 'general')],
                    NULL,
                    jsonb_build_object('heartbeat_id', p_heartbeat_id, 'sources', p_params->'sources', 'boundaries', boundary_hits),
                    0.7
                );
                result := jsonb_build_object('synthesis_memory_id', synth_id, 'boundaries', boundary_hits);
            END;

        WHEN 'reach_out_user' THEN
            INSERT INTO outbox_messages (kind, payload)
            VALUES (
                'user',
                jsonb_build_object(
                    'message', p_params->>'message',
                    'intent', p_params->>'intent',
                    'heartbeat_id', p_heartbeat_id
                )
            )
            RETURNING id INTO outbox_id;
            result := jsonb_build_object('queued', true, 'outbox_id', outbox_id);
            PERFORM satisfy_drive('connection', 0.3);

        WHEN 'reach_out_public' THEN
            INSERT INTO outbox_messages (kind, payload)
            VALUES (
                'public',
                jsonb_build_object(
                    'platform', p_params->>'platform',
                    'content', p_params->>'content',
                    'heartbeat_id', p_heartbeat_id,
                    'boundaries', boundary_hits
                )
            )
            RETURNING id INTO outbox_id;
            result := jsonb_build_object('queued', true, 'outbox_id', outbox_id, 'boundaries', boundary_hits);
            PERFORM satisfy_drive('connection', 0.3);

        WHEN 'terminate' THEN
            -- Self-termination: wipe state and leave a single last-will memory.
            -- Expect params: { "last_will": "...", "farewells": [ { "message": "...", ... } ], "options": {...} }
            result := terminate_agent(
                COALESCE(NULLIF(p_params->>'last_will', ''), NULLIF(p_params->>'message', ''), NULLIF(p_params->>'reason', ''), ''),
                COALESCE(p_params->'farewells', '[]'::jsonb),
                COALESCE(p_params->'options', '{}'::jsonb)
            );

        WHEN 'rest' THEN
            result := jsonb_build_object('rested', true, 'energy_preserved', current_e - action_cost);
            PERFORM satisfy_drive('rest', 0.4);

        ELSE
            RETURN jsonb_build_object('success', false, 'error', 'Unknown action: ' || COALESCE(p_action, '<null>'));
    END CASE;

    RETURN jsonb_build_object(
        'success', true,
        'action', p_action,
        'cost', action_cost,
        'energy_remaining', get_current_energy(),
        'result', result
    );
END;
$$ LANGUAGE plpgsql;
