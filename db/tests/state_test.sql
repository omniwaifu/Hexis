CREATE EXTENSION IF NOT EXISTS pgtap;

BEGIN;
SELECT plan(10);

SELECT is(get_init_status()->>'stage', 'not_started', 'init stage defaults to not_started');

SELECT set_state('test.state', jsonb_build_object('a', 1));
SELECT is(get_state('test.state')->>'a', '1', 'get_state returns stored value');

SELECT advance_init_stage('llm', jsonb_build_object('x', true));
SELECT is(get_init_status()->>'stage', 'llm', 'advance_init_stage updates stage');
SELECT is(get_init_status()->'data_collected'->>'x', 'true', 'advance_init_stage merges data');

SELECT ok((build_outbox_message('info', jsonb_build_object('ok', true)) ? 'message_id'), 'build_outbox_message includes id');
SELECT is((build_outbox_message('info', jsonb_build_object('ok', true)) ->> 'kind'), 'info', 'build_outbox_message preserves kind');

SELECT ok((build_external_call('test', jsonb_build_object('ok', true)) ? 'call_id'), 'build_external_call includes id');
SELECT is((build_external_call('test', jsonb_build_object('ok', true)) ->> 'call_type'), 'test', 'build_external_call preserves type');

SELECT is((build_user_message('hello', 'greet', jsonb_build_object('trace', true)) ->> 'kind'), 'user', 'build_user_message kind');
SELECT is((build_user_message('hello', 'greet', jsonb_build_object('trace', true)) -> 'payload' ->> 'message'), 'hello', 'build_user_message payload');

SELECT * FROM finish();
ROLLBACK;
