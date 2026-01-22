-- Hexis schema: context gathering functions.
SET search_path = public, ag_catalog, "$user";
SET check_function_bodies = off;

CREATE OR REPLACE FUNCTION get_environment_snapshot()
RETURNS JSONB AS $$
DECLARE
    last_user TIMESTAMPTZ;
BEGIN
    SELECT last_user_contact INTO last_user FROM heartbeat_state WHERE id = 1;

    RETURN jsonb_build_object(
        'timestamp', CURRENT_TIMESTAMP,
        'time_since_user_hours', CASE
            WHEN last_user IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_user)) / 3600
        END,
        'pending_events', 0,
        'day_of_week', EXTRACT(DOW FROM CURRENT_TIMESTAMP),
        'hour_of_day', EXTRACT(HOUR FROM CURRENT_TIMESTAMP)
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_goals_snapshot()
RETURNS JSONB AS $$
DECLARE
    active_goals JSONB;
    queued_goals JSONB;
    issues JSONB;
    stale_days FLOAT;
BEGIN
    stale_days := get_config_float('heartbeat.goal_stale_days');
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id,
        'title', metadata->>'title',
        'description', metadata->>'description',
        'due_at', (metadata->>'due_at')::timestamptz,
        'last_touched', (metadata->>'last_touched')::timestamptz,
        'progress_count', jsonb_array_length(COALESCE(metadata->'progress', '[]'::jsonb)),
        'blocked_by', metadata->'blocked_by'
    )), '[]'::jsonb)
    INTO active_goals
    FROM memories
    WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active';
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'id', id,
        'title', metadata->>'title',
        'source', metadata->>'source',
        'due_at', (metadata->>'due_at')::timestamptz
    )), '[]'::jsonb)
    INTO queued_goals
    FROM (
        SELECT * FROM memories
        WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'queued'
        ORDER BY (metadata->>'due_at')::timestamptz NULLS LAST, (metadata->>'last_touched')::timestamptz DESC
        LIMIT 5
    ) q;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'goal_id', id,
        'title', metadata->>'title',
        'issue', CASE
            WHEN metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb THEN 'blocked'
            WHEN (metadata->>'due_at')::timestamptz IS NOT NULL AND (metadata->>'due_at')::timestamptz < CURRENT_TIMESTAMP THEN 'overdue'
            WHEN (metadata->>'last_touched')::timestamptz < CURRENT_TIMESTAMP - (stale_days || ' days')::INTERVAL THEN 'stale'
            ELSE 'unknown'
        END,
        'due_at', (metadata->>'due_at')::timestamptz,
        'days_since_touched', EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - (metadata->>'last_touched')::timestamptz)) / 86400
    )), '[]'::jsonb)
    INTO issues
    FROM memories
    WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'
    AND (
        (metadata->'blocked_by' IS NOT NULL AND metadata->'blocked_by' <> 'null'::jsonb)
        OR ((metadata->>'due_at')::timestamptz IS NOT NULL AND (metadata->>'due_at')::timestamptz < CURRENT_TIMESTAMP)
        OR (metadata->>'last_touched')::timestamptz < CURRENT_TIMESTAMP - (stale_days || ' days')::INTERVAL
    );

    RETURN jsonb_build_object(
        'active', active_goals,
        'queued', queued_goals,
        'issues', issues,
        'counts', jsonb_build_object(
            'active', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'active'),
            'queued', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'queued'),
            'backburner', (SELECT COUNT(*) FROM memories WHERE type = 'goal' AND status = 'active' AND metadata->>'priority' = 'backburner')
        )
    );
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_goals_by_priority(
    p_priority goal_priority DEFAULT NULL
) RETURNS TABLE (
    id UUID,
    title TEXT,
    description TEXT,
    priority TEXT,
    source TEXT,
    due_at TIMESTAMPTZ,
    last_touched TIMESTAMPTZ,
    progress JSONB,
    blocked_by JSONB,
    emotional_valence FLOAT,
    created_at TIMESTAMPTZ
) AS $$
BEGIN
    IF p_priority IS NULL THEN
        RETURN QUERY
        SELECT
            m.id,
            m.metadata->>'title' as title,
            m.metadata->>'description' as description,
            m.metadata->>'priority' as priority,
            m.metadata->>'source' as source,
            (m.metadata->>'due_at')::timestamptz as due_at,
            (m.metadata->>'last_touched')::timestamptz as last_touched,
            m.metadata->'progress' as progress,
            m.metadata->'blocked_by' as blocked_by,
            (m.metadata->>'emotional_valence')::float as emotional_valence,
            m.created_at
        FROM memories m
        WHERE m.type = 'goal'
          AND m.status = 'active'
          AND m.metadata->>'priority' IN ('active', 'queued')
        ORDER BY m.metadata->>'priority', (m.metadata->>'last_touched')::timestamptz DESC;
    ELSE
        RETURN QUERY
        SELECT
            m.id,
            m.metadata->>'title' as title,
            m.metadata->>'description' as description,
            m.metadata->>'priority' as priority,
            m.metadata->>'source' as source,
            (m.metadata->>'due_at')::timestamptz as due_at,
            (m.metadata->>'last_touched')::timestamptz as last_touched,
            m.metadata->'progress' as progress,
            m.metadata->'blocked_by' as blocked_by,
            (m.metadata->>'emotional_valence')::float as emotional_valence,
            m.created_at
        FROM memories m
        WHERE m.type = 'goal'
          AND m.status = 'active'
          AND m.metadata->>'priority' = p_priority::text
        ORDER BY (m.metadata->>'last_touched')::timestamptz DESC;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_recent_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(sub.obj)
        FROM (
            SELECT jsonb_build_object(
                'id', m.id,
                'content', m.content,
                'created_at', m.created_at,
                'emotional_valence', (m.metadata->>'emotional_valence')::float,
                'trust_level', m.trust_level,
                'source_attribution', m.source_attribution
            ) as obj
            FROM memories m
            WHERE m.type = 'episodic' AND m.status = 'active'
            ORDER BY m.created_at DESC
            LIMIT p_limit
        ) sub
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_identity_context()
RETURNS JSONB AS $$
DECLARE
    result JSONB := '[]'::jsonb;
