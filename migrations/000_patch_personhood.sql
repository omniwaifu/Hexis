-- Patch migration: keep persistent dev/test DBs aligned with schema.sql
-- This file must be safe to re-run (CREATE OR REPLACE only).

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

DO $$
BEGIN
    BEGIN PERFORM create_vlabel('memory_graph', 'SelfNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'LifeChapterNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'TurningPointNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'NarrativeThreadNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'RelationshipNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM create_vlabel('memory_graph', 'ValueConflictNode'); EXCEPTION WHEN OTHERS THEN NULL; END;
END;
$$;

SET search_path = public, ag_catalog, "$user";

CREATE OR REPLACE FUNCTION ensure_self_node()
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    BEGIN
        EXECUTE format(
            'SELECT * FROM cypher(''memory_graph'', $q$
                MERGE (s:SelfNode {key: ''self''})
                SET s.name = ''Self'',
                    s.created_at = %L
                RETURN s
            $q$) as (result agtype)',
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;

    PERFORM set_config('agent.self', jsonb_build_object('key', 'self'));
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION ensure_current_life_chapter(p_name TEXT DEFAULT 'Foundations')
RETURNS VOID AS $$
DECLARE
    now_text TEXT := clock_timestamp()::text;
BEGIN
    PERFORM ensure_self_node();

    BEGIN
        EXECUTE format(
            'SELECT * FROM cypher(''memory_graph'', $q$
                MERGE (c:LifeChapterNode {key: ''current''})
                SET c.name = %L,
                    c.started_at = %L
                WITH c
                MATCH (s:SelfNode {key: ''self''})
                OPTIONAL MATCH (s)-[r:ASSOCIATED]->(c)
                WHERE r.kind = ''life_chapter_current''
                DELETE r
                CREATE (s)-[r2:ASSOCIATED]->(c)
                SET r2.kind = ''life_chapter_current'',
                    r2.strength = 1.0,
                    r2.updated_at = %L
                RETURN c
            $q$) as (result agtype)',
            COALESCE(NULLIF(p_name, ''), 'Foundations'),
            now_text,
            now_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION upsert_self_concept_edge(
    p_kind TEXT,
    p_concept TEXT,
    p_strength FLOAT DEFAULT 0.8,
    p_evidence_memory_id UUID DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    evidence_text TEXT;
    now_text TEXT := clock_timestamp()::text;
BEGIN
    IF p_kind IS NULL OR btrim(p_kind) = '' OR p_concept IS NULL OR btrim(p_concept) = '' THEN
        RETURN;
    END IF;

    PERFORM ensure_self_node();
    evidence_text := CASE WHEN p_evidence_memory_id IS NULL THEN NULL ELSE p_evidence_memory_id::text END;

    BEGIN
        EXECUTE format(
            'SELECT * FROM cypher(''memory_graph'', $q$
                MATCH (s:SelfNode {key: ''self''})
                MERGE (c:ConceptNode {name: %L})
                CREATE (s)-[r:ASSOCIATED]->(c)
                SET r.kind = %L,
                    r.strength = %s,
                    r.updated_at = %L,
                    r.evidence_memory_id = %L
                RETURN r
            $q$) as (result agtype)',
            p_concept,
            p_kind,
            LEAST(1.0, GREATEST(0.0, COALESCE(p_strength, 0.8))),
            now_text,
            evidence_text
        );
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_self_model_context(p_limit INT DEFAULT 25)
RETURNS JSONB AS $$
DECLARE
    lim INT := GREATEST(0, LEAST(200, COALESCE(p_limit, 25)));
    sql TEXT;
    out_json JSONB;
BEGIN
    sql := format($sql$
        WITH hits AS (
            SELECT
                NULLIF(replace(kind_raw::text, '"', ''), 'null') as kind,
                NULLIF(replace(concept_raw::text, '"', ''), 'null') as concept,
                NULLIF(replace(evidence_raw::text, '"', ''), 'null') as evidence_memory_id,
                NULLIF(strength_raw::text, 'null')::float as strength
            FROM cypher('memory_graph', $q$
                MATCH (s:SelfNode {key: 'self'})-[r:ASSOCIATED]->(c)
                WHERE r.kind IS NOT NULL
                RETURN r.kind, c.name, r.strength, r.evidence_memory_id
                LIMIT %s
            $q$) as (kind_raw agtype, concept_raw agtype, strength_raw agtype, evidence_raw agtype)
        )
        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'kind', kind,
                'concept', concept,
                'strength', COALESCE(strength, 0.0),
                'evidence_memory_id', evidence_memory_id
            )
        ), '[]'::jsonb)
        FROM hits
    $sql$, lim);

    EXECUTE sql INTO out_json;
    RETURN COALESCE(out_json, '[]'::jsonb);
