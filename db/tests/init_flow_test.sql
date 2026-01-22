CREATE EXTENSION IF NOT EXISTS pgtap;

BEGIN;
SELECT plan(10);

SELECT init_llm_config(
    '{"provider":"openai","model":"gpt-5"}'::jsonb,
    '{"provider":"openai","model":"gpt-5"}'::jsonb
);
SELECT is(get_config('llm.heartbeat') ->> 'provider', 'openai', 'init_llm_config sets heartbeat provider');
SELECT is(get_init_status()->>'stage', 'llm', 'init_llm_config advances stage');

SELECT init_mode('raw');
SELECT is(get_config_text('agent.mode'), 'raw', 'init_mode stores agent mode');
SELECT is(get_init_status()->>'stage', 'mode', 'init_mode advances stage');

SELECT init_heartbeat_settings(
    30,
    512,
    12,
    25,
    '["observe","invalid_action"]'::jsonb,
    '{"observe":1.5,"invalid_action":9}'::jsonb,
    '["search"]'::jsonb
);
SELECT is(get_config_int('heartbeat.heartbeat_interval_minutes'), 30, 'init_heartbeat_settings sets interval');
SELECT is(get_config_int('heartbeat.max_decision_tokens'), 512, 'init_heartbeat_settings sets decision tokens');
SELECT ok(get_config('heartbeat.allowed_actions') @> '["observe"]'::jsonb, 'allowed actions filtered');
SELECT is(get_action_cost('observe'), 1.5, 'action cost stored');
SELECT ok(get_config('agent.tools') @> '["search"]'::jsonb, 'agent tools stored');
SELECT is(get_init_status()->>'stage', 'heartbeat', 'init_heartbeat_settings advances stage');

SELECT * FROM finish();
ROLLBACK;
