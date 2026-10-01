-- Remove the temporary onboarding persona (Quinn Sampleton) from instance B.
-- example-app has no delete-user API, so this runs inside the instance's
-- database: API tokens first, then the user row. Matches that persona only and
-- is idempotent; run it by hand if a run died before its teardown.
BEGIN;
WITH q AS (
  SELECT id FROM app_users
  WHERE app_user_id = 'quinnsampleton' OR email = 'quinn.sampleton@example.com'
), t AS (
  DELETE FROM app_api_tokens WHERE app_user_ref IN (SELECT id FROM q) RETURNING 1
)
SELECT 'tokens_deleted', count(*) FROM t;
WITH u AS (
  DELETE FROM app_users
  WHERE app_user_id = 'quinnsampleton' OR email = 'quinn.sampleton@example.com'
  RETURNING 1
)
SELECT 'users_deleted', count(*) FROM u;
COMMIT;
SELECT 'remaining_quinn', count(*) FROM app_users
WHERE app_user_id = 'quinnsampleton' OR email = 'quinn.sampleton@example.com';
SELECT 'users', count(*) FROM app_users;