EXCEPTION
    WHEN OTHERS THEN
        RETURN '[]'::jsonb;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION get_narrative_context()
RETURNS JSONB AS $$
BEGIN
    RETURN COALESCE((
        WITH cur AS (
            SELECT
                NULLIF(replace(name_raw::text, '"', ''), 'null') as name
            FROM cypher('memory_graph', $q$
                MATCH (c:LifeChapterNode {key: 'current'})
                RETURN c.name
                LIMIT 1
            $q$) as (name_raw agtype)
        )
        SELECT jsonb_build_object(
            'current_chapter', COALESCE((SELECT jsonb_build_object('name', name) FROM cur), '{}'::jsonb)
        )
    ), jsonb_build_object('current_chapter', '{}'::jsonb));
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('current_chapter', '{}'::jsonb);
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION gather_turn_context()
RETURNS JSONB AS $$
DECLARE
    state_record RECORD;
    action_costs JSONB;
BEGIN
    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;

    SELECT jsonb_object_agg(
        regexp_replace(key, '^cost_', ''),
        value
    ) INTO action_costs
    FROM heartbeat_config
    WHERE key LIKE 'cost_%';

    RETURN jsonb_build_object(
        'agent', get_agent_profile_context(),
        'environment', get_environment_snapshot(),
        'goals', get_goals_snapshot(),
        'recent_memories', get_recent_context(5),
        'identity', get_identity_context(),
        'worldview', get_worldview_context(),
        'self_model', get_self_model_context(25),
        'narrative', get_narrative_context(),
        'energy', jsonb_build_object(
            'current', state_record.current_energy,
            'max', (SELECT value FROM heartbeat_config WHERE key = 'max_energy')
        ),
        'action_costs', action_costs,
        'heartbeat_number', state_record.heartbeat_count,
        'urgent_drives', (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'level', current_level,
                        'urgency_ratio', current_level / NULLIF(urgency_threshold, 0)
                    )
                    ORDER BY current_level DESC
                ),
                '[]'::jsonb
            )
            FROM drives
            WHERE current_level >= urgency_threshold * 0.8
        ),
        'emotional_state', (
            SELECT row_to_json(e)
            FROM current_emotional_state e
        )
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION start_heartbeat()
RETURNS UUID AS $$
DECLARE
    log_id UUID;
    state_record RECORD;
    base_regen FLOAT;
    max_energy FLOAT;
    new_energy FLOAT;
    context JSONB;
    hb_number INT;
BEGIN
    IF NOT is_agent_configured() THEN
        RETURN NULL;
    END IF;

    PERFORM ensure_self_node();
    PERFORM ensure_current_life_chapter();

    SELECT * INTO state_record FROM heartbeat_state WHERE id = 1;
    SELECT value INTO base_regen FROM heartbeat_config WHERE key = 'base_regeneration';
    SELECT value INTO max_energy FROM heartbeat_config WHERE key = 'max_energy';

    new_energy := LEAST(state_record.current_energy + base_regen, max_energy);
    hb_number := state_record.heartbeat_count + 1;

    PERFORM update_drives();

    UPDATE heartbeat_state SET
        current_energy = new_energy,
        heartbeat_count = hb_number,
        last_heartbeat_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;

    context := gather_turn_context();

    INSERT INTO heartbeat_log (
        heartbeat_number,
        energy_start,
        environment_snapshot,
        goals_snapshot
    ) VALUES (
        hb_number,
        new_energy,
        context->'environment',
        context->'goals'
    )
    RETURNING id INTO log_id;

    INSERT INTO external_calls (call_type, input, heartbeat_id)
    VALUES ('think', jsonb_build_object(
        'kind', 'heartbeat_decision',
        'context', context,
        'heartbeat_id', log_id
    ), log_id);

    RETURN log_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION process_reflection_result(
    p_heartbeat_id UUID,
    p_result JSONB
)
RETURNS VOID AS $$
DECLARE
    insight JSONB;
    ident JSONB;
    wupd JSONB;
    rel JSONB;
    contra JSONB;
    selfupd JSONB;
    content TEXT;
    conf FLOAT;
    category TEXT;
    aspect_type TEXT;
    change_text TEXT;
    reason_text TEXT;
    wid UUID;
    new_conf FLOAT;
    from_id UUID;
    to_id UUID;
    rel_type graph_edge_type;
    rel_conf FLOAT;
    ma UUID;
    mb UUID;
    sm_kind TEXT;
    sm_concept TEXT;
    sm_strength FLOAT;
    sm_evidence UUID;