BEGIN
    BEGIN
        SELECT COALESCE(jsonb_agg(sub.obj), '[]'::jsonb)
        INTO result
        FROM (
            SELECT jsonb_build_object(
                'type', replace(kind::text, '"', ''),
                'concept', replace(concept::text, '"', ''),
                'strength', (strength::text)::float
            ) as obj
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (s:SelfNode)-[r]->(c)
                WHERE type(r) IN ['CAPABLE_OF', 'VALUES', 'STRUGGLES_WITH', 'ASSOCIATED']
                RETURN type(r) as kind, c.name as concept, r.strength as strength
                ORDER BY r.strength DESC
                LIMIT 10
            $q$) as (kind ag_catalog.agtype, concept ag_catalog.agtype, strength ag_catalog.agtype)
        ) sub;
    EXCEPTION WHEN OTHERS THEN result := '[]'::jsonb; END;

    RETURN result;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_worldview_context()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(sub.obj)
        FROM (
            SELECT jsonb_build_object(
                'category', metadata->>'category',
                'belief', content,
                'confidence', (metadata->>'confidence')::float,
                'stability', (metadata->>'stability')::float
            ) as obj
            FROM memories
            WHERE type = 'worldview'
              AND status = 'active'
              AND (metadata->>'confidence')::float > 0.5
            ORDER BY (metadata->>'confidence')::float DESC, importance DESC
            LIMIT 5
        ) sub
    ), '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_worldview_snapshot(
    p_limit INT DEFAULT 5,
    p_min_confidence FLOAT DEFAULT 0.5
) RETURNS TABLE (
    content TEXT,
    category TEXT,
    confidence FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.content,
        m.metadata->>'category' as category,
        (m.metadata->>'confidence')::float as confidence
    FROM memories m
    WHERE m.type = 'worldview'
      AND m.status = 'active'
      AND COALESCE((m.metadata->>'confidence')::float, 0.0) > COALESCE(p_min_confidence, 0.5)
    ORDER BY (m.metadata->>'confidence')::float DESC, m.importance DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_emotional_patterns_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
BEGIN
    RETURN COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
            'memory_id', m.id,
            'pattern', m.metadata->'supporting_evidence'->>'pattern',
            'frequency', COALESCE((m.metadata->'supporting_evidence'->>'frequency')::int, 0),
            'unprocessed', COALESCE((m.metadata->'supporting_evidence'->>'unprocessed')::boolean, false),
            'summary', m.content
        ))
        FROM (
            SELECT id, content, metadata
            FROM memories
            WHERE type = 'strategic'
              AND metadata->'supporting_evidence'->>'kind' = 'emotional_pattern'
            ORDER BY created_at DESC
            LIMIT lim
        ) m
    ), '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_subconscious_context(
    p_recent_limit INT DEFAULT 20,
    p_self_limit INT DEFAULT 25,
    p_relationship_limit INT DEFAULT 15,
    p_contradiction_limit INT DEFAULT 5,
    p_emotional_pattern_limit INT DEFAULT 5,
    p_trigger_limit INT DEFAULT 5,
    p_trigger_min_similarity FLOAT DEFAULT 0.75
)
RETURNS JSONB AS $$
DECLARE
    recent JSONB;
    seed TEXT;
    emotional_triggers JSONB := '[]'::jsonb;
