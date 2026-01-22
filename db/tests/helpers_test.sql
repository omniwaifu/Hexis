CREATE EXTENSION IF NOT EXISTS pgtap;

BEGIN;
SELECT plan(3);

SELECT ok(age_in_days(NOW() - interval '1 day') >= 0.9, 'age_in_days returns ~1 day');
SELECT ok(
    calculate_relevance(1.0, 0.1, NOW() - interval '1 day', NOW()) > 0,
    'calculate_relevance returns positive score'
);
SELECT ok(
    calculate_relevance(1.0, 0.1, NOW() - interval '10 days', NOW()) <
    calculate_relevance(1.0, 0.1, NOW() - interval '1 day', NOW()),
    'calculate_relevance decays with age'
);

SELECT * FROM finish();
ROLLBACK;
