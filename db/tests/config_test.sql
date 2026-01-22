CREATE EXTENSION IF NOT EXISTS pgtap;

BEGIN;
SELECT plan(7);

SELECT set_config('test.key', to_jsonb('value'));
SELECT is(get_config_text('test.key'), 'value', 'get_config_text returns string');
SELECT is(get_config('test.key') #>> '{}', 'value', 'get_config returns jsonb');

SELECT set_config('test.count', to_jsonb(42));
SELECT is(get_config_int('test.count'), 42, 'get_config_int returns int');

SELECT set_config('test.ratio', to_jsonb(1.5));
SELECT cmp_ok(get_config_float('test.ratio'), '>=', 1.5, 'get_config_float returns float');

SELECT set_config('alpha.one', to_jsonb('a'));
SELECT set_config('alpha.two', to_jsonb('b'));
SELECT set_config('beta.one', to_jsonb('c'));
SELECT is((SELECT count(*)::int FROM get_config_by_prefixes(ARRAY['alpha.'])), 2, 'get_config_by_prefixes filters');

SELECT ok(delete_config_key('test.key'), 'delete_config_key returns true');
SELECT ok(get_config('test.key') IS NULL, 'deleted key missing');

SELECT * FROM finish();
ROLLBACK;