BEGIN
    recent := COALESCE(get_recent_context(p_recent_limit), '[]'::jsonb);

    SELECT string_agg(content, ' ')
    INTO seed
    FROM (
        SELECT NULLIF(value->>'content', '') as content
        FROM jsonb_array_elements(recent) value
        WHERE value ? 'content'
        LIMIT 5
    ) sub
    WHERE content IS NOT NULL;

    IF COALESCE(p_trigger_limit, 0) > 0 AND seed IS NOT NULL AND seed <> '' THEN
        emotional_triggers := match_emotional_triggers(seed, p_trigger_limit, p_trigger_min_similarity);
    END IF;

    RETURN jsonb_build_object(
        'recent_memories', recent,
        'narrative', get_narrative_context(),
        'self_model', get_self_model_context(p_self_limit),
        'relationships', get_relationships_context(p_relationship_limit),
        'worldview', get_worldview_context(),
        'contradictions', get_contradictions_context(p_contradiction_limit),
        'emotional_patterns', get_emotional_patterns_context(p_emotional_pattern_limit),
        'active_transformations', get_active_transformations_context(5),
        'transformations_ready', check_transformation_readiness(),
        'emotional_state', get_current_affective_state(),
        'emotional_triggers', COALESCE(emotional_triggers, '[]'::jsonb),
        'goals', get_goals_snapshot()
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'recent_memories', recent,
            'emotional_state', get_current_affective_state(),
            'emotional_triggers', '[]'::jsonb
        );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_subconscious_chat_context(
    p_query TEXT,
    p_limit INT DEFAULT 12
)
RETURNS JSONB AS $$
DECLARE
    recall JSONB;
