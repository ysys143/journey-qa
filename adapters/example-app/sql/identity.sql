-- example-app identity assertions for gates/sql_assert.py (--roster adapters/example-app/roster.json).
-- Every block must return zero rows. roster_identity(kind, value) is injected by the runner:
--   handle, email, login_email, account_id, provider_account ('provider|account_id'), privileged_handle (roles in roster privileged_roles).
-- Identity keys in example-app have no foreign keys (string joins), so each table is checked on its own.
-- Extend this list from the schema catalog, not by hand: a hand-written table list tends to miss
-- tables that carry identity columns (JSON attrs, archive queues and rollup grouping keys can
-- hold copies). Query information_schema for columns named *email*, *user_id*, *account_id*
-- and JSON columns before trusting this file.

-- assert: app_users.email_not_in_roster
SELECT email FROM app_users
WHERE email NOT IN (SELECT value FROM roster_identity WHERE kind = 'email');

-- assert: app_users.user_id_not_in_roster
SELECT app_user_id FROM app_users
WHERE app_user_id <> '' AND app_user_id NOT IN (SELECT value FROM roster_identity WHERE kind = 'handle');

-- assert: app_users.admins_differ_from_roster
SELECT app_user_id FROM app_users
WHERE role = 'admin' AND app_user_id NOT IN (SELECT value FROM roster_identity WHERE kind = 'privileged_handle')
UNION ALL
SELECT value FROM roster_identity
WHERE kind = 'privileged_handle' AND value NOT IN (SELECT app_user_id FROM app_users WHERE role = 'admin');

-- assert: app_users.count_differs_from_roster
SELECT n FROM (SELECT count(*) AS n FROM app_users) c
WHERE n <> (SELECT count(*) FROM roster_identity WHERE kind = 'handle');

-- assert: app_sessions.user_id_not_in_roster
SELECT DISTINCT user_id FROM app_sessions
WHERE user_id <> '' AND user_id NOT IN (SELECT value FROM roster_identity WHERE kind = 'handle');

-- assert: app_sessions.login_email_not_in_roster
SELECT DISTINCT login_email FROM app_sessions
WHERE login_email <> '' AND login_email NOT IN (SELECT value FROM roster_identity WHERE kind = 'login_email');

-- assert: app_sessions.profile_email_not_in_roster
-- profile_email is whatever the client reports as signed in; it has no FK, so it can hold any value.
SELECT DISTINCT profile_email FROM app_sessions
WHERE profile_email <> '' AND profile_email NOT IN (SELECT value FROM roster_identity WHERE kind IN ('email', 'login_email'));

-- assert: app_sessions.provider_account_not_in_roster
SELECT DISTINCT provider, account_id FROM app_sessions
WHERE account_id <> ''
  AND (provider || '|' || account_id) NOT IN (SELECT value FROM roster_identity WHERE kind = 'provider_account');

-- assert: app_sessions.login_email_to_account_not_1to1_per_provider
SELECT login_email, provider FROM app_sessions
WHERE login_email <> '' AND account_id <> ''
GROUP BY login_email, provider
HAVING count(DISTINCT account_id) > 1;

-- assert: app_usage_samples.provider_account_not_in_roster
SELECT DISTINCT provider, account_id FROM app_usage_samples
WHERE (provider || '|' || account_id) NOT IN (SELECT value FROM roster_identity WHERE kind = 'provider_account');

-- assert: app_usage_samples.email_not_in_roster
SELECT DISTINCT login_email FROM app_usage_samples
WHERE (login_email <> '' AND login_email NOT IN (SELECT value FROM roster_identity WHERE kind = 'login_email'))
   OR (coalesce(profile_email, '') <> '' AND profile_email NOT IN (SELECT value FROM roster_identity WHERE kind IN ('email', 'login_email')));