BEGIN
    IF p_result IS NULL THEN
        RETURN;
    END IF;

    IF p_result ? 'insights' THEN
        FOR insight IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'insights', '[]'::jsonb))
        LOOP
            content := COALESCE(insight->>'content', '');
            IF content <> '' THEN
                conf := COALESCE((insight->>'confidence')::float, 0.7);
                category := COALESCE(insight->>'category', 'pattern');
                PERFORM create_semantic_memory(
                    content,
                    conf,
                    ARRAY['reflection', category],
                    NULL,
                    jsonb_build_object('heartbeat_id', p_heartbeat_id, 'source', 'reflect'),
                    0.6
                );
            END IF;
        END LOOP;
    END IF;

    IF p_result ? 'identity_updates' THEN
        FOR ident IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'identity_updates', '[]'::jsonb))
        LOOP
            aspect_type := COALESCE(ident->>'aspect_type', '');
            change_text := COALESCE(ident->>'change', '');
            reason_text := COALESCE(ident->>'reason', '');
            IF aspect_type <> '' AND change_text <> '' THEN
                INSERT INTO identity_aspects (aspect_type, content, stability)
                VALUES (
                    aspect_type,
                    jsonb_build_object('change', change_text, 'reason', reason_text, 'heartbeat_id', p_heartbeat_id),
                    0.5
                );
            END IF;
        END LOOP;
    END IF;

    IF p_result ? 'worldview_updates' THEN
        FOR wupd IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'worldview_updates', '[]'::jsonb))
        LOOP
            wid := NULLIF(wupd->>'id', '')::uuid;
            new_conf := COALESCE(NULLIF(wupd->>'new_confidence', '')::float, 0.7);
            reason_text := COALESCE(wupd->>'reason', '');
            IF wid IS NOT NULL THEN
                UPDATE worldview_primitives
                SET confidence = LEAST(1.0, GREATEST(0.0, new_conf)),
                    updated_at = CURRENT_TIMESTAMP,
                    evidence = COALESCE(evidence, '{}'::jsonb) || jsonb_build_object('reflection_reason', reason_text)
                WHERE id = wid;
            END IF;
        END LOOP;
    END IF;

    IF p_result ? 'discovered_relationships' THEN
        FOR rel IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'discovered_relationships', '[]'::jsonb))
        LOOP
            from_id := NULLIF(rel->>'from_id', '')::uuid;
            to_id := NULLIF(rel->>'to_id', '')::uuid;
            rel_type := COALESCE(NULLIF(rel->>'type', ''), 'ASSOCIATED')::graph_edge_type;
            rel_conf := COALESCE(NULLIF(rel->>'confidence', '')::float, 0.8);
            IF from_id IS NOT NULL AND to_id IS NOT NULL THEN
                PERFORM discover_relationship(from_id, to_id, rel_type, rel_conf, 'reflection', p_heartbeat_id, 'reflect');
            END IF;
        END LOOP;
    END IF;

    IF p_result ? 'contradictions_noted' THEN
        FOR contra IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'contradictions_noted', '[]'::jsonb))
        LOOP
            ma := NULLIF(contra->>'memory_a', '')::uuid;
            mb := NULLIF(contra->>'memory_b', '')::uuid;
            IF ma IS NOT NULL AND mb IS NOT NULL THEN
                PERFORM create_memory_relationship(ma, mb, 'CONTRADICTS', jsonb_build_object('by', 'reflect', 'heartbeat_id', p_heartbeat_id));
            END IF;
        END LOOP;
    END IF;

    IF p_result ? 'self_updates' THEN
        FOR selfupd IN SELECT * FROM jsonb_array_elements(COALESCE(p_result->'self_updates', '[]'::jsonb))
        LOOP
            sm_kind := NULLIF(COALESCE(selfupd->>'kind', ''), '');
            sm_concept := NULLIF(COALESCE(selfupd->>'concept', ''), '');
            sm_strength := COALESCE(NULLIF(selfupd->>'strength', '')::float, 0.8);

            sm_evidence := NULL;
            BEGIN
                IF NULLIF(COALESCE(selfupd->>'evidence_memory_id', ''), '') IS NOT NULL THEN
                    sm_evidence := (selfupd->>'evidence_memory_id')::uuid;
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    sm_evidence := NULL;
            END;

            PERFORM upsert_self_concept_edge(sm_kind, sm_concept, sm_strength, sm_evidence);
        END LOOP;
    END IF;
END;
$$ LANGUAGE plpgsql;