BEGIN
    IF p_query IS NULL OR btrim(p_query) = '' THEN
        recall := '[]'::jsonb;
    ELSE
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_id', memory_id,
            'content', content,
            'type', memory_type,
            'score', score,
            'source', source
        )), '[]'::jsonb)
        INTO recall
        FROM fast_recall(p_query, p_limit);
    END IF;

    RETURN jsonb_build_object(
        'prompt', COALESCE(p_query, ''),
        'relevant_memories', recall,
        'emotional_state', get_current_affective_state(),
        'relationships', get_relationships_context(8),
        'goals', get_goals_snapshot()
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION get_chat_context(
    p_query TEXT,
    p_limit INT DEFAULT 8
)
RETURNS JSONB AS $$
DECLARE
    recall JSONB;
BEGIN
    IF p_query IS NULL OR btrim(p_query) = '' THEN
        recall := '[]'::jsonb;
    ELSE
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_id', memory_id,
            'content', content,
            'type', memory_type,
            'score', score,
            'source', source
        )), '[]'::jsonb)
        INTO recall
        FROM fast_recall(p_query, p_limit);
    END IF;

    RETURN jsonb_build_object(
        'agent', get_agent_profile_context(),
        'profile', get_init_profile(),
        'goals', get_goals_snapshot(),
        'identity', get_identity_context(),
        'worldview', get_worldview_context(),
        'relationships', get_relationships_context(10),
        'emotional_state', get_current_affective_state(),
        'relevant_memories', recall
    );
END;
$$ LANGUAGE plpgsql STABLE;
CREATE OR REPLACE FUNCTION record_subconscious_exchange(
    p_prompt TEXT,
    p_response JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    valence FLOAT;
    content TEXT;
    memory_id UUID;
BEGIN
    BEGIN
        valence := NULLIF(COALESCE(p_response#>>'{emotional_state,valence}', ''), '')::float;
    EXCEPTION
        WHEN OTHERS THEN
            valence := 0.0;
    END;
    content := format(
        'Prompt: %s%sSubconscious: %s',
        LEFT(COALESCE(p_prompt, ''), 1000),
        E'\n\n',
        LEFT(COALESCE(p_response::text, '{}'), 2000)
    );

    memory_id := create_episodic_memory(
        p_content := content,
        p_action_taken := jsonb_build_object('action', 'subconscious_chat'),
        p_context := jsonb_build_object('prompt', p_prompt),
        p_result := jsonb_build_object('subconscious_response', COALESCE(p_response, '{}'::jsonb)),
        p_emotional_valence := COALESCE(valence, 0.0),
        p_importance := 0.4,
        p_source_attribution := jsonb_build_object('kind', 'subconscious_chat', 'observed_at', CURRENT_TIMESTAMP)
    );

    RETURN memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION record_chat_turn(
    p_user_prompt TEXT,
    p_assistant_response TEXT,
    p_context JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    content TEXT;
    memory_id UUID;
BEGIN
    content := format('User: %s%sAssistant: %s',
        COALESCE(p_user_prompt, ''),
        E'\n\n',
        COALESCE(p_assistant_response, '')
    );

    memory_id := create_episodic_memory(
        p_content := content,
        p_action_taken := jsonb_build_object('action', 'chat_turn'),
        p_context := p_context,
        p_result := NULL,
        p_emotional_valence := 0.0,
        p_importance := 0.6,
        p_source_attribution := jsonb_build_object('kind', 'conversation', 'observed_at', CURRENT_TIMESTAMP)
    );

    RETURN memory_id;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION get_contradictions_context(p_limit INT DEFAULT 5)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(50, COALESCE(p_limit, 5)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH pairs AS (
            SELECT
                replace(a_id::text, '"', '')::uuid as a_uuid,
                replace(b_id::text, '"', '')::uuid as b_uuid
            FROM ag_catalog.cypher('memory_graph', $q$
                MATCH (a:MemoryNode)-[:CONTRADICTS]-(b:MemoryNode)
                RETURN a.memory_id, b.memory_id
                LIMIT %s
            $q$) as (a_id ag_catalog.agtype, b_id ag_catalog.agtype)
        )
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'memory_a', p.a_uuid,
            'memory_b', p.b_uuid,
            'content_a', ma.content,
            'content_b', mb.content
        )), '[]'::jsonb)
        FROM pairs p
        JOIN memories ma ON ma.id = p.a_uuid
        JOIN memories mb ON mb.id = p.b_uuid
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;

SET check_function_bodies = on;
